"""Remote half of the fresh-install operator. Invoked over SSH, not installed.

Never print Secret documents, process environments, or raw failed command output.
The caller must obtain scoped approval before invoking any mutation operation.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys

NS = "deployflow-system"
TARGET = "deployflow-test"
TARGET_UID = "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"
PART = "deployflow-platform"
HOST = "deployflow.damibegud.cloud"


def kubectl(*args, value=None):
    result = subprocess.run(["sudo", "-n", "k3s", "kubectl", "--request-timeout=30s", *args],
                            input=json.dumps(value) if value is not None else None,
                            text=True, capture_output=True, timeout=700, check=False)
    if result.returncode:
        # An API error can quote submitted Secret data. Keep it out of CI logs.
        raise ValueError("Kubernetes operation failed; inspect privately on the server")
    return result.stdout


def get(kind, name, namespace=None):
    scope = ["-n", namespace] if namespace else []
    raw = kubectl("get", kind, name, *scope, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw.strip() else None


def owned(value):
    return value["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") == PART


def identity():
    if socket.gethostname().split(".")[0] != "homeserver1":
        raise ValueError("Wrong server")
    target = get("namespace", TARGET)
    if not target or target["metadata"]["uid"] != TARGET_UID:
        raise ValueError("Target namespace identity changed")
    namespace = get("namespace", NS)
    if namespace and not owned(namespace):
        raise ValueError("Platform namespace already exists with different ownership")
    return namespace


def list_objects(kind):
    return json.loads(kubectl("get", kind, "-n", NS, "-o", "json"))["items"]


def query(sql):
    # psql inherits the established database user's local socket credentials.
    return kubectl("exec", "-n", NS, "postgres-0", "--", "psql", "-X", "-U", "deployflow",
                   "-d", "deployflow", "-At", "-v", "ON_ERROR_STOP=1", "-c",
                   "BEGIN READ ONLY; " + sql + "; COMMIT;")


def inspection(namespace):
    print("Server homeserver1; target namespace UID verified; platform namespace:", bool(namespace))
    routes = json.loads(kubectl("get", "ingress", "-A", "-o", "json"))["items"]
    for route in routes:
        if any(rule.get("host") == HOST for rule in route.get("spec", {}).get("rules", [])):
            print("Host owner:", route["metadata"]["namespace"], route["metadata"]["name"], route["metadata"]["uid"])
    if not namespace:
        return
    for kind in ("statefulsets", "deployments", "jobs", "pods"):
        for item in list_objects(kind):
            status = item.get("status", {})
            print(json.dumps({"kind": item["kind"], "name": item["metadata"]["name"],
                              "owned": owned(item), "status": status}, sort_keys=True))
    config = get("configmap", "deployflow-config", NS)
    if config:
        for flag in ("DF_DEPLOYMENT_EXECUTION_ENABLED", "DF_DEPLOYMENT_CLEANUP_ENABLED"):
            print(flag, config.get("data", {}).get(flag, "missing"))
    migrated = get("job", "deployflow-migrate-c3a71d9e4208", NS)
    if migrated and migrated.get("status", {}).get("succeeded") == 1:
        print("Schema:", query("SELECT version_num FROM alembic_version").strip())
        print("Durable work and enabled policies:", query(
            "SELECT 'analysis', count(*) FROM pending_jobs UNION ALL "
            "SELECT 'github', count(*) FROM github_jobs WHERE status NOT IN ('succeeded','failed') UNION ALL "
            "SELECT 'delivery', count(*) FROM delivery_jobs WHERE status NOT IN ('succeeded','failed') UNION ALL "
            "SELECT 'execution', count(*) FROM execution_jobs WHERE status NOT IN ('succeeded','failed') UNION ALL "
            "SELECT 'enabled policies', count(*) FROM deployment_policies WHERE enabled").strip())


def ensure_fresh():
    if list_objects("deployments"):
        raise ValueError("Fresh-install tool refuses an existing platform: audit jobs, backups and upgrade separately")


def check_collisions(items):
    for item in items:
        meta = item["metadata"]
        namespace = meta.get("namespace")
        allowed = namespace == NS or (item["kind"] == "Namespace" and meta["name"] == NS)
        allowed |= namespace == TARGET and item["kind"] == "RoleBinding" and meta["name"].startswith("deployflow-platform-")
        if not allowed or not owned(item):
            raise ValueError("Resource outside reviewed platform scope")
        current = get(item["kind"], meta["name"], namespace)
        if current and not owned(current):
            raise ValueError("Existing resource has different ownership; refusing adoption")


def completed_job(name):
    job = get("job", name, NS)
    if not job or job.get("status", {}).get("succeeded") != 1:
        raise ValueError("Prerequisite setup Job has not completed")


def apply(stage, document, namespace):
    items = document["items"]
    check_collisions(items)
    if stage != "foundation" and not namespace:
        raise ValueError("Foundation is absent")
    if namespace:
        if stage == "workloads":
            # An interrupted initial apply can be retried only with identical bytes.
            expected = {item["metadata"]["name"]: item for item in items}
            for item in list_objects("deployments"):
                previous = item["metadata"].get("annotations", {}).get("kubectl.kubernetes.io/last-applied-configuration", "null")
                if json.loads(previous) != expected.get(item["metadata"]["name"]) or not owned(item):
                    raise ValueError("Existing platform differs; audit backup/jobs before any upgrade")
        else:
            ensure_fresh()
    if stage == "foundation":
        for role in ("reader", "validator", "deployer"):
            if not get("role", "deployflow-" + role, TARGET):
                raise ValueError("Required narrow target Role is absent; do not widen RBAC")
    else:
        for name in ("deployflow-ghcr", "deployflow-postgres", "deployflow-identity-postgres",
                     "deployflow-redis", "deployflow-runtime", "deployflow-github-key",
                     "deployflow-webhook", "deployflow-identity-bootstrap"):
            current = get("secret", name, NS)
            if not current or not owned(current):
                raise ValueError("Required private installation Secret is absent or unowned")
        config = get("configmap", "deployflow-config", NS)
        if not config or any(config.get("data", {}).get(flag) != "false" for flag in
                             ("DF_DEPLOYMENT_EXECUTION_ENABLED", "DF_DEPLOYMENT_CLEANUP_ENABLED")):
            raise ValueError("Fresh-install execution and cleanup flags must remain disabled")
        for name in ("postgres", "identity-postgres", "redis"):
            kubectl("rollout", "status", "statefulset/" + name, "-n", NS, "--timeout=180s")
    if stage == "workloads":
        completed_job("deployflow-migrate-c3a71d9e4208")
        completed_job("deployflow-identity-setup")
        # Refuse startup that could execute restored work, even with deployment disabled.
        result = query("SELECT count(*) FROM (SELECT id FROM applications UNION ALL SELECT run_id AS id FROM pending_jobs) q")
        if result.splitlines() != ["BEGIN", "0", "COMMIT"]:
            raise ValueError("Database contains application/work state; fresh install refused")
    for item in items:
        # No pruning, force conflicts, deletion, token requests or Job recreation.
        kubectl("apply", "-f", "-", value=item)
        print("Applied", item["kind"], item["metadata"]["name"])
    if stage in {"migration", "identity-setup"}:
        kubectl("wait", "--for=condition=complete", "job/" + items[0]["metadata"]["name"], "-n", NS, "--timeout=610s")
    if stage == "workloads":
        for item in items:
            kubectl("rollout", "status", "deployment/" + item["metadata"]["name"], "-n", NS, "--timeout=240s")
        print("Workloads ready; public TLS/login/webhook/application acceptance still requires verification")


def install_secrets(document, namespace):
    if not namespace:
        raise ValueError("Approve foundation first")
    ensure_fresh()
    check_collisions(document["items"])
    for item in document["items"]:
        current = get("secret", item["metadata"]["name"], NS)
        if current:
            if current.get("data") != item.get("data") or current.get("type") != item.get("type"):
                raise ValueError("Existing credential differs; never rotate as part of initial setup")
            print("Existing credential unchanged:", item["metadata"]["name"])
        else:
            kubectl("create", "-f", "-", value=item)
            print("Created credential:", item["metadata"]["name"])


def capture_host():
    old = get("ingress", "deployflow-mockup", "deployflow-mockup")
    if not old or [r.get("host") for r in old["spec"]["rules"]] != [HOST]:
        raise ValueError("Expected mockup route changed; inspect before switching")
    meta = old["metadata"]
    backup = {"apiVersion": old["apiVersion"], "kind": "Ingress", "spec": old["spec"],
              "metadata": {"name": meta["name"], "namespace": meta["namespace"],
                           "annotations": {k: v for k, v in meta.get("annotations", {}).items()
                                           if k != "kubectl.kubernetes.io/last-applied-configuration"},
                           "labels": meta.get("labels", {})}}
    return {"uid": meta["uid"], "resourceVersion": meta["resourceVersion"], "restore": backup}


def switch_host(document, namespace):
    if not namespace or capture_host() != document["previous"]:
        raise ValueError("Hostname owner changed since backup/review")
    check_collisions(document["items"])
    routes = json.loads(kubectl("get", "ingress", "-A", "-o", "json"))["items"]
    for route in routes:
        if route["metadata"]["uid"] != document["previous"]["uid"] and any(
                rule.get("host") == HOST for rule in route.get("spec", {}).get("rules", [])):
            raise ValueError("Another Ingress already claims this hostname")
    for name in ("api", "frontend", "keycloak", "webhook"):
        kubectl("rollout", "status", "deployment/" + name, "-n", NS, "--timeout=180s")
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
        "uid": document["previous"]["uid"], "resourceVersion": document["previous"]["resourceVersion"]}}
    kubectl("delete", "--raw=/apis/networking.k8s.io/v1/namespaces/deployflow-mockup/ingresses/deployflow-mockup",
            "-f", "-", value=options)
    kubectl("wait", "--for=delete", "ingress/deployflow-mockup", "-n", "deployflow-mockup", "--timeout=60s")
    kubectl("apply", "-f", "-", value=document["items"][0])
    print("Hostname route switched; verify certificate, HTTPS API, login and webhook independently")


def restore_host(document):
    backup = document["previous"]["restore"]
    if (backup["kind"] != "Ingress" or backup["metadata"]["namespace"] != "deployflow-mockup"
            or backup["metadata"]["name"] != "deployflow-mockup"
            or [r.get("host") for r in backup["spec"]["rules"]] != [HOST]):
        raise ValueError("Unexpected restore target")
    if get("ingress", "deployflow-mockup", "deployflow-mockup"):
        raise ValueError("Mockup Ingress already exists; inspect rather than overwrite")
    current = get("ingress", "deployflow", NS)
    expected = document["platform_ingress_uid"]
    if current:
        if not owned(current) or current["metadata"]["uid"] != expected:
            raise ValueError("Platform Ingress ownership or UID changed")
        kubectl("delete", "--raw=/apis/networking.k8s.io/v1/namespaces/deployflow-system/ingresses/deployflow",
                "-f", "-", value={"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
                    "uid": expected, "resourceVersion": current["metadata"]["resourceVersion"]}})
        kubectl("wait", "--for=delete", "ingress/deployflow", "-n", NS, "--timeout=60s")
    elif expected != "absent":
        raise ValueError("Expected platform route is missing; obtain an updated restore approval")
    kubectl("create", "-f", "-", value=backup)
    print("Saved mockup Ingress restored; platform workloads/PVCs and both TLS Secrets retained")


def main():
    request = json.load(sys.stdin)
    namespace = identity()
    operation = request["operation"]
    if operation == "inspect":
        inspection(namespace)
        return
    if operation == "capture-host":
        print(json.dumps(capture_host()))
        return
    document = request["document"]
    serialized = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
    if hashlib.sha256(serialized).hexdigest() != request.get("approval"):
        raise ValueError("Exact document approval digest required")
    if operation == "secrets":
        install_secrets(document, namespace)
    elif operation in {"foundation", "migration", "identity-setup", "workloads"}:
        apply(operation, document, namespace)
    elif operation == "ingress":
        switch_host(document, namespace)
    elif operation == "restore-ingress":
        restore_host(document)
    else:
        raise ValueError("Unsupported operation; hostname switch requires separate approval")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("Operation stopped:", type(exc).__name__, str(exc) if isinstance(exc, ValueError) else "inspect privately", file=sys.stderr)
        sys.exit(1)
