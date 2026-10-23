"""Independent HTTPS acceptance, run on the GitHub runner, never via server hairpin."""

import argparse
import json
import time
import urllib.error
import urllib.request

HOST = "https://deployflow.damibegud.cloud"
PATHS = ("/", "/api/v1/auth/config", "/identity/realms/deployflow-local/.well-known/openid-configuration")


def check_once():
    result = {}
    for path in PATHS:
        # Default TLS verification stays enabled. Redirects must not hide a
        # different origin, login page, or a redirected API response.
        with urllib.request.urlopen(HOST + path, timeout=10) as response:
            expected = "text/html" if path == "/" else "application/json"
            if response.status != 200 or response.url != HOST + path or expected not in response.headers.get("Content-Type", ""):
                raise ValueError("public_endpoint_invalid")
            if path != "/":
                value = json.loads(response.read(1024 * 1024))
                if path.startswith("/api") and value.get("mode") != "oidc":
                    raise ValueError("public_auth_mode_invalid")
                if path.startswith("/identity") and value.get("issuer") != HOST + "/identity/realms/deployflow-local":
                    raise ValueError("public_issuer_invalid")
            result[path] = 200
    return result


def verify(*, attempts=6, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            return check_once()
        except (OSError, ValueError, urllib.error.URLError):
            if attempt + 1 == attempts:
                raise ValueError("public_verification_failed; cluster state is retained for reviewed recovery") from None
            sleep(min(5 * (attempt + 1), 15))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps({"vantage": "independent-github-runner", "checks": verify()}))
