"""Call only the credential-free probe service, never an arbitrary URL."""

import httpx


class ExternalProbeError(ValueError):
    pass


def external_health(hostname: str, path: str, tls: bool) -> dict:
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                "http://external-probe:8000/probe",
                json={"hostname": hostname, "path": path, "tls": tls},
            )
        if response.status_code != 200:
            raise ExternalProbeError("external_probe_rejected")
        result = response.json()
        if result.get("status") not in {"passed", "pending"}:
            raise ExternalProbeError("external_probe_invalid")
        return result
    except ExternalProbeError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ExternalProbeError("external_probe_unavailable") from exc
