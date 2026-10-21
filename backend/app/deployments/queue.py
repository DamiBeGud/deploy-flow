"""Queue only a preflighted release under an explicit application policy."""

from datetime import timedelta

from sqlalchemy import func, select

from app.config import settings
from app.deployments.state import transition
from app.persistence.models import (
    DeploymentPolicy,
    DeploymentRun,
    ExecutionJob,
    ReleaseSpecificationRecord,
)


def queue_execution(db, run: DeploymentRun) -> ExecutionJob | None:
    if not settings().deployment_execution_enabled or run.status != "waiting_for_executor":
        return None
    policy = db.scalar(select(DeploymentPolicy).where(
        DeploymentPolicy.application_id == run.application_id,
        DeploymentPolicy.environment_profile_id == run.environment_profile_id,
        DeploymentPolicy.enabled.is_(True),
    ))
    if policy is None or not run.preflight_validation_json or run.preflight_validation_json.get("status") != "passed":
        return None
    release = db.get(ReleaseSpecificationRecord, run.release_id)
    if policy.allowed_source_sha and policy.allowed_source_sha != (release.source_sha or release.merged_sha):
        return None
    existing = db.scalar(select(ExecutionJob).where(ExecutionJob.deployment_run_id == run.id))
    if existing:
        return existing
    previous = db.scalar(select(DeploymentRun).where(
        DeploymentRun.application_id == run.application_id,
        DeploymentRun.environment_profile_id == run.environment_profile_id,
        DeploymentRun.status == "succeeded",
    ).order_by(DeploymentRun.created_at.desc(), DeploymentRun.id.desc()))
    run.previous_release_id = previous.release_id if previous else None
    now = db.scalar(select(func.now()))
    job = ExecutionJob(
        deployment_run_id=run.id, status="queued", attempts=0,
        deadline_at=now + timedelta(seconds=settings().execution_deadline_seconds),
    )
    db.add(job)
    transition(db, run, "checking")
    return job
