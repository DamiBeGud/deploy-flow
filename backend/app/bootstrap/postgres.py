"""An operator-provisioned, reference-only PostgreSQL assignment for one application."""

from __future__ import annotations

import os
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

POSTGRES_IMAGE = "docker.io/library/postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
POSTGRES_PORT = 5432
POSTGRES_DATABASE = "application"
POSTGRES_STORAGE = (
    ("postgres-data", "/var/lib/postgresql/data", "256Mi"),
    ("postgres-tmp", "/tmp", "32Mi"),
    ("postgres-run", "/var/run/postgresql", "16Mi"),
)


def postgres_volumes() -> list[dict]:
    return [{"name": name, "emptyDir": {"sizeLimit": size}} for name, _, size in POSTGRES_STORAGE]


class PostgresBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    secret_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    migration_module: str = Field(pattern=r"^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)+$", max_length=160)
    liveness_path: str = Field(pattern=r"^/[a-zA-Z0-9/_-]*$", max_length=128)

    ephemeral: bool = False

    def secret_env(self) -> list[dict]:
        variable, key = ("POSTGRES_PASSWORD", "password") if self.ephemeral else ("DATABASE_URL", "DATABASE_URL")
        return [{
            "name": variable,
            "valueFrom": {"secretKeyRef": {"name": self.secret_name, "key": key}},
        }]

    def database_sidecar(self) -> dict:
        return {
            "name": "postgres",
            "image": POSTGRES_IMAGE,
            "imagePullPolicy": "IfNotPresent",
            "restartPolicy": "Always",
            "args": ["postgres", "-c", "listen_addresses=127.0.0.1", "-p", str(POSTGRES_PORT)],
            "env": [
                {"name": "POSTGRES_DB", "value": POSTGRES_DATABASE},
                {"name": "POSTGRES_USER", "value": POSTGRES_DATABASE},
                {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"},
                *self.secret_env(),
            ],
            "securityContext": {
                "runAsNonRoot": True, "runAsUser": 70, "runAsGroup": 70,
                "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
            "volumeMounts": [
                {"name": name, "mountPath": path} for name, path, _ in POSTGRES_STORAGE
            ],
            "startupProbe": {
                "exec": {"command": [
                    "pg_isready", "-h", "127.0.0.1", "-p", str(POSTGRES_PORT),
                    "-U", POSTGRES_DATABASE, "-d", POSTGRES_DATABASE,
                ]},
                "periodSeconds": 2, "failureThreshold": 30,
            },
        }

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
