"""Runner adapter: temporary SSH files, exact inputs, metadata-only result output."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main(stage):
    env = os.environ
    output = Path("upgrade-review/result.json")
    output.parent.mkdir(exist_ok=True)
    operation = "finalize" if stage == "finalize" else env["OPERATION"]
    with tempfile.TemporaryDirectory(prefix="deployflow-upgrade-", dir=env["RUNNER_TEMP"]) as tmp:
        root = Path(tmp)
        for name, variable in (("key", "SSH_KEY"), ("known_hosts", "SSH_HOSTS")):
            value = env.get(variable, "")
            if not value.strip():
                raise ValueError("Missing scoped SSH configuration: " + variable)
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value.rstrip() + "\n")
        args = [sys.executable, "deploy/k3s/upgrade.py", "--operation", operation,
                "--output", str(output), "--host", env["SSH_HOST"], "--port", env.get("SSH_PORT") or "22",
                "--user", env["SSH_USER"], "--key", str(root / "key"), "--known-hosts", str(root / "known_hosts")]
        if env.get("DF_FIXED_OPERATOR") == "true":
            args += ["--fixed-operator"]
        if operation.startswith("auto"):
            args += ["--publication-run-id", env["RELEASE_RUN_ID"], "--publication-attempt", env["RELEASE_ATTEMPT"]]
        elif operation in {"preview", "apply"}:
            args += ["--bundle", "platform-release/release.json", "--source-sha", env["GITHUB_SHA"]]
        else:
            args += ["--receipt-id", env["RECEIPT_ID"]]
        if operation.endswith("apply") and not operation.startswith("auto"):
            args += ["--approve-plan-sha256", env.get("APPROVAL", ""), "--confirmation", env.get("CONFIRMATION", "")]
        elif env.get("APPROVAL") or env.get("CONFIRMATION"):
            raise ValueError("Preview/automatic/finalize does not accept explicit mutation inputs")
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        # upgrade.py prints only protocol-safe metadata. Never echo stderr from
        # the SSH adapter, which can contain connection/configuration details.
        print(result.stdout)
        if result.returncode:
            raise ValueError("Upgrade operation failed; inspect metadata artifact and private receipt")
        value = json.loads(output.read_text())
        if env.get("GITHUB_OUTPUT"):
            with open(env["GITHUB_OUTPUT"], "a") as stream:
                stream.write("receipt_id=" + value.get("receipt_id", "") + "\n")
                stream.write("needs_verification=" + str(value.get("status") == "awaiting_public_verification").lower() + "\n")
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a") as stream:
                stream.write(result.stdout)
        if value.get("status") in {"waiting", "blocked"}:
            raise ValueError("No deployment performed: " + value["status"] + "; " + value.get("reason", "") + ". Scheduled reconciliation will retry pending work.")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 -- credential-safe protocol boundary
        raise SystemExit(str(exc) if type(exc) is ValueError else "Runner adapter failed; inspect metadata artifact") from None
