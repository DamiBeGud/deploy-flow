from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request

from app.config import settings


@dataclass(frozen=True)
class Identity:
    actor_id: str
    organization_id: str
    roles: frozenset[str]

    def require_write(self):
        if not self.roles.intersection({"developer", "deployer", "administrator"}):
            raise HTTPException(403, "Developer role required")


class IdentityProvider(Protocol):
    def authenticate(self, request: Request) -> Identity: ...


class DevelopmentIdentityProvider:
    def authenticate(self, request: Request) -> Identity:
        config = settings()
        return Identity(
            config.dev_actor_id, config.dev_organization_id, frozenset(config.dev_roles)
        )


def get_identity(request: Request) -> Identity:
    identity = request.app.state.identity_provider.authenticate(request)
    if not identity.roles.intersection({"viewer", "developer", "deployer", "administrator"}):
        raise HTTPException(403, "A platform role is required")
    return identity
