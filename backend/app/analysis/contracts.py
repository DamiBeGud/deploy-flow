"""Versioned, provider-independent analysis documents."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


FindingStatus = Literal["explicit", "inferred", "heuristic", "unknown", "user_supplied"]
Completion = Literal["complete", "partial"]


class RepositoryIdentity(StrictModel):
    url: str
    owner: str | None = None
    name: str | None = None
    revision: str | None = None
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class EvidenceDocument(StrictModel):
    id: str = Field(pattern=r"^ev-[A-Za-z0-9_-]+$")
    file_path: str = Field(min_length=1, max_length=1024)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    excerpt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector: str = Field(min_length=1, max_length=128)


class FindingV2(StrictModel):
    id: str = Field(pattern=r"^finding-[A-Za-z0-9_-]+$")
    category: str = Field(min_length=1, max_length=128)
    value: str | int | float | bool | None
    status: FindingStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class DraftFinding(StrictModel):
    category: str = Field(min_length=1, max_length=128)
    value: str | int | float | bool | None
    status: FindingStatus
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)


class AssessmentQuestion(StrictModel):
    id: str = Field(pattern=r"^question-[A-Za-z0-9_-]+$")
    field_path: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)
    required: bool
    answer_type: Literal["text", "integer", "boolean", "single_choice"]
    choices: list[str] = Field(default_factory=list, max_length=12)


class DraftQuestion(StrictModel):
    field_path: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)
    required: bool
    answer_type: Literal["text", "integer", "boolean", "single_choice"]
    choices: list[str] = Field(default_factory=list, max_length=12)


class AssessmentDraftV2(StrictModel):
    """The model may only add claims and questions; the host owns base facts."""

    summary: str = Field(min_length=1, max_length=4000)
    findings: list[DraftFinding] = Field(default_factory=list, max_length=100)
    questions: list[DraftQuestion] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=50)
    unknowns: list[str] = Field(default_factory=list, max_length=50)


class InfrastructureAssessmentV2(StrictModel):
    schema_version: Literal["infrastructure-assessment-v2"] = "infrastructure-assessment-v2"
    mode: Literal["deterministic", "agent"]
    completion: Completion
    incomplete_reason: str | None = None
    repository: RepositoryIdentity
    application: dict
    detected_stack: dict
    build: dict
    runtime: dict
    network: dict
    dependencies: dict
    configuration: dict
    storage: list = Field(default_factory=list)
    health_checks: list[str] = Field(default_factory=list)
    workflow: dict
    bootstrap: dict
    review: dict
    risks: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    questions: list[AssessmentQuestion] = Field(default_factory=list)
    findings: list[FindingV2] = Field(default_factory=list)
    evidence: list[EvidenceDocument] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=4000)


def assessment_v2_schema() -> dict:
    """JSON Schema sent to the provider for its deliberately narrow draft."""
    return AssessmentDraftV2.model_json_schema()
