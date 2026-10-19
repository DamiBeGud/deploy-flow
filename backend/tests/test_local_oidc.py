"""Only the exact loopback Keycloak endpoints may use HTTP for local testing."""

import pytest
from pydantic import ValidationError

from app.config import Settings


LOCAL = {
    "oidc_issuer": "http://localhost:8180/realms/deployflow-local",
    "oidc_audience": "deployflow-api",
    "oidc_jwks_url": "http://keycloak:8080/realms/deployflow-local/protocol/openid-connect/certs",
    "oidc_authorization_endpoint": "http://localhost:8180/realms/deployflow-local/protocol/openid-connect/auth",
    "oidc_token_endpoint": "http://localhost:8180/realms/deployflow-local/protocol/openid-connect/token",
    "oidc_client_id": "deployflow-local-ui",
}


def test_exact_local_keycloak_profile_is_allowed():
    config = Settings(_env_file=None, environment="local", auth_mode="oidc", **LOCAL)
    assert config.oidc_audience == "deployflow-api"


@pytest.mark.parametrize("change", [
    {"environment": "production"},
    {"oidc_jwks_url": "http://example.org/certs"},
    {"oidc_issuer": "http://localhost:8181/realms/deployflow-local"},
])
def test_http_oidc_is_not_accepted_outside_exact_local_profile(change):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **({"environment": "local", "auth_mode": "oidc"} | LOCAL | change))
