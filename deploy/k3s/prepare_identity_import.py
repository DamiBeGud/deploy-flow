"""Adapt an approved CLI realm/user export privately; never export or import live data."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import stat
from pathlib import Path

from prepare_secrets import private_path
from render import HOST, resource


def adapted_realm(original):
    realm = copy.deepcopy(original)
    if not isinstance(realm, dict):
        raise TypeError("Realm export must be a JSON object")
    if not realm.get("id"):
        raise ValueError("A full CLI export with realm identities is required")
    if not isinstance(realm["id"], str):
        raise TypeError("Realm identity must be a string")
    name = realm.get("realm")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Unsupported realm identity")
    users = realm.get("users")
    if not isinstance(users, list) or (name in {"master", "deployflow-local"} and not users):
        raise ValueError("Full inline user export is required; use --users realm_file")
    seen = set()
    for user in users:
        if not isinstance(user, dict) or not user.get("id") or not user.get("username") or user["id"] in seen:
            raise ValueError("Export contains missing or duplicated user identities")
        seen.add(user["id"])
        external = user.get("serviceAccountClientId") or user.get("federationLink") or user.get("federatedIdentities")
        if user.get("enabled") and not external and not user.get("credentials"):
            raise ValueError("Enabled local accounts require a credential-bearing CLI export")
    if name in {"master", "deployflow-local"}:
        realm["sslRequired"] = "external"
        attributes = realm.get("attributes") or {}
        if "frontendUrl" in attributes:
            realm["attributes"]["frontendUrl"] = f"https://{HOST}/identity"
    if name == "deployflow-local":
        clients = [value for value in realm.get("clients", []) if value.get("clientId") == "deployflow-local-ui"]
        if len(clients) != 1:
            raise ValueError("The existing DeployFlow browser client must be present exactly once")
        browser = clients[0]
        if (not browser.get("publicClient") or not browser.get("standardFlowEnabled")
                or browser.get("directAccessGrantsEnabled")
                or browser.get("attributes", {}).get("pkce.code.challenge.method") != "S256"):
            raise ValueError("Existing browser-client security settings need review")
        browser["redirectUris"] = [f"https://{HOST}/auth/callback"]
        browser["webOrigins"] = [f"https://{HOST}"]
    return realm


def prepare_identity(source, output):
    source, output = private_path(source), private_path(output)
    if not source.is_dir() or stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise ValueError("Export directory must be private (0700)")
    if output.exists() or not output.parent.is_dir() or stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise ValueError("Use a new output file in a private directory (0700)")
    documents, names, count = {}, set(), 0
    paths = sorted(source.glob("*.json"))
    for path in paths:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Export files must be regular private files (0600 or stricter)")
        try:
            original = json.loads(path.read_text())
            realm = adapted_realm(original)
        except (json.JSONDecodeError, UnicodeError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid realm export; no private data was printed") from exc
        name = realm["realm"]
        if path.name != f"{name}-realm.json" or name in names:
            raise ValueError("Full inline-user realm-file names are required")
        names.add(name)
        count += len(realm["users"])
        documents[path.name] = base64.b64encode(json.dumps(realm).encode()).decode("ascii")
    if not {"master", "deployflow-local"}.issubset(names):
        raise ValueError("Export both master and deployflow-local to preserve administrator and application accounts")
    payload = json.dumps(resource("Secret", "deployflow-identity-import", type="Opaque", data=documents), indent=2)
    if len(payload.encode()) > 900 * 1024:
        raise ValueError("Export exceeds the Secret budget; prepare a separately reviewed private-volume import")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(payload + "\n")
    return {"realms": len(names), "users": count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = prepare_identity(args.export_directory, args.output)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(f"Prepared private identity import for {result['realms']} realms / {result['users']} users; no live action.")


if __name__ == "__main__":
    main()
