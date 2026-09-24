import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.bootstrap.contracts import (
    BUILD_SPECIFICATION_SCHEMA_VERSION,
    DEPLOYMENT_INTENT_SCHEMA_VERSION,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateApplication(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$|^[a-z]$")
    repository_url: str = Field(max_length=512)
    revision: str = Field(default="main", min_length=1, max_length=255)

    @field_validator("repository_url")
    @classmethod
    def github_repository(cls, value: str) -> str:
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", value):
            raise ValueError("Use an HTTPS GitHub repository URL without credentials or query")
        return value.rstrip("/").removesuffix(".git")


class StartAnalysis(StrictModel):
    revision: str | None = Field(default=None, min_length=1, max_length=255)


class Finding(StrictModel):
    value: str | int | None
    source: Literal["mock", "unknown", "user"]


class Assessment(StrictModel):
    schema_version: Literal["infrastructure-assessment-v1"] = "infrastructure-assessment-v1"
    mode: Literal["fake"] = "fake"
    runtime: Finding
    port: Finding
    health_path: Finding
    environment_variable_names: list[str] = Field(default_factory=list, max_length=100)
    unknowns: list[str] = Field(default_factory=list, max_length=100)


class AssessmentPatch(StrictModel):
    expected_version: int = Field(ge=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    health_path: str | None = Field(default=None, pattern=r"^/[^\s]*$", max_length=255)
    question_answers: dict[str, str | int | bool] = Field(default_factory=dict, max_length=30)


class Exposure(StrictModel):
    kind: Literal["internal", "external"] = "internal"
    hostname: str | None = Field(default=None, max_length=253)
    path: str = Field(default="/", pattern=r"^/[^\s]*$", max_length=255)
    tls_secret_name: str | None = Field(default=None, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

    @model_validator(mode="after")
    def external_has_hostname(self):
        if self.kind == "external" and not self.hostname:
            raise ValueError("External exposure requires a hostname")
        return self


class SecretReference(StrictModel):
    environment_variable: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=128)
    secret_name: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    secret_key: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=253)


class ConnectionBinding(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$|^[a-z]$")
    kind: Literal["database", "cache", "queue", "service", "external"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "http", "https", "postgres", "redis", "amqp"]
    tls: Literal["disabled", "preferred", "required"] = "required"
    secret_references: list[SecretReference] = Field(default_factory=list, max_length=12)


class Resources(StrictModel):
    requests: dict[str, str] = Field(default_factory=lambda: {"cpu": "100m", "memory": "128Mi"})
    limits: dict[str, str] = Field(default_factory=lambda: {"cpu": "500m", "memory": "512Mi"})

    @field_validator("requests", "limits")
    @classmethod
    def cpu_and_memory_only(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"cpu", "memory"}:
            raise ValueError("Resources require exactly cpu and memory")
        if not re.fullmatch(r"(?:[1-9][0-9]*m|[1-9][0-9]*)", value["cpu"]):
            raise ValueError("Invalid Kubernetes CPU quantity")
        if not re.fullmatch(r"[1-9][0-9]*(?:Ki|Mi|Gi)", value["memory"]):
            raise ValueError("Invalid Kubernetes memory quantity")
        return value


class Connectivity(StrictModel):
    port: int = Field(ge=1, le=65535)
    health_path: str = Field(pattern=r"^/[^\s]*$", max_length=255)
    replicas: int = Field(default=1, ge=1, le=3)
    resources: Resources = Field(default_factory=Resources)
    environment: dict[str, str] = Field(default_factory=dict, max_length=50)
    connections: list[ConnectionBinding] = Field(default_factory=list, max_length=20)
    exposure: Exposure = Field(default_factory=Exposure)

    @field_validator("environment")
    @classmethod
    def safe_environment(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key):
                raise ValueError("Invalid environment variable name")
            if len(item) > 4096 or any(character in item for character in "\x00\r"):
                raise ValueError("Invalid environment variable value")
            if re.search(r"(?i)(secret|token|password|api[_-]?key|private[_-]?key)", key):
                raise ValueError("Sensitive configuration requires a secret reference")
        return value


class BuildSpecification(StrictModel):
    schema_version: Literal[BUILD_SPECIFICATION_SCHEMA_VERSION] = BUILD_SPECIFICATION_SCHEMA_VERSION
    branch: str = Field(min_length=1, max_length=255)
    image: str = Field(min_length=3, max_length=255)
    build_context: Literal["."] = "."
    install_command: list[str] = Field(min_length=1, max_length=8)
    test_command: list[str] | None = Field(default=None, max_length=8)
    build_command: list[str] | None = Field(default=None, max_length=8)
    start_command: list[str] = Field(min_length=1, max_length=8)

    @field_validator("branch")
    @classmethod
    def safe_branch(cls, value: str) -> str:
        if (
            value.startswith(("-", "."))
            or value.endswith(("/", "."))
            or any(part in value for part in ("..", "//", "@{", "\\", " "))
        ):
            raise ValueError("Invalid Git branch or revision")
        return value

    @field_validator("image")
    @classmethod
    def safe_image(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+", value):
            raise ValueError("Use a lowercase registry image name without a tag")
        return value


class DeploymentIntentWrite(StrictModel):
    schema_version: Literal[DEPLOYMENT_INTENT_SCHEMA_VERSION] = DEPLOYMENT_INTENT_SCHEMA_VERSION
    expected_version: int = Field(ge=0)
    analysis_run_id: str
    expected_assessment_version: int = Field(ge=1)
    environment_profile_id: Literal["local-k3s"] = "local-k3s"
    connectivity: Connectivity
    build: BuildSpecification


class CreateInfrastructurePlan(StrictModel):
    analysis_run_id: str
    expected_assessment_version: int = Field(ge=1)
    intent_version: int = Field(ge=1)
    environment_profile_id: Literal["local-k3s"] = "local-k3s"


class ApprovePlan(StrictModel):
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
