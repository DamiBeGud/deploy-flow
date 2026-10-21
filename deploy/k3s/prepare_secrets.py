"""Prepare new hosted credentials privately; never apply resources or print values."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import stat
from pathlib import Path

from render import NAMESPACE, ROOT, resource


def private_path(path):
    path = path.expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Credentials must stay outside the repository")
    return path


def prepare(output, github_key):
    output, github_key = private_path(output), private_path(github_key)
    if not github_key.is_file() or stat.S_IMODE(github_key.stat().st_mode) & 0o077:
        raise ValueError("GitHub key must be an existing private file (0600 or stricter)")
    key = github_key.read_bytes()
    if not key or b"PRIVATE KEY" not in key:
        raise ValueError("GitHub key is missing PEM data")
    # Require a new directory, so neither live credentials nor older preparation
    # can be overwritten. The caller also owns its private parent directory.
    if not output.parent.is_dir() or stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise ValueError("Output parent must already be a private directory (0700)")
    output.mkdir(mode=0o700, exist_ok=False)
    password, identity_password, redis_password = (secrets.token_urlsafe(36) for _ in range(3))
    data = {
        "deployflow-postgres": {"password": password},
        "deployflow-identity-postgres": {"password": identity_password},
        "deployflow-redis": {"password": redis_password},
        "deployflow-runtime": {
            "DF_DATABASE_URL": f"postgresql+psycopg://deployflow:{password}@postgres:5432/deployflow",
            "DF_REDIS_URL": f"redis://:{redis_password}@redis:6379/0",
        },
        "deployflow-github-key": {"private.pem": key.decode("ascii")},
        "deployflow-webhook": {"secret": secrets.token_urlsafe(48)},
        "deployflow-identity-bootstrap": {
            "admin-password": secrets.token_urlsafe(36),
            "user-password": secrets.token_urlsafe(36),
        },
    }
    for name, values in data.items():
        document = resource("Secret", name, type="Opaque", data={
            field: base64.b64encode(value.encode()).decode("ascii") for field, value in values.items()
        })
        descriptor = os.open(output / f"{name}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
    bootstrap = data["deployflow-identity-bootstrap"]
    descriptor = os.open(output / "initial-accounts.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(
            "Private initial credentials; do not commit or paste into logs/chat.\n"
            "Keycloak master temporary administrator: deployflow-bootstrap-admin\n"
            f"Temporary administrator password: {bootstrap['admin-password']}\n"
            "DeployFlow application operator: deployflow-local\n"
            f"Initial operator password: {bootstrap['user-password']}\n"
            "Change the operator password at first login. Create a permanent master\n"
            "administrator and remove the temporary administrator through a private\n"
            "admin connection after separate operational approval.\n"
        )
    return list(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-key", required=True, type=Path)
    args = parser.parse_args()
    try:
        names = prepare(args.output, args.github_key)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(f"Prepared {len(names)} private Secret files for {NAMESPACE}; no resources applied.")


if __name__ == "__main__":
    main()
