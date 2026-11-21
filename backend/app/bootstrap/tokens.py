"""Approved app-only bearer-token binding for a source-pinned service."""

import os
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class TokenBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    secret_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")

    def image_repository(self, registry: str) -> str:
        return f"{registry.rstrip('/')}/{self.repository}"

    def allows_image(self, image: str, registry: str) -> bool:
        return bool(re.fullmatch(
            re.escape(self.image_repository(registry)) + rf"(?::{self.source_sha}|@sha256:[0-9a-f]{{64}})",
            image,
        ))

    def secret_env(self) -> list[dict]:
        return [{
            "name": "API_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": self.secret_name, "key": "API_TOKEN"}},
        }]


def configured_token(application_id: str) -> TokenBinding | None:
    try:
        catalog = TypeAdapter(list[TokenBinding]).validate_json(os.getenv("DF_APPROVED_TOKEN_BINDINGS", "[]"))
    except ValidationError:
        return None
    matches = [binding for binding in catalog if str(binding.application_id) == application_id]
    return matches[0] if len(matches) == 1 else None


def token_for_source(assessment: dict, application_id: str) -> TokenBinding | None:
    binding = configured_token(application_id)
    if not binding:
        return None
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
    if (
        assessment.get("review", {}).get("runtime") != "nodejs"
        or slug != binding.repository
        or source.get("commit_sha") != binding.source_sha
        or assessment.get("application", {}).get("name") != binding.application_name
        or assessment.get("dependencies", {}).get("detected_services")
        or set(assessment.get("configuration", {}).get("required_variables", [])) != {"API_TOKEN"}
    ):
        return None
    return binding
