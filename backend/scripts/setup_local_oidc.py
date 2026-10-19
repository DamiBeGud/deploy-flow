"""Create local Keycloak bootstrap passwords outside the repository.

The file is intentionally not regenerated on repeat runs: Keycloak imports
the realm only once, and replacing passwords here would lock the user out.
"""

import os
import secrets
import stat
from pathlib import Path


def main() -> None:
    directory = Path.home() / ".config" / "deployflow" / "local-oidc"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise SystemExit(f"Refusing to write credentials into a non-private directory: {directory}")
    target = directory / "keycloak.env"
    if target.exists():
        if stat.S_IMODE(target.stat().st_mode) & 0o077:
            raise SystemExit(f"Existing credential file is not private: {target}")
        print(f"Existing local OIDC credentials preserved: {target}")
        return
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(f"KC_BOOTSTRAP_ADMIN_PASSWORD={secrets.token_urlsafe(30)}\n")
        output.write(f"DF_LOCAL_OIDC_USER_PASSWORD={secrets.token_urlsafe(30)}\n")
    print(f"Created private local OIDC credentials: {target}")


if __name__ == "__main__":
    main()
