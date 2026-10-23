"""Shared image-only operator: explicit SSH or separately installed fixed controller.

Preview is read-only. Apply requires the exact current plan digest. Backups and
receipts remain private on the server, never in Actions artifacts. No migration,
restore, policy/feature change, namespace deletion or automatic rollback exists.
"""

from __future__ import annotations

import base64
import datetime
import fcntl
import hashlib
import json
import os
import pwd
import re
import select
import socket
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from upgrade_protocol import (
    NS,
    NS_UID,
    PART,
    TARGET,
    TARGET_UID,
    deployment_snapshot,
    digest,
    encoded,
    image_patch,
    make_plan,
    target_source,
    verification_plan,
)


def kube(*args, value=None, timeout=90):
    result = subprocess.run(["sudo", "-n", "k3s", "kubectl", "--request-timeout=30s", *args],
                            input=encoded(value) if value is not None else None,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError("Kubernetes operation failed; raw output suppressed")
    return result.stdout.decode()


def get(kind, name, namespace=NS):
    return json.loads(kube("get", kind, name, "-n", namespace, "-o", "json"))


def rows(kind, namespace=NS):
    return json.loads(kube("get", kind, "-n", namespace, "-o", "json"))["items"]


def psql_command():
    return ["sudo", "-n", "k3s", "kubectl", "exec", "-i", "-n", NS, "postgres-0", "--",
            "psql", "-X", "-U", "deployflow", "-d", "deployflow", "-At", "-v", "ON_ERROR_STOP=1"]


def query(sql):
    result = subprocess.run(psql_command(), input=("BEGIN READ ONLY; " + sql + "; COMMIT;\n").encode(),
                            capture_output=True, timeout=45, check=False)
    if result.returncode:
        raise ValueError("Read-only application database inspection failed")
    return [json.loads(line) for line in result.stdout.decode().splitlines() if line.startswith("{")]


def pending():
    return query("SELECT json_build_object("
                 "'schema_revision',(SELECT version_num FROM alembic_version),"
                 "'analysis',(SELECT count(*) FROM pending_jobs),"
                 "'analysis_runs',(SELECT count(*) FROM analysis_runs WHERE status IN ('queued','running')),"
                 "'github',(SELECT count(*) FROM github_jobs WHERE status NOT IN ('completed','failed')),"
                 "'delivery',(SELECT count(*) FROM delivery_jobs WHERE status NOT IN ('completed','failed')),"
                 "'execution',(SELECT count(*) FROM execution_jobs WHERE status <> 'completed'))")[0]


def identity():
    if socket.gethostname().split(".")[0] != "homeserver1" or pwd.getpwuid(os.geteuid()).pw_name != "connectops":
        raise ValueError("Wrong server")
    for name, uid in ((NS, NS_UID), (TARGET, TARGET_UID)):
        item = json.loads(kube("get", "namespace", name, "-o", "json"))
        if item["metadata"]["uid"] != uid:
            raise ValueError("Namespace identity changed")
        if name == NS and item["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") != PART:
            raise ValueError("Platform namespace ownership changed")


def protected():
    result = {}
    for kind in ("statefulsets", "services", "networkpolicies", "serviceaccounts", "configmaps", "persistentvolumeclaims", "ingresses"):
        for item in rows(kind):
            meta = item["metadata"]
            if meta.get("labels", {}).get("app.kubernetes.io/part-of") != PART:
                # ACME solver resources may come and go; never mutate/adopt them.
                continue
            fields = {key: value for key, value in item.items() if key not in {"metadata", "status"}}
            result[kind + "/" + meta["name"]] = {"uid": meta["uid"], "sha256": digest(fields)}
    # Only metadata is requested as command output; no Secret data enters previews.
    template = '{{range .items}}{{.metadata.name}}{{"\\t"}}{{.metadata.uid}}{{"\\t"}}{{.metadata.resourceVersion}}{{"\\n"}}{{end}}'
    metadata = kube("get", "secrets", "-n", NS, "-o", "go-template=" + template)
    for line in metadata.splitlines():
        name, uid, version = line.split("\t")
        result["secrets/" + name] = {"uid": uid, "resource_version": version}
    for kind in ("roles", "rolebindings"):
        for item in rows(kind, TARGET):
            if item["metadata"]["name"] in {
                "deployflow-reader", "deployflow-validator", "deployflow-deployer", "deployflow-cleaner",
                "deployflow-platform-reader", "deployflow-platform-validator", "deployflow-platform-deployer", "deployflow-platform-cleaner",
            }:
                result[TARGET + "/" + kind + "/" + item["metadata"]["name"]] = {
                    "uid": item["metadata"]["uid"],
                    "sha256": digest({k: v for k, v in item.items() if k not in {"metadata", "status"}}),
                }
    return result


def schema_fingerprint(pod, user, database):
    # Structure only, never application rows, passwords or identity hashes.
    sql = """SELECT json_build_object(
      'columns',(SELECT json_agg(x ORDER BY relname,attnum) FROM (
        SELECT c.relname,c.relkind,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod) AS type,
          a.attnotnull,pg_get_expr(d.adbin,d.adrelid) AS default_expr
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')) x),
      'constraints',(SELECT json_agg(x ORDER BY conname,definition) FROM (
        SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint
        WHERE connamespace='public'::regnamespace) x),
      'indexes',(SELECT json_agg(x ORDER BY indexname) FROM (
        SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public') x))"""
    raw = kube("exec", "-n", NS, pod, "--", "psql", "-X", "-U", user, "-d", database,
               "-At", "-v", "ON_ERROR_STOP=1", "-c", "BEGIN READ ONLY; " + sql + "; COMMIT;")
    # json_agg(composite) can include literal formatting newlines even with psql
    # unaligned output. Parse the complete row, not just its first printed line.
    payload = "\n".join(line for line in raw.splitlines() if line not in {"BEGIN", "COMMIT"})
    return digest(json.loads(payload))


def snapshot(*, origin=None):
    identity()
    deployments = {}
    for item in rows("deployments"):
        deployments[item["metadata"]["name"]] = deployment_snapshot(item)
        status = item.get("status", {})
        replicas = item["spec"].get("replicas", 1)
        if not origin and (status.get("observedGeneration", 0) != item["metadata"]["generation"]
                or status.get("updatedReplicas", 0) != replicas
                or status.get("readyReplicas", 0) != replicas or status.get("availableReplicas", 0) != replicas):
            raise ValueError("Existing platform is not settled/ready; diagnose before upgrade")
    observed = True
    try:
        version = kube("exec", "-n", NS, "deployment/keycloak", "--", "/opt/keycloak/bin/kc.sh", "--version")
        match = re.search(r"\bKeycloak ([0-9]+\.[0-9]+\.[0-9]+)\b", version)
        if not match:
            raise ValueError("Cannot verify current Keycloak version")
        keycloak_version = match[1]
    except ValueError:
        if not origin:
            raise
        # A broken provider must not prevent an explicitly approved image-only
        # rollback. Require unchanged DB structure and recorded same-version target.
        keycloak_version, observed = origin["before"]["keycloak_version"], False
    work = pending()
    policies = query("SELECT json_build_object('application_id',application_id,'enabled',enabled,"
                     "'allowed_source_sha',allowed_source_sha,'source_compatibility_policy',COALESCE(to_jsonb(deployment_policies)->>'source_compatibility_policy','node-source-compatibility-v1')) FROM deployment_policies ORDER BY application_id")
    applications = query("SELECT json_build_object('application_id',id,'name',name) FROM applications ORDER BY id")
    config = get("configmap", "deployflow-config")["data"]
    flags = {key: config.get(key) for key in ("DF_DEPLOYMENT_EXECUTION_ENABLED", "DF_DEPLOYMENT_CLEANUP_ENABLED")}
    runtime_flags = {}
    for name in ("api", "dispatcher", "delivery-worker", "kube-deployer", "kube-cleaner"):
        if name not in deployments:
            continue
        try:
            code = ("import json;from app.config import settings;s=settings();"
                    "print(json.dumps({'DF_DEPLOYMENT_EXECUTION_ENABLED':s.deployment_execution_enabled,"
                    "'DF_DEPLOYMENT_CLEANUP_ENABLED':s.deployment_cleanup_enabled}))")
            observed_flags = json.loads(kube("exec", "-n", NS, "deployment/" + name, "--", "python", "-c", code))
            runtime_flags[name] = {"observed": True, "flags": observed_flags}
            if not origin and any(observed_flags.get(key) != (flags[key] == "true") for key in flags):
                raise ValueError("Live flags differ from Pod configuration; review capability activation separately")
        except ValueError:
            if not origin:
                raise
            runtime_flags[name] = {"observed": False}
    return {"namespace_uid": NS_UID, "target_namespace_uid": TARGET_UID,
            "schema_revision": work.pop("schema_revision"), "keycloak_version": keycloak_version,
            "keycloak_version_observed": observed,
            "schema_fingerprints": {"application": schema_fingerprint("postgres-0", "deployflow", "deployflow"),
                                    "identity": schema_fingerprint("identity-postgres-0", "keycloak", "keycloak")},
            "pending_jobs": work, "deployments": deployments, "protected": protected(),
            "flags": flags, "runtime_flags": runtime_flags,
            "application_policies": policies, "applications": applications}


class WriteFence:
    """Hold table SHARE locks without DML; bounded and released on any exit."""

    def __init__(self, tables, *, seconds=600, lock_timeout_ms=5000):
        if not tables or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in tables):
            raise ValueError("Unexpected application table names")
        self.tables, self.seconds, self.lock_timeout_ms = tables, seconds, lock_timeout_ms
        self.process = None
        self.deadline = 0

    def __enter__(self):
        self.process = subprocess.Popen(psql_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=0)
        sql = ("BEGIN; SET LOCAL lock_timeout='" + str(self.lock_timeout_ms) + "ms'; "
               "SET LOCAL idle_in_transaction_session_timeout='" + str(self.seconds + 120) + "s'; "
               "LOCK TABLE " + ",".join('public."' + name + '"' for name in sorted(self.tables)) + " IN SHARE MODE;\n"
               "\\echo DEPLOYFLOW_UPGRADE_FENCED\n")
        self.process.stdin.write(sql.encode())
        self.process.stdin.flush()
        output, until = b"", time.monotonic() + self.lock_timeout_ms / 1000 + 10
        try:
            while b"DEPLOYFLOW_UPGRADE_FENCED\n" not in output:
                if self.process.poll() is not None or time.monotonic() >= until:
                    raise ValueError("Cannot pause database writes safely; upgrade refused")
                ready, _, _ = select.select([self.process.stdout], [], [], 0.2)
                if ready:
                    output += os.read(self.process.stdout.fileno(), 4096)
            self.deadline = time.monotonic() + self.seconds
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def check(self):
        if self.process.poll() is not None or time.monotonic() >= self.deadline:
            raise ValueError("Database write fence lost/expired; no further patches permitted")

    def remaining(self):
        self.check()
        return max(1, min(180, int(self.deadline - time.monotonic())))

    def __exit__(self, *_):
        if self.process is not None:
            try:
                self.process.stdin.write(b"ROLLBACK;\n\\q\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)
            self.process.stdout.close()
            self.process.stderr.close()


def private_base(*, create=False):
    base = Path.home() / ".deployflow-upgrades"
    if create and not base.exists():
        base.mkdir(mode=0o700)
    if base.exists() and (base.is_symlink() or not base.is_dir()
                          or base.stat().st_uid != os.getuid() or stat.S_IMODE(base.stat().st_mode) & 0o077):
        raise ValueError("Upgrade receipt directory is not private/owned")
    return base


def receipt_path(identifier):
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("Receipt ID must be its full SHA256")
    path = private_base() / identifier
    if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Private receipt is missing or unsafe")
    return path


def load_receipt(identifier):
    path = receipt_path(identifier) / "receipt.json"
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Unsafe receipt file")
    value = json.loads(path.read_text())
    if value["id"] != identifier or digest(value["plan"]) != identifier:
        raise ValueError("Receipt integrity differs")
    return value


def unfinished(except_id=None):
    base = private_base()
    if not base.exists():
        return []
    result = []
    resolved = resolved_receipts()
    for directory in base.iterdir():
        if re.fullmatch(r"[a-f0-9]{64}", directory.name) and directory.name != except_id:
            value = load_receipt(directory.name)
            if except_id is not None and value["plan"].get("origin_receipt") == except_id:
                # Explicit recovery may retry a failed rollback of this same
                # original upgrade. The server lock still excludes a live peer.
                continue
            if value["status"] != "succeeded" and not value.get("reverted_by") and not value.get("recovered_by") and directory.name not in resolved:
                result.append(directory.name)
    return result


def resolved_receipts():
    """Derive recovery links from atomic successful receipts, never rewrite failure.

    A crash after finalization cannot leave half a multi-file success update.
    A successful verification may close a chain of failed verification attempts.
    """
    base = private_base()
    records = {p.name: load_receipt(p.name) for p in base.iterdir()
               if re.fullmatch(r"[a-f0-9]{64}", p.name)} if base.exists() else {}
    resolved = {}
    for identifier, receipt in records.items():
        if receipt["status"] != "succeeded" or receipt["plan"]["operation"] not in {"verify", "rollback"}:
            continue
        cursor = receipt["plan"].get("origin_receipt")
        child = receipt
        seen = {identifier}
        while cursor:
            if cursor in seen or cursor not in records:
                raise ValueError("Invalid recovery receipt ancestry")
            seen.add(cursor)
            ancestor = records[cursor]
            if child["plan"]["operation"] == "verify" and child["targets"] != ancestor["targets"]:
                raise ValueError("Verification ancestry targets differ")
            resolved[cursor] = identifier
            if child["plan"]["operation"] == "rollback":
                # A completed rollback also closes older partial recovery
                # attempts in this same family. Raw failed receipts stay intact.
                family = {cursor}
                while True:
                    more = {key for key, value in records.items() if value["plan"].get("origin_receipt") in family}
                    if more <= family:
                        break
                    family |= more
                for key in family - {identifier}:
                    if records[key]["status"] != "succeeded":
                        resolved[key] = identifier
            if ancestor["plan"]["operation"] not in {"verify", "rollback"}:
                break
            cursor, child = ancestor["plan"].get("origin_receipt"), ancestor
    return resolved


def save_receipt(path, value):
    temporary = path / "receipt.new"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path / "receipt.json")


def backup(path):
    records = []
    for pod, user, database in (("postgres-0", "deployflow", "deployflow"), ("identity-postgres-0", "keycloak", "keycloak")):
        target = path / (database + ".dump")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            result = subprocess.run(["sudo", "-n", "k3s", "kubectl", "exec", "-n", NS, pod, "--",
                                     "pg_dump", "-U", user, "-d", database, "--format=custom", "--no-owner", "--no-acl"],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=120, check=False)
        if result.returncode:
            raise ValueError("Private database backup failed; no image patch permitted")
        for args in (("--list",), ("--no-owner", "--no-acl", "--file=/dev/null")):
            with target.open("rb") as stream:
                result = subprocess.run(["sudo", "-n", "k3s", "kubectl", "exec", "-i", "-n", NS, pod, "--", "pg_restore", *args],
                                        stdin=stream, capture_output=True, timeout=120, check=False)
            if result.returncode:
                raise ValueError("Private archive validation failed; no image patch permitted")
        records.append({"database": database, "file": target.name, "bytes": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "validation": "Catalog and complete archive-to-SQL read; not a restore"})
    return records


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def fetch(url, authorization=None, accept="application/json"):
    headers = {"Accept": accept}
    if authorization:
        headers["Authorization"] = authorization
    with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url, headers=headers), timeout=20) as response:
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("Registry response exceeds limit")
        return data, response.headers


def verify_images(references):
    # Apply only: existing read-only pull credential stays in server memory.
    secret = get("secret", "deployflow-ghcr")
    config = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    auth = config["auths"]["ghcr.io"]["auth"]
    _, password = base64.b64decode(auth).decode().split(":", 1)
    _, headers = fetch("https://api.github.com/user", "Bearer " + password)
    if {v.strip() for v in headers.get("X-OAuth-Scopes", "").split(",") if v.strip()} != {"read:packages"}:
        raise ValueError("Registry credential must remain read:packages-only")
    result = {}
    for reference in sorted(set(references)):
        repository, expected = reference.removeprefix("ghcr.io/").split("@sha256:")
        query_string = urllib.parse.urlencode({"service": "ghcr.io", "scope": "repository:" + repository + ":pull"})
        raw, _ = fetch("https://ghcr.io/token?" + query_string, "Basic " + auth)
        token = json.loads(raw)["token"]
        raw, headers = fetch("https://ghcr.io/v2/" + repository + "/manifests/sha256:" + expected,
                             "Bearer " + token, "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json")
        if hashlib.sha256(raw).hexdigest() != expected or headers.get("Docker-Content-Digest") != "sha256:" + expected:
            raise ValueError("Registry content digest differs")
        manifests = json.loads(raw).get("manifests", [])
        amd64 = [row["digest"] for row in manifests if row.get("platform", {}).get("os") == "linux"
                 and row.get("platform", {}).get("architecture") == "amd64"]
        if len(amd64) != 1 or not re.fullmatch(r"sha256:[a-f0-9]{64}", amd64[0]):
            raise ValueError("Expected one Linux amd64 manifest in published platform index")
        result[reference] = {reference, "ghcr.io/" + repository + "@" + amd64[0]}
    return result


def verify_running(targets, verified):
    replica_sets = {r["metadata"]["uid"]: r for r in rows("replicasets")}
    pods = rows("pods")
    for name, target in targets.items():
        item = get("deployment", name)
        current = deployment_snapshot(item)
        if current["uid"] != target["uid"] or current["images"] != target["images"]:
            raise ValueError("Deployment image/identity differs after rollout")
        count = 0
        for pod in pods:
            if pod["metadata"].get("deletionTimestamp"):
                continue
            owners = pod["metadata"].get("ownerReferences", [])
            rs = next((replica_sets.get(o["uid"]) for o in owners if o["kind"] == "ReplicaSet" and o.get("controller")), None)
            if not rs or not any(o["uid"] == target["uid"] and o.get("controller") for o in rs["metadata"].get("ownerReferences", [])):
                continue
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if not statuses or not all(c.get("ready") for c in statuses):
                continue
            for group, field in (("containers", "containerStatuses"), ("initContainers", "initContainerStatuses")):
                for container in pod.get("status", {}).get(field, []):
                    reference = target["images"].get(group + "/" + container["name"])
                    image_id = container.get("imageID", "").removeprefix("docker-pullable://")
                    if reference is None or image_id not in verified[reference]:
                        raise ValueError("Running image digest not proven from verified registry manifest")
            count += 1
        if count < current["replicas"]:
            raise ValueError("Ready Pods with verified image IDs are missing")


def execute(request):
    if request["operation"] == "status":
        value = load_receipt(request["receipt_id"])
        return {"receipt_id": value["id"], "recorded_status": value["status"],
                "resolved_by": resolved_receipts().get(value["id"]), "origin_receipt": value["plan"].get("origin_receipt"),
                "operation": value["plan"]["operation"], "current_health": "not observed by this historical receipt read"}
    if request["operation"] in {"verify-preview", "verify-apply"}:
        return recover_verification(request)
    if request["operation"] == "finalize":
        return finalize(request)
    rollback = request["operation"].startswith("rollback")
    origin = load_receipt(request["receipt_id"]) if rollback else None
    if origin and origin["plan"]["operation"] != "upgrade":
        raise ValueError("Rollback requires an original upgrade receipt")
    if unfinished(origin["id"] if origin else None):
        raise ValueError("Unfinished platform upgrade/rollback exists; inspect private receipts before proceeding")
    release = origin["release"] if origin else request["release"]
    plan = make_plan(snapshot(origin=origin), release, origin=origin)
    identifier = digest(plan)
    if request["operation"] in {"preview", "rollback-preview"}:
        return {"plan_sha256": identifier, "plan": plan}
    if request.get("approval") != identifier:
        raise ValueError("Current plan differs from exact approval; preview/review again")
    base = private_base(create=True)
    fd = os.open(base / "operator.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck after taking the server-side lock, not just workflow concurrency.
        if digest(make_plan(snapshot(origin=origin), release, origin=origin)) != identifier:
            raise ValueError("State changed before maintenance lock; no upgrade performed")
        path = base / identifier
        path.mkdir(mode=0o700)  # no overwrite/replay of existing receipts
        targets = {name: {"uid": row["uid"], "images": row["images"]} for name, row in plan["before"]["deployments"].items()}
        for change in plan["changes"]:
            targets[change["name"]]["images"] = change["to_images"]
        journal = {"id": identifier, "status": "preparing", "plan": plan, "before": plan["before"],
                   "release": release, "targets": targets, "applied": []}
        journal["controller"] = request.get("controller")
        journal["authorization"] = request.get("authorization", {"mode": "exact-plan", "plan_sha256": identifier})
        save_receipt(path, journal)
        try:
            if not plan["changes"]:
                # Record admission even when images already match. Otherwise a
                # later rollback followed by replay could re-admit this source.
                # This path verifies without backups, table locks or patches.
                if request.get("_mutation_guard"):
                    request["_mutation_guard"]()
                verify_running(targets, verify_images([i for t in targets.values() for i in t["images"].values()]))
                journal["status"] = "awaiting_public_verification"
                journal["cluster_verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                save_receipt(path, journal)
                return {"status": journal["status"], "receipt_id": identifier, "deployments": [], "operation": plan["operation"],
                        "target_sources": {name: target_source(journal, name) for name in targets},
                        "deployment_images": {name: target["images"] for name, target in targets.items()}}
            tables = query("SELECT json_build_object('name',tablename) FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            with WriteFence([row["name"] for row in tables]) as fence:
                if digest(make_plan(snapshot(origin=origin), release, origin=origin)) != identifier:
                    raise ValueError("State changed before database write fence; no image patched")
                journal["backups"] = backup(path)
                verified = verify_images([image for target in targets.values() for image in target["images"].values()])
                fence.check()
                journal["status"] = "applying"
                save_receipt(path, journal)
                for change in plan["changes"]:
                    fence.check()
                    if request.get("_mutation_guard"):
                        request["_mutation_guard"]()
                    item = get("deployment", change["name"])
                    kube("patch", "deployment", change["name"], "-n", NS, "--type=json", "-p", json.dumps(image_patch(item, change)))
                    journal["applied"].append(change["name"])
                    save_receipt(path, journal)
                for change in plan["changes"]:
                    seconds = fence.remaining()
                    kube("rollout", "status", "deployment/" + change["name"], "-n", NS,
                         "--timeout=" + str(seconds) + "s", timeout=seconds + 10)
                fence.check()
                verify_running(targets, verified)
                if protected() != plan["before"]["protected"]:
                    raise ValueError("Protected resources changed; investigate before further operations")
                # The independent runner proves public HTTPS in its own completed
                # job. Server hairpin connectivity is not a public acceptance gate.
                fence.check()
                # Both current DB/provider versions must still match, without DDL.
                current = snapshot()
                if (current["schema_revision"] != release["schema_revision"]
                        or current["keycloak_version"] != release["keycloak_version"]
                        or current["flags"] != plan["before"]["flags"]
                        or current["schema_fingerprints"] != plan["before"]["schema_fingerprints"]
                        or current["application_policies"] != plan["before"]["application_policies"]):
                    raise ValueError("Schema/provider/authorization context changed during upgrade")
            journal["status"] = "awaiting_public_verification"
            journal["cluster_verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            save_receipt(path, journal)
            return {"status": journal["status"], "receipt_id": identifier, "operation": plan["operation"],
                    "source_sha": plan["source_sha"], "deployments": [c["name"] for c in plan["changes"]],
                    "deployment_images": {name: target["images"] for name, target in targets.items()},
                    "target_sources": {name: target_source(journal, name) for name in targets},
                    "authorization": journal["authorization"],
                    "public_checks": "pending independent runner job and authenticated finalization",
                    "acceptance_limit": "Rollout, running digests and public endpoints verified; authenticated workflows/CD acceptance remain separate"}
        except Exception as exc:
            journal["status"] = "failed"
            journal["failure"] = {"stage": "cluster_upgrade", "type": type(exc).__name__,
                                  "code": str(exc) if type(exc) is ValueError else "operation_failed"}
            save_receipt(path, journal)
            exc.receipt_id = identifier
            raise


def _locked_receipt(identifier):
    # Caller holds this returned file's context until its receipt write finishes.
    base = private_base()
    fd = os.open(base / "operator.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    stream = os.fdopen(fd, "w")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise ValueError("Platform operator is busy") from None


def recover_verification(request):
    origin = load_receipt(request["receipt_id"])
    if origin["id"] in resolved_receipts():
        raise ValueError("Receipt already has a successful recovery")
    ancestors = {origin["id"]}
    cursor = origin["plan"].get("origin_receipt")
    while cursor:
        if cursor in ancestors:
            raise ValueError("Invalid verification ancestry")
        ancestors.add(cursor)
        cursor = load_receipt(cursor)["plan"].get("origin_receipt")
    if set(unfinished(origin["id"])) - ancestors:
        raise ValueError("Unrelated unfinished operation blocks verification recovery")
    plan = verification_plan(snapshot(), origin)
    identifier = digest(plan)
    if request["operation"] == "verify-preview":
        return {"plan": plan, "plan_sha256": identifier}
    if request.get("approval") != identifier:
        raise ValueError("Verification recovery differs from approved plan")
    with _locked_receipt(origin["id"]):
        if origin["id"] in resolved_receipts():
            raise ValueError("Receipt resolved after review")
        if digest(verification_plan(snapshot(), load_receipt(origin["id"]))) != identifier:
            raise ValueError("Verification recovery changed after lock")
        verify_running(origin["targets"], verify_images([i for t in origin["targets"].values() for i in t["images"].values()]))
        path = private_base() / identifier
        path.mkdir(mode=0o700)
        journal = {"id": identifier, "plan": plan, "before": plan["before"], "release": origin["release"],
                   "targets": origin["targets"], "status": "awaiting_public_verification", "applied": [],
                   "controller": request.get("controller"), "authorization": {"mode": "exact-recovery-plan", "plan_sha256": identifier}}
        journal["cluster_verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_receipt(path, journal)
    return {"status": journal["status"], "receipt_id": identifier, "operation": "verify", "origin_receipt": origin["id"]}


def finalize(request):
    from github_proof import GitHubProof

    identifier = request["receipt_id"]
    with _locked_receipt(identifier):
        journal = load_receipt(identifier)
        if identifier in resolved_receipts():
            raise ValueError("Receipt already resolved by a later recovery")
        if journal["status"] != "awaiting_public_verification" or journal.get("reverted_by") or journal.get("recovered_by"):
            raise ValueError("Receipt is not awaiting independent verification")
        if not journal.get("controller") or journal["controller"] != request.get("controller"):
            raise ValueError("Finalization controller differs from original operation")
        proof = GitHubProof(request.get("github_token")).public_job(journal["controller"], since=journal["cluster_verified_at"])
        current = snapshot()
        for key in ("protected", "schema_fingerprints", "schema_revision", "keycloak_version", "flags", "application_policies"):
            if current[key] != journal["before"][key]:
                raise ValueError("Finalization context changed: " + key)
        if set(current["deployments"]) != set(journal["targets"]):
            raise ValueError("Finalization inventory changed")
        for name, row in current["deployments"].items():
            expected_source = target_source(journal, name)
            if (row["contract_sha256"] != journal["before"]["deployments"][name]["contract_sha256"]
                    or row["source_sha"] != expected_source):
                raise ValueError("Finalization workload/source changed")
        verify_running(journal["targets"], verify_images([i for t in journal["targets"].values() for i in t["images"].values()]))
        journal["public_checks"] = proof
        journal["status"] = "succeeded"
        save_receipt(receipt_path(identifier), journal)
        return {"status": "succeeded", "receipt_id": identifier, "operation": journal["plan"]["operation"], "public_checks": proof,
                "deployment_images": {name: target["images"] for name, target in journal["targets"].items()},
                "target_sources": {name: row["source_sha"] for name, row in current["deployments"].items()}}


def main():
    request = json.load(sys.stdin)
    if request.get("operation") not in {"preview", "apply", "rollback-preview", "rollback-apply",
                                        "verify-preview", "verify-apply", "finalize", "status"}:
        raise ValueError("Unknown upgrade operation")
    identity()
    print(json.dumps(execute(request)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — never expose credential-bearing transport exceptions.
        # Registry, DB, subprocess and JSON exceptions can contain credential data.
        message = str(exc) if type(exc) is ValueError else "Operation failed; inspect private server receipts"
        print(json.dumps({"error": message, "receipt_id": getattr(exc, "receipt_id", None), "automatic_rollback": False}))
        sys.exit(1)
