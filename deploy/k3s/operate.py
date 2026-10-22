"""Review or apply ONE approved fresh-install stage over strictly verified SSH.

Default invocation is read-only. A matching digest is a confirmation mechanism,
not standing operational authorization: obtain the user's scoped approval first.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import stat
import subprocess
from pathlib import Path

from bundle import STAGES, digest, encoded, validate
from prepare_secrets import private_path
from render import NAMESPACE

SECRET_KEYS = {
    "deployflow-ghcr": {".dockerconfigjson"}, "deployflow-postgres": {"password"},
    "deployflow-identity-postgres": {"password"}, "deployflow-redis": {"password"},
    "deployflow-runtime": {"DF_DATABASE_URL", "DF_REDIS_URL"}, "deployflow-github-key": {"private.pem"},
    "deployflow-webhook": {"secret"}, "deployflow-identity-bootstrap": {"admin-password", "user-password"},
}


def secret_document(folder, *, registry_on_cluster=False):
    folder = private_path(folder)
    if stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise ValueError("Secret directory must be private (0700)")
    items = []
    for name, keys in SECRET_KEYS.items():
        if registry_on_cluster and name == "deployflow-ghcr":
            continue
        path = private_path(folder / f"{name}.json")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Secret file must be private (0600)")
        item = json.loads(path.read_text())
        if (item.get("kind") != "Secret" or item.get("metadata", {}).get("name") != name
                or item["metadata"].get("namespace") != NAMESPACE or set(item.get("data", {})) != keys
                or item["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") != "deployflow-platform"):
            raise ValueError("Unexpected Secret shape or target")
        expected_type = "kubernetes.io/dockerconfigjson" if name == "deployflow-ghcr" else "Opaque"
        if item.get("type") != expected_type:
            raise ValueError("Unexpected Secret type")
        # Reconstruct only the approved fields; no stringData/ownerReferences etc.
        items.append({"apiVersion": "v1", "kind": "Secret", "metadata": {
            "name": name, "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/part-of": "deployflow-platform"}},
            "type": expected_type, "data": item["data"]})
    return {"apiVersion": "v1", "kind": "List", "items": items}


def ssh_command(host, port, user, key, known_hosts):
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Invalid SSH destination")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid SSH port")
    remote = (Path(__file__).with_name("remote.py")).read_text()
    return ["ssh", "-T", "-p", str(port), "-i", str(key), "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            f"{user}@{host}", "python3 -c " + shlex.quote(remote)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["inspect", "capture-host", "secrets", "restore-ingress", *STAGES], default="inspect")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--secrets-dir", type=Path)
    parser.add_argument("--registry-secret-on-cluster", action="store_true",
                        help="For secrets stage: use the already provisioned namespace pull Secret")
    parser.add_argument("--host-backup", type=Path)
    parser.add_argument("--platform-ingress-uid", help="Exact current Ingress UID for separately approved restoration; or absent")
    parser.add_argument("--approve-sha256", help="After scoped approval only; absent means review without SSH mutation")
    parser.add_argument("--host", default="91.89.185.146")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="connectops")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    args = parser.parse_args()
    if args.registry_secret_on_cluster and args.stage != "secrets":
        parser.error("--registry-secret-on-cluster applies only to --stage secrets")
    request = {"operation": args.stage}
    if args.stage not in {"inspect", "capture-host"}:
        if args.stage == "secrets":
            if not args.secrets_dir:
                parser.error("--secrets-dir is required")
            document = secret_document(args.secrets_dir, registry_on_cluster=args.registry_secret_on_cluster)
        elif args.stage == "restore-ingress":
            if not args.host_backup or not args.platform_ingress_uid:
                parser.error("Restore requires --host-backup and --platform-ingress-uid")
            previous = json.loads(args.host_backup.read_text())
            document = {"items": [previous["restore"]], "previous": previous,
                        "platform_ingress_uid": args.platform_ingress_uid}
        else:
            if not args.bundle:
                parser.error("--bundle is required")
            value = validate(json.loads(args.bundle.read_text()))
            document = value["stages"][args.stage]
            print("Source:", value["source_sha"])
            print("Images:", json.dumps(value["images"], sort_keys=True))
            if args.stage == "ingress":
                if not args.host_backup:
                    parser.error("--host-backup captured read-only is required")
                document = {**document, "previous": json.loads(args.host_backup.read_text())}
        approved = digest(document)
        print("Stage:", args.stage, "SHA256:", approved)
        for item in document["items"]:
            print(item["kind"], item["metadata"].get("namespace", "cluster"), item["metadata"]["name"])
        if not args.approve_sha256:
            print("REVIEW ONLY. No SSH operation performed. Obtain scoped approval before passing this digest.")
            return
        if args.approve_sha256 != approved:
            parser.error("Approval digest does not match the exact stage")
        request.update(document=document, approval=approved)
    command = ssh_command(args.host, args.port, args.user, args.key, args.known_hosts)
    if args.stage == "capture-host":
        if not args.host_backup:
            parser.error("--host-backup destination required")
        output = private_path(args.host_backup)
        if stat.S_IMODE(output.parent.stat().st_mode) & 0o077 or output.exists():
            parser.error("Backup requires a private parent and new output path")
        result = subprocess.run(command, input=encoded(request), capture_output=True, check=True, timeout=180)
        value = json.loads(result.stdout)
        import os
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(value))
        print("Sanitized mockup restore manifest captured privately; no resources changed")
    else:
        subprocess.run(command, input=encoded(request), check=True, timeout=3600)


if __name__ == "__main__":
    main()
