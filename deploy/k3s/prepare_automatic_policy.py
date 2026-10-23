"""Offline automatic-policy proposal. Never installs policy, keys or server files."""

import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path

from automatic import PROTECTED_FILES, infrastructure_fingerprints
from bundle import validate
from upgrade_protocol import NS_UID, TARGET_UID, digest


def prepare(bundle, preview, baseline_sha, expires_at, *, allow_future=False):
    validate(bundle)
    if not re.fullmatch(r"[a-f0-9]{40}", baseline_sha) or baseline_sha != bundle["source_sha"]:
        raise ValueError("Full reviewed main baseline required")
    plan = preview["plan"]
    if digest(plan) != preview["plan_sha256"] or plan["source_sha"] != bundle["source_sha"]:
        raise ValueError("Preview/bundle binding differs")
    until = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    if until.tzinfo is None or not now < until <= now + datetime.timedelta(days=90):
        raise ValueError("Explicit future expiry of at most 90 days required")
    root = Path(__file__).resolve().parents[2]
    value = {"format": 1, "enabled": False, "baseline_sha": baseline_sha, "expires_at": expires_at,
             "source_scope": {"mode": "future-compatible"} if allow_future else {"mode": "exact", "sha": baseline_sha},
             "namespace_uid": NS_UID, "target_namespace_uid": TARGET_UID,
             "schema_revision": bundle["schema_revision"], "keycloak_version": bundle["keycloak_version"],
             "flags": plan["before"]["flags"],
             "deployment_uids": {name: row["uid"] for name, row in plan["before"]["deployments"].items()},
             "infrastructure": infrastructure_fingerprints(plan["before"]["protected"]),
             "approved_files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in PROTECTED_FILES}}
    value["id"] = digest(value)
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "preview", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--baseline-sha", required=True)
    p.add_argument("--expires-at", required=True)
    p.add_argument("--allow-future-compatible", action="store_true", help="Prepare a disabled proposal authorizing future compatible sources; separate approval required")
    args = p.parse_args()
    value = prepare(json.loads(args.bundle.read_text()), json.loads(args.preview.read_text()), args.baseline_sha, args.expires_at,
                    allow_future=args.allow_future_compatible)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=2)
    print("DISABLED policy proposal only:", value["id"])
