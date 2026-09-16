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
    auth_mode: Literal["development"] = "development"
    dev_actor_id: str = "local-developer"
    dev_organization_id: str = "local-organization"
    dev_roles: list[str] = ["developer"]
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    fake_step_seconds: float = Field(default=1, ge=0, le=10)
    analysis_provider: Literal["github", "fake"] = "github"
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    github_app_id: str | None = None
    github_installation_id: int | None = None
    github_private_key_path: Path | None = None
    repository_archive_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    repository_extracted_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    repository_file_count: int = Field(default=10_000, ge=1)
    repository_path_length: int = Field(default=512, ge=32)
    repository_read_bytes: int = Field(default=64 * 1024, ge=1024)
    repository_result_budget: int = Field(default=2 * 1024 * 1024, ge=1024)
    analysis_attempt_seconds: int = Field(default=300, ge=10, le=1800)
    job_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_attempts: int = Field(default=3, ge=1, le=5)
    redispatch_seconds: int = Field(default=10, ge=1)
    queue_timeout_seconds: int = Field(default=3600, ge=1)

    @field_validator("github_app_id", "github_installation_id", "github_private_key_path", mode="before")
    @classmethod
    def blank_optional_settings_are_none(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def local_auth_only(self):
        if self.environment == "production":
            raise ValueError("Real authentication must be implemented before production use")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
