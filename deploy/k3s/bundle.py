"""Create/validate a public, digest-bound installation bundle. No network or secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from render import HOST, NAMESPACE, REVISION, render

STAGES = ("foundation", "migration", "identity-setup", "workloads", "ingress")


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def create(images, source_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source_sha must be a full Git commit SHA")
    stages = {stage: render(stage, images) for stage in STAGES}
    return {"format": 1, "source_sha": source_sha, "namespace": NAMESPACE,
            "host": HOST, "schema_revision": REVISION, "images": images,
            "stages": stages, "sha256": {stage: digest(value) for stage, value in stages.items()}}


def validate(value):
    # Re-render rather than trusting downloaded executable manifests or checksums.
    expected = create(value["images"], value["source_sha"])
    if value != expected:
        raise ValueError("Bundle differs from this checkout's reviewed renderer")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    for role in ("backend", "frontend", "keycloak"):
        parser.add_argument(f"--{role}-image", required=True)
    args = parser.parse_args()
    images = {role: getattr(args, f"{role}_image") for role in ("backend", "frontend", "keycloak")}
    value = create(images, args.source_sha)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "release.json").write_bytes(encoded(value))
    for stage in STAGES:
        (args.output / f"{stage}.json").write_bytes(encoded(value["stages"][stage]))
    print("Bundle SHA256:", digest(value))
    for stage in STAGES:
        print(stage, value["sha256"][stage])


if __name__ == "__main__":
    main()
