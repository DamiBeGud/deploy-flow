from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DF_", extra="ignore")

    database_url: str = "postgresql+psycopg://deployflow:local-only@localhost:5432/deployflow"
    redis_url: str = "redis://localhost:6379/0"
    environment: Literal["local", "test", "production"] = "local"
    auth_mode: Literal["development", "oidc"] = "development"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_client_id: str | None = None
    deployment_execution_enabled: bool = False
    deployment_cleanup_enabled: bool = False
    dev_actor_id: str = "local-developer"
    dev_organization_id: str = "local-organization"
    dev_roles: list[str] = ["developer"]
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    fake_step_seconds: float = Field(default=1, ge=0, le=10)
    # `analysis_provider` is kept as a short-lived compatibility bridge for
    # Phase 2 `.env` files. New configuration uses analysis_mode instead.
    analysis_provider: Literal["github", "fake"] | None = None
    analysis_mode: Literal["fake", "deterministic", "agent"] | None = None
    model_provider: Literal["deepseek"] = "deepseek"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_reasoning_effort: Literal["none", "low", "high", "max"] = "high"
    ghcr_registry: str | None = None
    ghcr_image: str | None = None
    kube_reader_mcp_url: str | None = None
    kube_validator_mcp_url: str | None = None
    node_base_image: str = "node:20-bookworm-slim"
    agent_max_turns: int = Field(default=8, ge=1, le=32)
    agent_max_tool_calls: int = Field(default=12, ge=1, le=64)
    agent_max_input_tokens: int = Field(default=120_000, ge=1_000)
    agent_max_output_tokens: int = Field(default=32_000, ge=1_000)
    agent_response_max_tokens: int = Field(default=16_000, ge=1_000)
    agent_tool_output_bytes: int = Field(default=256 * 1024, ge=1024)
    agent_wall_seconds: int = Field(default=180, ge=10, le=1800)
    agent_provider_timeout_seconds: int = Field(default=60, ge=5, le=300)
    agent_tool_timeout_seconds: int = Field(default=20, ge=1, le=120)
    skill_context_chars: int = Field(default=40_000, ge=1_000)
    skill_directory: Path = Path("/srv/backend/skills")
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    github_app_id: str | None = None
    github_installation_id: int | None = None
    github_private_key_path: Path | None = None
    github_webhook_secret: str | None = None
    repository_archive_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    repository_extracted_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    repository_file_count: int = Field(default=10_000, ge=1)
    repository_path_length: int = Field(default=512, ge=32)
    repository_read_bytes: int = Field(default=64 * 1024, ge=1024)
    repository_result_budget: int = Field(default=2 * 1024 * 1024, ge=1024)
    analysis_attempt_seconds: int = Field(default=300, ge=10, le=1800)
    # An agent turn can wait on a model request and several bounded MCP calls.
    # Keep the durable claim valid for the complete configured attempt window.
    job_timeout_seconds: int = Field(default=300, ge=1, le=1800)
    max_attempts: int = Field(default=3, ge=1, le=5)
    redispatch_seconds: int = Field(default=10, ge=1)
    queue_timeout_seconds: int = Field(default=3600, ge=1)
    delivery_max_attempts: int = Field(default=5, ge=1, le=10)
    delivery_lease_seconds: int = Field(default=300, ge=30, le=1800)
    delivery_retry_seconds: int = Field(default=30, ge=1, le=3600)
    execution_lease_seconds: int = Field(default=300, ge=30, le=1800)
    execution_deadline_seconds: int = Field(default=600, ge=60, le=3600)
    execution_retry_seconds: int = Field(default=15, ge=1, le=300)
    execution_max_attempts: int = Field(default=5, ge=1, le=20)

    @field_validator(
        "github_app_id",
        "github_installation_id",
        "github_private_key_path",
        "github_webhook_secret",
        "deepseek_api_key",
        "analysis_provider",
        "analysis_mode",
        "ghcr_registry",
        "ghcr_image",
        "kube_reader_mcp_url",
        "kube_validator_mcp_url",
        "oidc_issuer",
        "oidc_audience",
        "oidc_jwks_url",
        "oidc_authorization_endpoint",
        "oidc_token_endpoint",
        "oidc_client_id",
        mode="before",
    )
    @classmethod
    def blank_optional_settings_are_none(cls, value):
        return None if value == "" else value

    @property
    def resolved_analysis_mode(self) -> Literal["fake", "deterministic", "agent"]:
        if self.analysis_mode:
            return self.analysis_mode
        if self.analysis_provider == "fake":
            return "fake"
        return "deterministic"

    @model_validator(mode="after")
    def local_auth_only(self):
        if self.auth_mode == "oidc":
            issuer = "http://localhost:8180/realms/deployflow-local"
            local_provider = self.environment == "local" and (
                self.oidc_issuer == issuer
                and self.oidc_jwks_url == "http://keycloak:8080/realms/deployflow-local/protocol/openid-connect/certs"
                and self.oidc_authorization_endpoint == f"{issuer}/protocol/openid-connect/auth"
                and self.oidc_token_endpoint == f"{issuer}/protocol/openid-connect/token"
            )
            https_provider = all(
                value and value.startswith("https://")
                for value in (
                    self.oidc_issuer, self.oidc_jwks_url,
                    self.oidc_authorization_endpoint, self.oidc_token_endpoint,
                )
            )
            if not (self.oidc_audience and self.oidc_client_id and (local_provider or https_provider)):
                raise ValueError("OIDC requires a complete HTTPS provider or the local Keycloak profile")
        if self.environment == "production" and self.auth_mode != "oidc":
            raise ValueError("OIDC authentication is required in production")
        if self.deployment_execution_enabled and self.auth_mode != "oidc":
            raise ValueError("Deployment execution requires OIDC authentication")
        if self.deployment_cleanup_enabled and not self.deployment_execution_enabled:
            raise ValueError("Deployment cleanup requires deployment execution")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
