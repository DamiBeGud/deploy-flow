"""Run via SSH stdin: verify an existing pull credential and optionally copy it.

No dependency on a server checkout. Credentials stay in server memory and are
never printed or written to files. Default is read-only; --apply creates only
the reviewed platform Namespace and pull Secret. Never updates existing values.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

NAMESPACE = "deployflow-system"
PART = "deployflow-platform"
IMAGES = {
    "deployflow-backend": "47b2787a01f93371114ccbd5426ea1b4628e8b50446bb6735fe23a55ebbceb24",
    "deployflow-frontend": "627f7561027ec24b475572fc54df9083d5b70b4488c556372cb07842e7bad5da",
    "deployflow-keycloak": "91fbecdd9f5ac089492f0905463a884d291b165b347d2da243c66b5add96de75",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, authorization, accept="application/json"):
    request = urllib.request.Request(url, headers={"Authorization": authorization,
        "Accept": accept, "User-Agent": "DeployFlow-registry-access-check"})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        return response.read(4 * 1024 * 1024), response.headers


def kubectl(*args, value=None):
    result = subprocess.run(["sudo", "-n", "k3s", "kubectl", "--request-timeout=20s", *args],
                            input=json.dumps(value) if value is not None else None,
                            text=True, capture_output=True, check=False, timeout=45)
    if result.returncode:
        raise ValueError("Kubernetes operation failed; raw error suppressed to protect credentials")
    # create emits a plain acknowledgement by default, e.g. "namespace/... created".
    # It has already succeeded at this point; parsing that text as JSON incorrectly
    # aborted between namespace creation and Secret creation. Only reads return JSON.
    if args[0] == "create":
        return None
    return json.loads(result.stdout) if result.stdout.strip() else None


def get(kind, name, namespace=None):
    scope = ["-n", namespace] if namespace else []
    return kubectl("get", kind, name, *scope, "--ignore-not-found", "-o", "json")


def owned(value):
    return value["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") == PART


def pull_secret(source):
    if source.get("type") != "kubernetes.io/dockerconfigjson":
        raise ValueError("Source must be a dockerconfigjson registry Secret")
    config = json.loads(base64.b64decode(source["data"][".dockerconfigjson"]))
    entries = [value for host, value in config["auths"].items()
               if host.rstrip("/") in {"ghcr.io", "https://ghcr.io", "https://ghcr.io/v1"}]
    if len(entries) != 1:
        raise ValueError("Expected exactly one GHCR credential entry")
    auth = entries[0]
    if auth.get("auth"):
        username, password = base64.b64decode(auth["auth"]).decode().split(":", 1)
    else:
        username, password = auth["username"], auth["password"]
    _, headers = fetch("https://api.github.com/user", "Bearer " + password)
    scopes = {scope.strip() for scope in headers.get("X-OAuth-Scopes", "").split(",") if scope.strip()}
    if scopes != {"read:packages"}:
        raise ValueError("Copy stopped: existing credential is not confirmed read:packages-only. Use a separate read-only token")
    print("Read-only scope confirmed. Expiry:", headers.get("GitHub-Authentication-Token-Expiration", "not reported"))
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    for name, digest in IMAGES.items():
        query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:damibegud/{name}:pull"})
        response, _ = fetch("https://ghcr.io/token?" + query, "Basic " + basic)
        token = json.loads(response)["token"]
        body, headers = fetch(f"https://ghcr.io/v2/damibegud/{name}/manifests/sha256:{digest}", "Bearer " + token,
                              "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json")
        manifests = json.loads(body).get("manifests", [])
        if headers.get("Docker-Content-Digest") != "sha256:" + digest or not any(
                item.get("platform", {}).get("os") == "linux" and item["platform"].get("architecture") == "amd64"
                for item in manifests):
            raise ValueError("Registry digest or Linux amd64 manifest differs")
        print("Verified registry read access:", name)
    # Copy only GHCR authentication, excluding any unrelated registries/settings.
    docker_config = json.dumps({"auths": {"ghcr.io": {"auth": basic}}}).encode()
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {
        "name": "deployflow-ghcr", "namespace": NAMESPACE,
        "labels": {"app.kubernetes.io/part-of": PART}}, "type": "kubernetes.io/dockerconfigjson",
        "data": {".dockerconfigjson": base64.b64encode(docker_config).decode()}}


def run(apply=False):
    if socket.gethostname().split(".")[0] != "homeserver1":
        raise ValueError("Wrong server")
    target = get("namespace", "deployflow-test")
    if not target or target["metadata"]["uid"] != "84a720fd-dc36-4592-a2bb-12c8f54ddc7c":
        raise ValueError("Target namespace identity changed")
    namespace = get("namespace", NAMESPACE)
    if namespace:
        if not owned(namespace):
            raise ValueError("Destination namespace has different ownership")
        pending = kubectl("get", "deployments,statefulsets,jobs,pods", "-n", NAMESPACE, "-o", "json")
        if pending["items"]:
            raise ValueError("Existing workloads found; copying credentials could activate pending work. Review separately")
    source = get("secret", "ghcr-pull-secret", "connect-app")
    if not source:
        raise ValueError("Source registry Secret is missing")
    desired = pull_secret(source)
    current = get("secret", "deployflow-ghcr", NAMESPACE) if namespace else None
    if current and (not owned(current) or current.get("type") != desired["type"] or current.get("data") != desired["data"]):
        raise ValueError("Existing destination credential differs; refusing overwrite/rotation")
    if not apply:
        print("VERIFIED; NO CHANGES. --apply creates only Namespace/deployflow-system and Secret/deployflow-ghcr if absent")
        return
    if not namespace:
        kubectl("create", "-f", "-", value={"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": NAMESPACE, "labels": {"app.kubernetes.io/part-of": PART,
                "pod-security.kubernetes.io/enforce": "restricted", "pod-security.kubernetes.io/enforce-version": "v1.36",
                "pod-security.kubernetes.io/audit": "restricted", "pod-security.kubernetes.io/warn": "restricted"}}})
        print("Created Namespace/deployflow-system")
    if not current:
        kubectl("create", "-f", "-", value=desired)
        print("Created Secret/deployflow-ghcr in deployflow-system")
    else:
        print("Destination Secret already matches; unchanged")
    print("Source Connect Secret unchanged. No workloads, services or policies deployed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create only the two reviewed resources after scoped approval")
    args = parser.parse_args()
    try:
        run(args.apply)
    except urllib.error.HTTPError as exc:
        print("Check stopped: HTTP", exc.code, "(credential/response details suppressed)", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        # Only fixed messages from this script are safe; JSON errors can be subclassed ValueError.
        print("Check stopped:", str(exc) if type(exc) is ValueError else "invalid configuration; details suppressed", file=sys.stderr)
        sys.exit(1)
    except Exception:  # noqa: BLE001 — CLI boundary must never leak a credential-bearing traceback.
        print("Check stopped; raw exception suppressed to protect credentials", file=sys.stderr)
        sys.exit(1)
