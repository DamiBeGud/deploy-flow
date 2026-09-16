import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
