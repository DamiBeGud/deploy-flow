"""Fixed, policy-bound server operator. Install only after reviewing exact scope.

Use a dedicated forced-command SSH key. This program and its imported files must
be root-owned and not writable by the SSH account; it never runs candidate code.
"""

import datetime
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

import upgrade_remote as remote
from bundle import validate
from github_proof import GitHubProof
from upgrade import release_metadata
from upgrade_protocol import NS_UID, TARGET_UID, digest, make_plan

POLICY = Path("/opt/deployflow-platform/policy.json")
ROOT = Path(__file__).resolve().parents[2]
PROTECTED_FILES = (
    ".github/workflows/platform-images.yml", ".github/workflows/platform-auto.yml",
    ".github/workflows/platform-upgrade.yml",
    ".github/workflows/platform-k3s.yml",
    "backend/Dockerfile", "backend/pyproject.toml", "backend/uv.lock",
    "frontend/Dockerfile", "frontend/nginx.conf", "frontend/package.json", "frontend/package-lock.json",
    "deploy/k3s/Dockerfile.keycloak",
    "backend/ops/local-oidc/deployflow-local-realm.json",
    "backend/app/config.py", "backend/app/main.py", "backend/app/webhook_main.py", "backend/app/api/routes.py",
    "backend/app/api/identity.py", "backend/app/api/github_webhook.py",
    "backend/app/api/delivery_routes.py", "backend/app/persistence/models.py", "backend/app/dispatcher.py",
    *tuple("backend/app/kubernetes/" + name + ".py" for name in ("client", "executor", "policy", "gateway")),
    *tuple("backend/app/integrations/github/" + name + ".py" for name in ("api", "verification", "compatibility", "source_updates", "jobs")),
    *tuple("backend/app/deployments/" + name + ".py" for name in ("preparation", "jobs", "release", "execution", "queue", "rollback", "cleanup", "state")),
    *tuple(str(p.relative_to(ROOT)) for p in sorted((ROOT / "backend/migrations/versions").glob("*.py"))),
    *tuple("deploy/k3s/" + name for name in (
        "automatic.py", "upgrade.py", "upgrade_remote.py", "upgrade_protocol.py", "github_proof.py",
        "public_checks.py", "check_publication.py", "bundle.py", "render.py", "operate.py", "remote.py", "prepare_secrets.py", "workflow_upgrade.py", "automatic_candidate.py",
    )),
)


def infrastructure_fingerprints(value):
    # Credential values never enter policy. Approved rotations may change a
    # Secret resourceVersion between upgrades; replacing its UID needs review.
    return {key: {"uid": row["uid"]} if key.startswith("secrets/") else dict(row)
            for key, row in value.items()}


def latest_publication(proof, request, source):
    runs = proof.read("/actions/workflows/platform-images.yml/runs?branch=main&event=push&head_sha=" + source + "&per_page=30").get("workflow_runs", [])
    runs.sort(key=lambda r: (r.get("id", 0), r.get("run_attempt", 0)), reverse=True)
    if (not runs or runs[0].get("id") != request["publication_run_id"]
            or runs[0].get("run_attempt") != request["publication_attempt"]
            or runs[0].get("status") != "completed" or runs[0].get("conclusion") != "success"):
        raise ValueError("Publication superseded or latest current-main push CI is not successful")


def load_policy():
    value = POLICY.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022):
        raise ValueError("Automatic policy must be root-owned and not group/world writable")
    policy = json.loads(POLICY.read_text())
    until = datetime.datetime.fromisoformat(policy["expires_at"].replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    if (until.tzinfo is None or until <= now or until > now + datetime.timedelta(days=90)
            or policy.get("format") != 1 or not re.fullmatch(r"[a-f0-9]{64}", policy.get("id", ""))
            or not re.fullmatch(r"[a-f0-9]{40}", policy.get("baseline_sha", ""))
            or policy.get("enabled") is not True or policy.get("namespace_uid") != NS_UID
            or policy.get("target_namespace_uid") != TARGET_UID):
        raise ValueError("Automatic policy disabled, expired or outside namespace scope")
    if set(policy["approved_files"]) != set(PROTECTED_FILES):
        raise ValueError("Automatic controller approval incomplete")
    # Check the installed controller too: a candidate cannot replace its policy
    # checker or renderer by sending Python over SSH.
    root = Path(__file__).resolve().parents[2]
    for path in PROTECTED_FILES:
        if not path.startswith("deploy/k3s/"):
            continue
        file = root / path
        for parent in (root, root / "deploy", root / "deploy/k3s"):
            meta = parent.lstat()
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
                raise ValueError("Installed operator directories are not protected")
        meta = file.lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise ValueError("Installed operator files are not protected")
        if hashlib.sha256(file.read_bytes()).hexdigest() != policy["approved_files"][path]:
            raise ValueError("Installed operator differs from approved policy")
    return policy


def admit(request, policy, proof):
    context = request["controller"]
    proof.run(context)
    if context["workflow"] != ".github/workflows/platform-auto.yml":
        raise ValueError("Automatic controller workflow differs")
    source = context["source_sha"]
    scope = policy["source_scope"]
    if scope != {"mode": "future-compatible"} and scope != {"mode": "exact", "sha": source}:
        raise ValueError("Source is outside the explicitly approved automatic source scope")
    if proof.read("/git/ref/heads/main")["object"]["sha"] != source:
        raise ValueError("source_superseded")
    comparison = proof.read("/compare/" + policy["baseline_sha"] + "..." + source)
    if comparison.get("status") not in {"ahead", "identical"}:
        raise ValueError("Source is outside approved forward main history")
    migration_files = proof.read("/contents/backend/migrations/versions?ref=" + source)
    expected_migrations = {p.rsplit("/", 1)[-1] for p in PROTECTED_FILES if p.startswith("backend/migrations/versions/")}
    if {p["name"] for p in migration_files if p["name"].endswith(".py")} != expected_migrations:
        raise ValueError("Migration inventory changed; fresh platform review required")
    for path, expected in policy["approved_files"].items():
        if hashlib.sha256(proof.file(path, source)).hexdigest() != expected:
            raise ValueError("Controller/infrastructure changed; fresh platform review required: " + path)
    latest_publication(proof, request, source)
    bundle = validate(proof.publication(request["publication_run_id"], request["publication_attempt"], source))
    release = release_metadata(bundle)
    if release["schema_revision"] != policy["schema_revision"] or release["keycloak_version"] != policy["keycloak_version"]:
        raise ValueError("Schema/provider change is outside automatic policy")
    for directory in remote.private_base().glob("[a-f0-9]" * 64):
        receipt = remote.load_receipt(directory.name)
        if receipt["plan"]["operation"] != "upgrade":
            continue
        previous = receipt["release"]["source_sha"]
        if previous == source:
            exc = ValueError("source_already_admitted")
            exc.receipt_id = directory.name
            exc.unresolved = receipt.get("status") != "succeeded" and not receipt.get("reverted_by") and not receipt.get("recovered_by") and directory.name not in remote.resolved_receipts()
            raise exc
        if proof.read("/compare/" + previous + "..." + source).get("status") != "ahead":
            raise ValueError("An older candidate cannot overwrite a later admitted source")
    return release


def execute(request):
    if set(request) - {"operation", "controller", "github_token", "publication_run_id", "publication_attempt", "receipt_id"}:
        raise ValueError("Unexpected fixed-operator input")
    if request.get("operation") == "status":
        return remote.execute({"operation": "status", "receipt_id": request["receipt_id"]})
    if request.get("operation") == "finalize":
        return remote.finalize(request)
    if request.get("operation") not in {"auto-preview", "auto-apply"}:
        raise ValueError("Dedicated automatic key does not authorize manual apply/rollback/recovery")
    if any(type(request.get(k)) is not int or request[k] < 1 for k in ("publication_run_id", "publication_attempt")):
        raise ValueError("Exact positive publication identifiers required")
    policy = load_policy()
    proof = GitHubProof(request.get("github_token"))
    try:
        release = admit(request, policy, proof)
    except ValueError as exc:
        if str(exc) == "source_already_admitted":
            return {"status": "blocked" if exc.unresolved else "already_admitted", "receipt_id": exc.receipt_id,
                    "reason": "No replay or redeployment; inspect original receipt for recovery"}
        raise
    if remote.unfinished():
        return {"status": "blocked", "reason": "unresolved_platform_receipt"}
    current = remote.snapshot()
    if {name: row["uid"] for name, row in current["deployments"].items()} != policy["deployment_uids"]:
        raise ValueError("Platform Deployment identity/inventory changed since approval")
    if infrastructure_fingerprints(current["protected"]) != policy["infrastructure"]:
        raise ValueError("Platform infrastructure changed since approval")
    if current["flags"] != policy["flags"]:
        raise ValueError("Live feature flags changed since automatic authorization")
    if any(current["pending_jobs"].values()):
        return {"status": "waiting", "reason": "pending_work", "retry": "next scheduled reconciliation; no receipt or patch created"}
    plan = make_plan(current, release)
    if request["operation"] == "auto-preview":
        return {"plan": plan, "plan_sha256": digest(plan), "policy_id": policy["id"]}

    started = False

    def guard():
        nonlocal started
        if digest(load_policy()) != digest(policy):
            raise ValueError("Automatic authorization revoked/changed before mutation")
        if remote.protected() != current["protected"]:
            raise ValueError("Infrastructure/credential identity changed before mutation")
        if not started:
            if proof.read("/git/ref/heads/main")["object"]["sha"] != release["source_sha"]:
                raise ValueError("source_superseded")
            latest_publication(proof, request, release["source_sha"])
            started = True

    # Authorization comes from protected policy plus independently queried
    # provenance, never from a caller-supplied plan hash or confirmation string.
    return remote.execute({"operation": "apply", "release": release, "approval": digest(plan),
                           "controller": request["controller"], "_mutation_guard": guard,
                           "authorization": {"mode": "automatic-policy", "policy_id": policy["id"],
                                             "policy_sha256": digest(policy), "source_sha": release["source_sha"]}})


if __name__ == "__main__":
    try:
        if os.environ.get("SSH_ORIGINAL_COMMAND", "") != "deployflow-platform":
            raise ValueError("Only the fixed JSON protocol is permitted")
        raw = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Request too large")
        print(json.dumps(execute(json.loads(raw))))
    except Exception as exc:  # noqa: BLE001 -- credential-safe protocol boundary
        print(json.dumps({"error": str(exc) if type(exc) is ValueError else "Fixed operator failed; inspect private receipt", "automatic_rollback": False,
                          "receipt_id": getattr(exc, "receipt_id", None)}))
        sys.exit(1)
