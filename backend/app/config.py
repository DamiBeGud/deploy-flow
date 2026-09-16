from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
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
    job_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_attempts: int = Field(default=3, ge=1, le=5)
    redispatch_seconds: int = Field(default=10, ge=1)
    queue_timeout_seconds: int = Field(default=3600, ge=1)

    @model_validator(mode="after")
    def local_auth_only(self):
        if self.environment == "production":
            raise ValueError("Real authentication must be implemented before production use")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
