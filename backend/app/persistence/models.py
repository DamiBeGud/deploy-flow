from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
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
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(default=0)
    event_sequence: Mapped[int] = mapped_column(default=0)
    assessment_version: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
