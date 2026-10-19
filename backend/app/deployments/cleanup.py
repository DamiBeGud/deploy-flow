"""Fenced, explicit cleanup of a failed first deployment's owned inventory."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.rollback import RollbackError, release_files
from app.deployments.state import transition
from app.kubernetes.client import ClusterError
from app.kubernetes.executor import delete_owned
from app.kubernetes.policy import ManifestPolicyError, checked_manifests
from app.persistence.database import Session
from app.persistence.models import (
    DeploymentResource,
    DeploymentRun,
    ExecutionJob,
    ReleaseSpecificationRecord,
)

log = logging.getLogger(__name__)
_REVERSE_ORDER = {"Ingress": 0, "Deployment": 1, "Service": 2, "ConfigMap": 3}


class CleanupError(ValueError):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


def _claim(job_id: UUID) -> tuple[UUID, UUID] | None:
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        if not job or job.purpose != "cleanup" or job.status not in {"queued", "retry_wait", "running"}:
            return None
        now = db.scalar(select(func.now()))
        if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
            return None
        if job.status == "retry_wait" and job.next_dispatch_at > now:
            return None
        run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
        if run.status == "cleaned":
            job.status = "completed"
            job.lease_token = None
            job.lease_expires_at = None
            return None
        if now >= job.deadline_at or (
            run.status != "cleaning" and job.attempts >= settings().execution_max_attempts
        ):
            job.status = "blocked"
            job.error_code = "cleanup_deadline_exceeded"
            job.lease_token = None
            job.lease_expires_at = None
            transition(db, run, "reconciling", error_code=job.error_code)
            return None
        job.status = "running"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=settings().execution_lease_seconds)
        return job.lease_token, run.id


def _fenced(db, job_id: UUID, token: UUID, run_id: UUID):
    job = db.get(ExecutionJob, job_id, with_for_update=True)
    now = db.scalar(select(func.now()))
    if (
        not job or job.deployment_run_id != run_id or job.purpose != "cleanup"
        or job.status != "running" or job.lease_token != token
        or not job.lease_expires_at or job.lease_expires_at <= now
        or job.deadline_at <= now
    ):
        raise CleanupError("cleanup_attempt_stale")
    run = db.get(DeploymentRun, run_id, with_for_update=True)
    if run.status not in {"cleanup_pending", "cleaning"}:
        raise CleanupError("cleanup_state_changed")
    return job, run


def _inventory(db, run: DeploymentRun) -> list[DeploymentResource]:
    if run.previous_release_id is not None:
        raise CleanupError("cleanup_prior_release_exists")
    profile = local_k3s_profile()
    release = db.get(ReleaseSpecificationRecord, run.release_id)
    if (
        release is None or release.application_id != run.application_id
        or release.environment_profile_id != profile["id"]
        or release.environment_profile_hash != profile["hash"]
    ):
        raise CleanupError("cleanup_release_invalid")
    files = release_files(db, release)
    manifests = checked_manifests(
        files, digest=release.manifest_digest, namespace=profile["namespace"],
        application_id=str(run.application_id), registry=profile["registry"],
    )
    allowed = {(item["kind"], item["metadata"]["name"]) for item in manifests}
    rows = db.scalars(select(DeploymentResource).where(
        DeploymentResource.deployment_run_id == run.id,
    )).all()
    if (
        not rows or any((row.kind, row.name) not in allowed for row in rows)
        or any(row.status not in {"sending", "applied", "deleting", "deleted"} for row in rows)
    ):
        raise CleanupError("cleanup_inventory_invalid")
    return sorted(rows, key=lambda row: _REVERSE_ORDER[row.kind])


def _execute(job_id: UUID, token: UUID, run_id: UUID) -> None:
    if not settings().deployment_cleanup_enabled or settings().auth_mode != "oidc":
        raise CleanupError("cleanup_not_configured")
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        if run is None:
            raise CleanupError("deployment_missing")
        inventory = [(row.kind, row.name) for row in _inventory(db, run)]
    pending = False
    for kind, name in inventory:
        with Session.begin() as db:
            _, run = _fenced(db, job_id, token, run_id)
            _inventory(db, run)
            row = db.scalar(select(DeploymentResource).where(
                DeploymentResource.deployment_run_id == run.id,
                DeploymentResource.kind == kind, DeploymentResource.name == name,
            ).with_for_update())
            if row.status == "deleted":
                continue
            row.status = "deleting"
            transition(db, run, "cleaning")
            # Hold the claim and resource lock while the bounded request runs.
            absent = delete_owned(kind, name, str(run.application_id), row.uid)
            if absent:
                row.status = "deleted"
                row.error_code = None
            else:
                pending = True
    if pending:
        raise CleanupError("cleanup_pending", transient=True)
    with Session.begin() as db:
        _, run = _fenced(db, job_id, token, run_id)
        if any(row.status != "deleted" for row in _inventory(db, run)):
            raise CleanupError("cleanup_pending", transient=True)
        transition(db, run, "cleaned")


def _finish(job_id: UUID, token: UUID, error: CleanupError | None) -> None:
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        now = db.scalar(select(func.now()))
        if (
            not job or job.purpose != "cleanup" or job.status != "running"
            or job.lease_token != token or not job.lease_expires_at
            or job.lease_expires_at <= now
        ):
            return
        run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
        job.lease_token = None
        job.lease_expires_at = None
        if error is None:
            job.status = "completed"
            job.error_code = None
            return
        job.error_code = error.code
        if error.transient and now < job.deadline_at and (
            run.status == "cleaning" or job.attempts < settings().execution_max_attempts
        ):
            job.status = "retry_wait"
            job.next_dispatch_at = now + timedelta(seconds=min(
                settings().execution_retry_seconds * 2 ** min(job.attempts - 1, 6), 90,
            ))
            return
        job.status = "blocked"
        transition(db, run, "reconciling", error_code=error.code)


def execute(job_id: str) -> None:
    identifier = UUID(job_id)
    claim = _claim(identifier)
    if claim is None:
        return
    token, run_id = claim
    error = None
    try:
        _execute(identifier, token, run_id)
    except Exception as exc:
        if isinstance(exc, CleanupError):
            error = exc
        elif isinstance(exc, ClusterError):
            error = CleanupError(exc.code, transient=exc.code in {
                "cluster_unavailable", "cluster_access_expired",
            })
        elif isinstance(exc, (RollbackError, ManifestPolicyError)):
            error = CleanupError(str(exc))
        else:
            log.exception("cleanup_job_unexpected job_id=%s", identifier)
            error = CleanupError("cleanup_unexpected")
        log.warning("cleanup_job_failed job_id=%s code=%s", identifier, error.code)
    _finish(identifier, token, error)
