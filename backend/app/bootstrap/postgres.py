"""An operator-provisioned, reference-only PostgreSQL assignment for one application."""

from __future__ import annotations

import os
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PostgresBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    secret_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    migration_module: str = Field(pattern=r"^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)+$", max_length=160)
    liveness_path: str = Field(pattern=r"^/[a-zA-Z0-9/_-]*$", max_length=128)

    def secret_env(self) -> list[dict]:
        return [{
            "name": "DATABASE_URL",
            "valueFrom": {"secretKeyRef": {"name": self.secret_name, "key": "DATABASE_URL"}},
        }]

    def image_repository(self, registry: str) -> str:
        return f"{registry.rstrip('/')}/{self.repository}"


def configured_postgres() -> PostgresBinding | None:
    value = os.environ.get("DF_APPROVED_POSTGRES_BINDING")
    if not value:
        return None
    try:
        return PostgresBinding.model_validate_json(value)
    except ValidationError:
        return None


def postgres_for_source(assessment: dict, application_id: str) -> PostgresBinding | None:
    binding = configured_postgres()
    if not binding:
        return None
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
    if (
        assessment.get("review", {}).get("runtime") != "python"
        or slug != binding.repository
        or source.get("commit_sha") != binding.source_sha
        or assessment.get("application", {}).get("name") != binding.application_name
        or application_id != str(binding.application_id)
        or set(assessment.get("configuration", {}).get("required_variables", [])) - {"DATABASE_URL"}
        or set(assessment.get("dependencies", {}).get("detected_services", [])) != {"psycopg"}
    ):
        return None
    return binding
