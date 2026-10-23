"""Runner adapter: temporary SSH files, exact inputs, metadata-only result output."""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from github_proof import GitHubProof


def finalization_receipt(env, proof=None):
    try:
        context = {"run_id": int(env["GITHUB_RUN_ID"]), "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                   "source_sha": env.get("DF_CONTROLLER_SOURCE_SHA") or env["GITHUB_SHA"],
                   "workflow": env["DF_CONTROLLER_WORKFLOW"]}
        if not env.get("GH_TOKEN") and proof is None:
            raise ValueError("missing token")
    except (KeyError, ValueError):
        raise ValueError("Finalization artifact context/token missing") from None
    proof = proof or GitHubProof(env["GH_TOKEN"])
    proof.run(context)
    name = f"platform-upgrade-{context['run_id']}-{context['attempt']}"
    artifacts = proof.read(f"/actions/runs/{context['run_id']}/artifacts?per_page=100").get("artifacts", [])
    found = [a for a in artifacts if a.get("name") == name and not a.get("expired")]
    if len(found) != 1:
        raise ValueError("Exact upgrade metadata artifact unavailable")
    artifact = found[0]
    raw = proof.read(f"/actions/artifacts/{artifact['id']}/zip", raw=True)
    if artifact.get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ValueError("Upgrade metadata artifact digest differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [i for i in archive.infolist() if i.filename == "result.json"]
        if len(entries) != 1 or entries[0].file_size > 1024 * 1024:
            raise ValueError("Upgrade metadata artifact invalid")
        value = json.loads(archive.read(entries[0]))
    receipt = value.get("receipt_id", "")
    if value.get("status") != "awaiting_public_verification" or not re.fullmatch(r"[a-f0-9]{64}", receipt):
        raise ValueError("Upgrade artifact is not awaiting verification")
    return receipt


def main(stage):
    env = dict(os.environ)
    output = Path("upgrade-review/result.json")
    output.parent.mkdir(exist_ok=True)
    operation = "finalize" if stage == "finalize" else env["OPERATION"]
    if stage == "finalize":
        try:
            env["RECEIPT_ID"] = finalization_receipt(env)
        except Exception:
            output.write_text(json.dumps({"error": "finalization_metadata_unavailable", "operation": operation}) + "\n")
            raise
    if operation not in {"preview", "apply", "auto-preview", "auto-apply"} and not re.fullmatch(r"[a-f0-9]{64}", env.get("RECEIPT_ID", "")):
        output.write_text(json.dumps({"error": "exact_receipt_id_missing", "operation": operation}) + "\n")
        raise ValueError("Exact receipt ID unavailable; inspect upgrade metadata and GitHub output-redaction warnings")
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
