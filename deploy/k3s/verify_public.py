"""Read-only HTTPS checks after an approved hostname switch. Does not log in."""

import json
from urllib.request import urlopen

from render import HOST, ISSUER


def main():
    origin = "https://" + HOST
    with urlopen(origin + "/", timeout=20) as response:
        if response.status != 200 or "text/html" not in response.headers.get("Content-Type", ""):
            raise ValueError("UI is not served as HTML")
    for path in ("/api/v1/auth/config", "/identity/realms/deployflow-local/.well-known/openid-configuration"):
        with urlopen(origin + path, timeout=20) as response:
            if response.status != 200 or "application/json" not in response.headers.get("Content-Type", ""):
                raise ValueError("Expected real JSON endpoint, not the mockup HTML fallback: " + path)
            value = json.load(response)
        if path.startswith("/identity/") and value.get("issuer") != ISSUER:
            raise ValueError("OIDC issuer mismatch")
        if path.startswith("/api/") and value.get("mode") != "oidc":
            raise ValueError("API does not advertise OIDC")
        print("HTTPS JSON endpoint verified:", path)
    print("TLS/UI/auth-discovery passed; first login, PKCE, API/SSE and signed webhook remain separate acceptance checks")


if __name__ == "__main__":
    main()
