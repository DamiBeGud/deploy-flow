"""Preview/apply a protected, image-only upgrade of the installed platform.

This is distinct from fresh installation and application releases. Preview only
reads remote state. Apply/rollback require a previously reviewed exact plan SHA256
and explicit confirmation; this mechanism does not grant operational authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from bundle import validate
from operate import ssh_command
from render import runtime
from upgrade_protocol import contract, digest, encoded


def release_metadata(value):
    validate(value)
    deployments = value["stages"]["workloads"]["items"]
    # Optional cleaner is upgraded only if it already exists. Never install it
    # or enable either capability as a side effect of upgrading images.
    cleaner = runtime("kube-cleaner", value["images"]["backend"],
                      ["dramatiq", "app.worker", "--processes", "1", "--threads", "1", "--queues", "cleanup"], identity=True)
    return {"source_sha": value["source_sha"], "images": value["images"],
            "schema_revision": value["schema_revision"], "keycloak_version": value["keycloak_version"],
            "contracts": {item["metadata"]["name"]: contract(item["spec"]) for item in [*deployments, cleaner]}}


def command(args):
    result = ssh_command(args.host, args.port, args.user, args.key, args.known_hosts)
    root = Path(__file__).parent
    # Embed only this checkout's trusted stdlib modules. No server checkout,
    # dependency installation, credentials, or mutable downloaded script needed.
    program = ("import types,sys\n"
               "m=types.ModuleType('upgrade_protocol');sys.modules['upgrade_protocol']=m\n"
               "exec(" + repr((root / "upgrade_protocol.py").read_text()) + ",m.__dict__)\n"
               "exec(" + repr((root / "upgrade_remote.py").read_text()) + ",{'__name__':'__main__'})\n")
    result[-1] = "python3 -c " + shlex.quote(program)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=["preview", "apply", "rollback-preview", "rollback-apply"], default="preview")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--source-sha", help="Exact current main checkout/publication SHA")
    parser.add_argument("--receipt-id", help="Original server upgrade receipt for rollback")
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--confirmation", help="UPGRADE <source SHA> or ROLLBACK <receipt ID>, after scoped approval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="91.89.185.146")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="connectops")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    args = parser.parse_args()
    request = {"operation": args.operation}
    if args.operation.startswith("rollback"):
        if not args.receipt_id or not re.fullmatch(r"[a-f0-9]{64}", args.receipt_id):
            parser.error("Rollback requires an exact original receipt ID")
        request["receipt_id"] = args.receipt_id
        confirmation = "ROLLBACK " + args.receipt_id
    else:
        if not args.bundle or not args.source_sha:
            parser.error("Upgrade requires the authenticated publication bundle and exact source SHA")
        value = validate(json.loads(args.bundle.read_text()))
        if value["source_sha"] != args.source_sha:
            parser.error("Publication source differs from current main checkout")
        request["release"] = release_metadata(value)
        confirmation = "UPGRADE " + args.source_sha
    if args.operation.endswith("apply"):
        if not args.approve_plan_sha256 or not re.fullmatch(r"[a-f0-9]{64}", args.approve_plan_sha256):
            parser.error("Mutation requires exact reviewed plan SHA256")
        if args.confirmation != confirmation:
            parser.error("Explicit scoped confirmation does not match the selected source/receipt")
        request["approval"] = args.approve_plan_sha256
    elif args.approve_plan_sha256 or args.confirmation:
        parser.error("Preview cannot accept mutation approval")
    # Never replace prior review/results. No credential data or backup bytes are
    # returned by the remote protocol or written to GitHub artifacts.
    if args.output.exists():
        parser.error("Output already exists; use a new review/result path")
    result = subprocess.run(command(args), input=encoded(request), capture_output=True, timeout=1200, check=False)
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise SystemExit("SSH upgrade failed; raw output suppressed. Inspect server receipts before retrying") from None
    if "plan" in response and digest(response["plan"]) != response["plan_sha256"]:
        raise SystemExit("Remote plan integrity differs")
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded(response))
    if result.returncode or response.get("error"):
        if response.get("receipt_id"):
            print("Recovery receipt ID:", response["receipt_id"])
        raise SystemExit(response.get("error", "SSH upgrade failed; inspect server receipts"))
    if "plan" in response:
        plan = response["plan"]
        print("REVIEW ONLY. No cluster changes, backups, locks, migrations or restarts performed.")
        print("Plan SHA256:", response["plan_sha256"])
        print("Schema:", plan["schema_revision"], "Keycloak:", plan["keycloak_version"])
        print("Live flags preserved:", json.dumps(plan["before"]["flags"], sort_keys=True))
        print("Observed Pod flags:", json.dumps(plan["before"].get("runtime_flags", {}), sort_keys=True))
        print("Application policies preserved:", json.dumps(plan["before"]["application_policies"], sort_keys=True))
        for change in plan["changes"]:
            print("Deployment", change["name"], "UID", change["uid"], "replicas", change["replicas"])
            print("  From:", json.dumps(change["from_images"], sort_keys=True))
            print("  To:  ", json.dumps(change["to_images"], sort_keys=True))
        print(json.dumps(plan["effects"], indent=2))
    else:
        print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()
