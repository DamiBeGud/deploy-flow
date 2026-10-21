from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(63))
    repository_url: Mapped[str] = mapped_column(String(512))
    revision: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[str] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    analysis_mode: Mapped[str] = mapped_column(String(32), default="fake")
    incomplete_reason: Mapped[str | None] = mapped_column(String(64))
    reproducibility_json: Mapped[dict | None] = mapped_column(JSONB)
    scanner_facts_json: Mapped[dict | None] = mapped_column(JSONB)
    model_provider: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    agent_turns: Mapped[int] = mapped_column(default=0)
    agent_tool_calls: Mapped[int] = mapped_column(default=0)
    agent_input_tokens: Mapped[int] = mapped_column(default=0)
    agent_output_tokens: Mapped[int] = mapped_column(default=0)
    agent_tool_output_bytes: Mapped[int] = mapped_column(default=0)
    agent_latency_ms: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(default=0)
    event_sequence: Mapped[int] = mapped_column(default=0)
    assessment_version: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentVersion(Base):
    __tablename__ = "assessment_versions"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    document: Mapped[dict] = mapped_column(JSONB)
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    # IDs are ordered within one run. Allocated while holding that run's row lock,
    # so a later event cannot commit before an earlier event in the same stream.
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_items_run_key", "run_id", "evidence_key", unique=True),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    evidence_key: Mapped[str | None] = mapped_column(String(128))
    file_path: Mapped[str] = mapped_column(String(1024))
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    excerpt_hash: Mapped[str] = mapped_column(String(64))
    detector: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(128))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "skill_id", "semantic_version", "content_hash", name="uq_skill_version_content"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    skill_id: Mapped[str] = mapped_column(String(128), index=True)
    semantic_version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    instructions: Mapped[str] = mapped_column(Text)
    references_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisRunSkill(Base):
    __tablename__ = "analysis_run_skills"
    __table_args__ = (UniqueConstraint("run_id", "ordinal", name="uq_analysis_run_skill_ordinal"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    skill_version_id: Mapped[UUID] = mapped_column(ForeignKey("skill_versions.id"))
    ordinal: Mapped[int] = mapped_column(Integer)


class AgentToolEvent(Base):
    __tablename__ = "agent_tool_events"
    __table_args__ = (
        UniqueConstraint("run_id", "turn", "call_id", name="uq_agent_tool_event_call"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    turn: Mapped[int] = mapped_column(Integer)
    call_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeploymentIntentVersion(Base):
    __tablename__ = "deployment_intent_versions"
    __table_args__ = (UniqueConstraint("application_id", "version", name="uq_intent_version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    assessment_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSONB)
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InfrastructurePlan(Base):
    __tablename__ = "infrastructure_plans"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    analysis_run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    assessment_version: Mapped[int] = mapped_column(Integer)
    intent_version_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_intent_versions.id"))
    environment_profile_id: Mapped[str] = mapped_column(String(128))
    environment_profile_hash: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(64))
    bundle_digest: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    validation_json: Mapped[dict] = mapped_column(JSONB)
    previous_plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("infrastructure_plans.id"))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InfrastructurePlanFile(Base):
    __tablename__ = "infrastructure_plan_files"
    __table_args__ = (UniqueConstraint("plan_id", "path", name="uq_plan_file_path"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_plans.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InfrastructurePlanValidation(Base):
    __tablename__ = "infrastructure_plan_validations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_plans.id"), index=True)
    bundle_digest: Mapped[str] = mapped_column(String(64))
    environment_profile_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict] = mapped_column(JSONB)
    actor_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanApproval(Base):
    __tablename__ = "plan_approvals"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_plans.id"), index=True)
    bundle_digest: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(128))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InfrastructurePullRequest(Base):
    __tablename__ = "infrastructure_pull_requests"
    __table_args__ = (UniqueConstraint("plan_id", name="uq_infrastructure_pr_plan"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_plans.id"), index=True)
    repository_id: Mapped[int | None] = mapped_column(BigInteger)
    owner: Mapped[str] = mapped_column(String(128))
    repository: Mapped[str] = mapped_column(String(128))
    base_branch: Mapped[str] = mapped_column(String(255))
    base_sha: Mapped[str] = mapped_column(String(40))
    head_branch: Mapped[str] = mapped_column(String(255))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    number: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(1024))
    merged_sha: Mapped[str | None] = mapped_column(String(40))
    source_head_sha: Mapped[str | None] = mapped_column(String(40))
    source_status: Mapped[str | None] = mapped_column(String(32))
    source_error_code: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"
    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(64))
    repository_id: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GitHubJob(Base):
    __tablename__ = "github_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    pull_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("infrastructure_pull_requests.id"), index=True)
    delivery_id: Mapped[str | None] = mapped_column(ForeignKey("github_webhook_deliveries.delivery_id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_dispatch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerifiedBuild(Base):
    __tablename__ = "verified_builds"
    __table_args__ = (UniqueConstraint("pull_request_id", "run_id", "run_attempt", name="uq_verified_build_run"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_pull_requests.id"), index=True)
    run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    merged_sha: Mapped[str] = mapped_column(String(40))
    image_repository: Mapped[str] = mapped_column(String(255))
    image_digest: Mapped[str] = mapped_column(String(71))
    source_sha: Mapped[str | None] = mapped_column(String(40))
    artifact_id: Mapped[int | None] = mapped_column(BigInteger)
    compatibility_json: Mapped[dict | None] = mapped_column(JSONB)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseSpecificationRecord(Base):
    __tablename__ = "release_specifications"
    __table_args__ = (Index(
        "uq_standard_release_plan_build", "plan_id", "verified_build_id", unique=True,
        postgresql_where=text("kind = 'standard'"),
    ),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("infrastructure_plans.id"), index=True)
    verified_build_id: Mapped[UUID] = mapped_column(ForeignKey("verified_builds.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="standard")
    rollback_target_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("release_specifications.id"))
    rollback_from_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("release_specifications.id"))
    approval_id: Mapped[UUID] = mapped_column(ForeignKey("plan_approvals.id"))
    environment_profile_id: Mapped[str] = mapped_column(String(128))
    environment_profile_hash: Mapped[str] = mapped_column(String(64))
    source_bundle_digest: Mapped[str] = mapped_column(String(64))
    manifest_digest: Mapped[str] = mapped_column(String(64))
    merged_sha: Mapped[str] = mapped_column(String(40))
    image_reference: Mapped[str] = mapped_column(String(330))
    validation_json: Mapped[dict] = mapped_column(JSONB)
    source_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseFile(Base):
    __tablename__ = "release_files"
    __table_args__ = (UniqueConstraint("release_id", "path", name="uq_release_file_path"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("release_specifications.id"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))


class DeploymentRun(Base):
    __tablename__ = "deployment_runs"
    __table_args__ = (
        UniqueConstraint("release_id", name="uq_deployment_release"),
        Index(
            "uq_active_deployment_application_environment",
            "application_id", "environment_profile_id", unique=True,
            postgresql_where=text("status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling', 'cleanup_pending', 'cleaning')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("release_specifications.id"))
    verified_build_id: Mapped[UUID] = mapped_column(ForeignKey("verified_builds.id"))
    environment_profile_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    preflight_validation_json: Mapped[dict | None] = mapped_column(JSONB)
    rollout_json: Mapped[dict | None] = mapped_column(JSONB)
    health_json: Mapped[dict | None] = mapped_column(JSONB)
    previous_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("release_specifications.id"))
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"
    __table_args__ = (UniqueConstraint("deployment_run_id", "sequence", name="uq_deployment_event_sequence"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    deployment_run_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeliveryJob(Base):
    __tablename__ = "delivery_jobs"
    __table_args__ = (UniqueConstraint("kind", "verified_build_id", name="uq_delivery_job_build_kind"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    verified_build_id: Mapped[UUID] = mapped_column(ForeignKey("verified_builds.id"), index=True)
    deployment_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("deployment_runs.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_dispatch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeploymentPolicy(Base):
    __tablename__ = "deployment_policies"
    __table_args__ = (UniqueConstraint("application_id", "environment_profile_id", name="uq_deployment_policy_target"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    environment_profile_id: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=False)
    allowed_source_sha: Mapped[str | None] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ExecutionJob(Base):
    __tablename__ = "execution_jobs"
    __table_args__ = (UniqueConstraint("deployment_run_id", name="uq_execution_job_run"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    deployment_run_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="deploy")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_dispatch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeploymentResource(Base):
    __tablename__ = "deployment_resources"
    __table_args__ = (UniqueConstraint("deployment_run_id", "kind", "name", name="uq_deployment_resource"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    deployment_run_id: Mapped[UUID] = mapped_column(ForeignKey("deployment_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(32))
    uid: Mapped[str | None] = mapped_column(String(128))
    generation: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PendingJob(Base):
    __tablename__ = "pending_jobs"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_runs.id"), primary_key=True)
    next_dispatch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    publish_attempts: Mapped[int] = mapped_column(default=0)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "actor_id", "action", "key", name="uq_idempotency"),
        Index("ix_idempotency_created", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSONB)
    status_code: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
