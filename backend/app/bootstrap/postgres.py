"""An operator-provisioned, reference-only PostgreSQL assignment for one application."""

from __future__ import annotations

import os
import shlex
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

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
    migration_module: str | None = Field(default=None, pattern=r"^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)+$", max_length=160)
    runtime: Literal["python", "nodejs", "java", "ruby"] = "python"
    api_token: bool = False
    liveness_path: str = Field(pattern=r"^/[a-zA-Z0-9/_-]*$", max_length=128)

    ephemeral: bool = False
    managed: bool = False

    def postgres_env(self) -> list[dict]:
        variable, key = ("POSTGRES_PASSWORD", "password") if self.ephemeral else ("DATABASE_URL", "DATABASE_URL")
        return [{
            "name": variable,
            "valueFrom": {"secretKeyRef": {"name": self.secret_name, "key": key}},
        }]

    def secret_env(self) -> list[dict]:
        extra = (["SECRET_KEY_BASE"] if self.runtime == "ruby" else []) + (["API_TOKEN"] if self.api_token else [])
        return self.postgres_env() + [
            {"name": name, "valueFrom": {"secretKeyRef": {"name": self.secret_name, "key": name}}}
            for name in extra
        ]

    def migration(self, start: list[str]) -> list[str]:
        if self.runtime == "python":
            return ["python", "-m", self.migration_module] if self.migration_module else []
        if self.runtime == "java":
            return [*start, "migrate"]
        if self.runtime == "nodejs":
            return ["npm", "run", "migrate"]
        return ["bundle", "exec", "rails", "db:migrate"]

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
                *self.postgres_env(),
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


def configured_postgres(application_id: str) -> PostgresBinding | None:
    legacy = os.environ.get("DF_APPROVED_POSTGRES_BINDING")
    catalog = os.environ.get("DF_APPROVED_POSTGRES_BINDINGS", "[]")
    try:
        bindings = TypeAdapter(list[PostgresBinding]).validate_json(catalog)
        if legacy:
            bindings.append(PostgresBinding.model_validate_json(legacy))
    except ValidationError:
        return None
    matches = [binding for binding in bindings if str(binding.application_id) == application_id]
    return matches[0] if len(matches) == 1 else None


def postgres_for_source(assessment: dict, application_id: str) -> PostgresBinding | None:
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
    binding = configured_postgres(application_id)
    if not binding and assessment.get("review", {}).get("runtime") == "ruby":
        from app.bootstrap.managed_rails import managed_binding, managed_profile

        profile = managed_profile()
        if profile and slug == profile.repository and source.get("commit_sha") == profile.source_sha:
            binding = managed_binding(application_id, profile.source_sha)
    if not binding:
        return None
    allowed_dependencies = {
        "python": {"psycopg"}, "nodejs": {"postgresql"}, "ruby": {"postgresql"},
        "java": {"spring-jdbc", "postgresql", "flyway-core", "flyway-database-postgresql"},
    }
    required = set(assessment.get("configuration", {}).get("required_variables", []))
    expected_secrets = {"DATABASE_URL"} | ({"API_TOKEN"} if binding.api_token else set())
    if binding.runtime == "ruby":
        expected_secrets.add("SECRET_KEY_BASE")
    if (
        assessment.get("review", {}).get("runtime") != binding.runtime
        or slug != binding.repository
        or source.get("commit_sha") != binding.source_sha
        or assessment.get("application", {}).get("name") != binding.application_name
        or application_id != str(binding.application_id)
        or (required - {"DATABASE_URL"} if binding.runtime == "python" else required) != (
            expected_secrets - {"DATABASE_URL"} if binding.runtime == "python" else expected_secrets
        )
        or set(assessment.get("dependencies", {}).get("detected_services", [])) != allowed_dependencies[binding.runtime]
    ):
        return None
    if binding.runtime == "python":
        return binding if binding.migration_module else None
    start = (assessment.get("runtime", {}).get("command") or "").split()
    return binding if " ".join(binding.migration(start)) == assessment.get("workflow", {}).get("migration_command") else None


def migrated_start(start: list[str], migration: list[str], ephemeral: bool) -> list[str]:
    # Non-Python images use the system timeout and exec. A private generated
    # password uses the URI-safe alphabet. An external DATABASE_URL comes
    # entirely from the approved Secret and is left untouched.
    steps = []
    if ephemeral:
        steps.extend([
            'case "${POSTGRES_PASSWORD:-}" in ""|*[!A-Za-z0-9_-]*) exit 1;; esac',
            f'export DATABASE_URL="postgresql://{POSTGRES_DATABASE}:$POSTGRES_PASSWORD@127.0.0.1:{POSTGRES_PORT}/{POSTGRES_DATABASE}"',
            "unset POSTGRES_PASSWORD",
        ])
    steps.extend(["timeout 60 " + shlex.join(migration), "exec " + shlex.join(start)])
    return ["sh", "-ec", "; ".join(steps)]
