"""Prepare a namespace-only GHCR pull Secret privately; never apply or print it."""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
from pathlib import Path

from prepare_secrets import private_path
from render import PULL_SECRET, resource


def prepare(output, username, token_file):
    output, token_file = private_path(output), private_path(token_file)
    if not username or any(c.isspace() for c in username):
        raise ValueError("A GHCR account name is required")
    if not token_file.is_file() or stat.S_IMODE(token_file.stat().st_mode) & 0o077:
        raise ValueError("Token must be an existing private file (0600 or stricter)")
    if not output.parent.is_dir() or stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise ValueError("Output parent must be private (0700)")
    token = token_file.read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("Token file must contain one credential")
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    config = json.dumps({"auths": {"ghcr.io": {"auth": auth}}}).encode()
    value = resource("Secret", PULL_SECRET, type="kubernetes.io/dockerconfigjson",
                     data={".dockerconfigjson": base64.b64encode(config).decode()})
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output, args.username, args.token_file)
    print("Prepared deployflow-ghcr outside Git; no cluster resources changed.")


if __name__ == "__main__":
    main()
