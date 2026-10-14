from dataclasses import dataclass
from typing import Protocol

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from app.config import settings


@dataclass(frozen=True)
class Identity:
    actor_id: str
    organization_id: str
    roles: frozenset[str]

    def require_write(self):
        if not self.roles.intersection({"developer", "deployer", "administrator"}):
            raise HTTPException(403, "Developer role required")

    def require_deploy(self):
        if not self.roles.intersection({"deployer", "administrator"}):
            raise HTTPException(403, "Deployer role required")


class IdentityProvider(Protocol):
    def authenticate(self, request: Request) -> Identity: ...


class DevelopmentIdentityProvider:
    def authenticate(self, request: Request) -> Identity:
        config = settings()
        return Identity(
            config.dev_actor_id, config.dev_organization_id, frozenset(config.dev_roles)
        )


class OIDCIdentityProvider:
    """Verify a signed access token; never trust browser-supplied role headers."""

    def __init__(self):
        config = settings()
        self.issuer = config.oidc_issuer
        self.audience = config.oidc_audience
        self.keys = PyJWKClient(config.oidc_jwks_url)

    def authenticate(self, request: Request) -> Identity:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Bearer token required")
        try:
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256", "ES256"],
                issuer=self.issuer, audience=self.audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (PyJWTError, ValueError):
            raise HTTPException(401, "Invalid access token") from None
        organization = claims.get("organization_id")
        roles = claims.get("roles")
        if not isinstance(organization, str) or not organization or not isinstance(roles, list):
            raise HTTPException(403, "Organization and roles are required")
        return Identity(str(claims["sub"]), organization, frozenset(
            role for role in roles if isinstance(role, str)
        ))


def get_identity(request: Request) -> Identity:
    identity = request.app.state.identity_provider.authenticate(request)
    if not identity.roles.intersection({"viewer", "developer", "deployer", "administrator"}):
        raise HTTPException(403, "A platform role is required")
    return identity
