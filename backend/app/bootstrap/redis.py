"""Pinned ephemeral Redis and native Celery workload for one reviewed Python source."""

from __future__ import annotations

import os
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.bootstrap.native_runtime import RUNTIME_SCRATCH_MOUNT

REDIS_IMAGE = "docker.io/library/redis@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
REDIS_PORT = 6379
REDIS_REQUIREMENTS = {"CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "QUEUE_NAMESPACE"}


class RedisBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    worker_target: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+:celery$", max_length=140)
    api_token_secret: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")

    def image_repository(self, registry: str) -> str:
        return f"{registry.rstrip('/')}/{self.repository}"

    def allows_image(self, image: str, registry: str) -> bool:
        return bool(re.fullmatch(
            re.escape(self.image_repository(registry)) + rf"(?::{self.source_sha}|@sha256:[0-9a-f]{{64}})",
            image,
        ))

    def environment(self) -> dict:
        return {
            "CELERY_BROKER_URL": f"redis://127.0.0.1:{REDIS_PORT}/0",
            "CELERY_RESULT_BACKEND": f"redis://127.0.0.1:{REDIS_PORT}/1",
            "QUEUE_NAMESPACE": f"q-{self.application_id.hex}",
        }

    def secret_env(self) -> list[dict]:
        if not self.api_token_secret:
            return []
        return [{
            "name": "API_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": self.api_token_secret, "key": "API_TOKEN"}},
        }]

    def worker_argv(self) -> list[str]:
        return ["celery", "-A", self.worker_target, "worker", "--loglevel=warning", "--concurrency=1"]

    def worker(self, image: str, config_name: str) -> dict:
        return {
            "name": "queue-worker",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": self.worker_argv(),
            "envFrom": [{"configMapRef": {"name": config_name}}],
            "volumeMounts": [RUNTIME_SCRATCH_MOUNT],
            "securityContext": self.security(10001),
            "resources": self.resources(),
        }

    def sidecar(self) -> dict:
        return {
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
        }

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


def configured_redis(application_id: str) -> RedisBinding | None:
    value = os.environ.get("DF_APPROVED_REDIS_BINDINGS")
    if not value:
        return None
    try:
        bindings = TypeAdapter(list[RedisBinding]).validate_json(value)
    except ValidationError:
        return None
    matches = [binding for binding in bindings if str(binding.application_id) == application_id]
    return matches[0] if len(matches) == 1 else None


def redis_for_source(assessment: dict, application_id: str) -> RedisBinding | None:
    binding = configured_redis(application_id)
    if not binding:
        return None
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
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
