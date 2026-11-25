"""Pinned Redis connections and native Celery workloads for approved Python sources."""

from __future__ import annotations

import os
import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.bootstrap.native_runtime import RUNTIME_SCRATCH_MOUNT

REDIS_IMAGE = "docker.io/library/redis@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
REDIS_PORT = 6379
REDIS_REQUIREMENTS = {"CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "QUEUE_NAMESPACE"}
REDIS_SOURCE_KEY = "DEPLOYFLOW_REDIS_SOURCE_SHA"
REDIS_RESERVED_ENVIRONMENT = REDIS_REQUIREMENTS | {REDIS_SOURCE_KEY}
Repository = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")]
WorkerTarget = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+:celery$", max_length=140)]
_PILOT_REPOSITORIES = TypeAdapter(dict[Repository, WorkerTarget])


class RedisBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: Repository
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    worker_target: WorkerTarget
    api_token_secret: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    redis_connection_secret: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")

    def image_repository(self, registry: str) -> str:
        return f"{registry.rstrip('/')}/{self.repository}"

    def allows_image(self, image: str, registry: str) -> bool:
        return bool(re.fullmatch(
            re.escape(self.image_repository(registry)) + rf"(?::{self.source_sha}|@sha256:[0-9a-f]{{64}})",
            image,
        ))

    def environment(self) -> dict:
        environment = {"QUEUE_NAMESPACE": f"q-{self.application_id.hex}"}
        if self.redis_connection_secret:
            return environment
        return {
            "CELERY_BROKER_URL": f"redis://127.0.0.1:{REDIS_PORT}/0",
            "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{REDIS_PORT}/1",
            **environment,
        }

    def connection_env(self) -> list[dict]:
        if not self.redis_connection_secret:
            return []
        return [
            {
                "name": key,
                "valueFrom": {"secretKeyRef": {"name": self.redis_connection_secret, "key": key}},
            }
            for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
        ]

    def secret_env(self) -> list[dict]:
        environment = self.connection_env()
        if self.api_token_secret:
            environment.append({
                "name": "API_TOKEN",
                "valueFrom": {"secretKeyRef": {"name": self.api_token_secret, "key": "API_TOKEN"}},
            })
        return environment

    def worker_argv(self) -> list[str]:
        return ["celery", "-A", self.worker_target, "worker", "--loglevel=warning", "--concurrency=1"]

    def worker(self, image: str, config_name: str) -> dict:
        worker = {
            "name": "queue-worker",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": self.worker_argv(),
            "envFrom": [{"configMapRef": {"name": config_name}}],
            "volumeMounts": [RUNTIME_SCRATCH_MOUNT],
            "securityContext": self.security(10001),
            "resources": self.resources(),
        }
        if environment := self.connection_env():
            worker["env"] = environment
        return worker

    def init_containers(self) -> list[dict]:
        if self.redis_connection_secret:
            return []
        return [{
            "name": "redis",
            "image": REDIS_IMAGE,
            "imagePullPolicy": "IfNotPresent",
            "restartPolicy": "Always",
            "args": [
                "redis-server", "--bind", "127.0.0.1", "--port", str(REDIS_PORT),
                "--protected-mode", "yes", "--save", "", "--appendonly", "no", "--maxmemory", "128mb",
            ],
            "securityContext": self.security(999),
            "resources": self.resources(),
            "startupProbe": {
                "exec": {"command": ["redis-cli", "-h", "127.0.0.1", "-p", str(REDIS_PORT), "PING"]},
                "periodSeconds": 2, "failureThreshold": 30,
            },
        }]

    @staticmethod
    def security(uid: int) -> dict:
        return {
            "runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid,
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        }

    @staticmethod
    def resources() -> dict:
        return {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        }


class ManagedRedisBinding(RedisBinding):
    """A credential-free sidecar for an explicitly approved repository."""

    def environment(self) -> dict:
        return {**super().environment(), REDIS_SOURCE_KEY: self.source_sha}


def _explicit_bindings(application_id: str) -> list[RedisBinding] | None:
    try:
        bindings = TypeAdapter(list[RedisBinding]).validate_json(
            os.environ.get("DF_APPROVED_REDIS_BINDINGS") or "[]",
        )
    except ValidationError:
        return None
    return [binding for binding in bindings if str(binding.application_id) == application_id]


def _managed_binding(application_id: str, name: str, repository: str, source_sha: str) -> ManagedRedisBinding | None:
    try:
        allowed = _PILOT_REPOSITORIES.validate_json(os.environ.get("DF_REDIS_PILOT_REPOSITORIES") or "{}")
        worker = allowed.get(repository)
        if not worker:
            return None
        return ManagedRedisBinding(
            application_id=application_id, application_name=name, repository=repository,
            source_sha=source_sha, worker_target=worker,
        )
    except ValidationError:
        return None


def redis_for_manifest(
    application_id: str, *, name: str, image: str, registry: str, environment: dict,
) -> RedisBinding | None:
    explicit = _explicit_bindings(application_id)
    if explicit != []:
        return explicit[0] if explicit and len(explicit) == 1 else None
    prefix = registry.rstrip("/") + "/"
    source_sha = environment.get(REDIS_SOURCE_KEY)
    if not image.startswith(prefix) or not isinstance(source_sha, str):
        return None
    repository = re.split(r"[:@]", image.removeprefix(prefix), maxsplit=1)[0]
    return _managed_binding(application_id, name, repository, source_sha)


def redis_for_source(assessment: dict, application_id: str) -> RedisBinding | None:
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
    explicit = _explicit_bindings(application_id)
    if explicit == []:
        binding = _managed_binding(
            application_id, assessment.get("application", {}).get("name", ""),
            slug, source.get("commit_sha", ""),
        )
    else:
        binding = explicit[0] if explicit and len(explicit) == 1 else None
    if not binding:
        return None
    required = set(assessment.get("configuration", {}).get("required_variables", []))
    if (
        assessment.get("review", {}).get("runtime") != "python"
        or assessment.get("review", {}).get("liveness_path") != "/health/live"
        or slug != binding.repository
        or source.get("commit_sha") != binding.source_sha
        or assessment.get("application", {}).get("name") != binding.application_name
        or set(assessment.get("dependencies", {}).get("detected_services", [])) != {"celery", "redis"}
        or assessment.get("workflow", {}).get("worker_target") != binding.worker_target
        or required != REDIS_REQUIREMENTS | ({"API_TOKEN"} if binding.api_token_secret else set())
    ):
        return None
    return binding
