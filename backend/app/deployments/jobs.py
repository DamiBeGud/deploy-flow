"""Durable CI-to-release handoff and deployment preflight; no apply capability."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.bootstrap.generator import canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.preparation import (
    ReleasePreparationError,
    _check_github,
    _source,
    prepare_release_candidate,
)
from app.deployments.release import ReleaseError, bind_verified_image
from app.deployments.state import transition
from app.integrations.github.api import GitHubPhase6Error
from app.kubernetes.gateway import GatewayError, call_kubernetes
from app.kubernetes.policy import ManifestPolicyError
from app.persistence.database import Session
from app.persistence.models import (
    Application,
    DeliveryJob,
    DeploymentRun,
    ReleaseFile,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

log = logging.getLogger(__name__)


class DeliveryError(ValueError):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


def _claim(job_id: UUID) -> tuple[UUID, str, UUID, UUID | None] | None:
    with Session.begin() as db:
        job = db.get(DeliveryJob, job_id, with_for_update=True)
        if job is None or job.status not in {"queued", "retry_wait", "running"}:
            return None
        now = db.scalar(select(func.now()))
        if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
            return None
        if job.status == "retry_wait" and job.next_dispatch_at > now:
            return None
        if job.attempts >= settings().delivery_max_attempts:
            job.status = "failed"
            job.error_code = "delivery_attempts_exhausted"
            job.lease_token = None
            job.lease_expires_at = None
            if job.deployment_run_id:
                run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
                if run.status not in {"superseded", "cancelled"}:
                    transition(db, run, "failed", error_code=job.error_code)
            return None
        if job.deployment_run_id:
            run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
            if run.status in {"superseded", "cancelled", "waiting_for_executor"}:
                job.status = "completed"
                job.lease_token = None
                job.lease_expires_at = None
                return None
            transition(db, run, "validating")
        job.status = "running"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=settings().delivery_lease_seconds)
        return job.lease_token, job.kind, job.verified_build_id, job.deployment_run_id


async def _preflight(job_id: UUID, token: UUID, build_id: UUID, run_id: UUID) -> None:
    profile = local_k3s_profile()
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        build = db.get(VerifiedBuild, build_id)
        record = db.get(ReleaseSpecificationRecord, run.release_id) if run else None
        if not run or not build or not record or run.verified_build_id != build_id or record.verified_build_id != build_id:
            raise DeliveryError("release_record_corrupt")
        source = _source(db, build.pull_request_id, build_id, profile)
        rows = db.scalars(select(ReleaseFile).where(ReleaseFile.release_id == record.id)).all()
        files = {row.path: row.content for row in rows}
        if (
            not rows or len(files) != len(rows)
            or any(hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash for row in rows)
            or canonical_digest(files) != record.manifest_digest
            or record.application_id != source.application_id
            or record.plan_id != source.plan_id
            or record.approval_id != source.approval_id
            or record.source_bundle_digest != source.bundle_digest
            or record.environment_profile_id != profile["id"]
            or record.environment_profile_hash != profile["hash"]
            or record.merged_sha != source.merged_sha
        ):
            raise DeliveryError("release_record_corrupt")
        expected = bind_verified_image(
            bundle_files=source.files, bundle_digest=source.bundle_digest,
            image_binding=source.image_binding, image_repository=source.image_repository,
            image_digest=source.image_digest, profile=profile,
            application_id=str(source.application_id),
        )
        if expected.files != files or expected.digest != record.manifest_digest or expected.image_reference != record.image_reference:
            raise DeliveryError("release_record_corrupt")
    _check_github(source)
    validation = await call_kubernetes("validator", "kube.validate_manifests", {
        "files": files, "digest": record.manifest_digest,
        "application_id": str(source.application_id), "registry": profile["registry"],
    })
    if validation.get("status") != "passed":
        raise DeliveryError(
            "release_admission_unavailable" if validation.get("status") == "unavailable"
            else "release_admission_failed",
            transient=validation.get("status") == "unavailable",
        )
    with Session.begin() as db:
        db.get(Application, source.application_id, with_for_update=True)
        if _source(db, source.pr_id, build_id, profile, lock=True) != source:
            raise DeliveryError("release_source_stale")
        job = db.get(DeliveryJob, job_id, with_for_update=True)
        now = db.scalar(select(func.now()))
        if (not job or job.status != "running" or job.lease_token != token
                or job.lease_expires_at is None or job.lease_expires_at <= now):
            raise DeliveryError("delivery_job_stale")
        run = db.get(DeploymentRun, run_id, with_for_update=True)
        if run.status != "validating":
            raise DeliveryError("deployment_state_changed")
        run.preflight_validation_json = validation
        transition(db, run, "waiting_for_executor")


def _error(exc: Exception) -> DeliveryError:
    if isinstance(exc, DeliveryError):
        return exc
    if isinstance(exc, GitHubPhase6Error):
        return DeliveryError(exc.code, transient=exc.transient)
    if isinstance(exc, GatewayError):
        return DeliveryError(str(exc), transient=str(exc) != "cluster_access_unconfigured")
    if isinstance(exc, ReleasePreparationError):
        return DeliveryError(str(exc), transient=str(exc) in {
            "release_admission_unavailable", "deployment_slot_busy",
        })
    if isinstance(exc, (ReleaseError, ManifestPolicyError)):
        return DeliveryError(str(exc))
    log.exception("delivery_job_unexpected")
    return DeliveryError("delivery_unexpected", transient=True)


def _finish(job_id: UUID, token: UUID, error: DeliveryError | None) -> None:
    with Session.begin() as db:
        job = db.get(DeliveryJob, job_id, with_for_update=True)
        now = db.scalar(select(func.now()))
        if (not job or job.status != "running" or job.lease_token != token
                or job.lease_expires_at is None or job.lease_expires_at <= now):
            return
        job.lease_token = None
        job.lease_expires_at = None
        if error is None:
            job.status = "completed"
            job.error_code = None
            return
        job.error_code = error.code
        if error.transient and job.attempts < settings().delivery_max_attempts:
            job.status = "retry_wait"
            job.next_dispatch_at = db.scalar(select(func.now())) + timedelta(
                seconds=min(settings().delivery_retry_seconds * 2 ** (job.attempts - 1), 900)
            )
        else:
            job.status = "failed" if error.code in {"release_record_corrupt", "delivery_unexpected"} else "blocked"
        if job.deployment_run_id:
            run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
            if run.status not in {"superseded", "cancelled"}:
                transition(db, run, job.status, error_code=error.code)


def execute(job_id: str) -> None:
    identifier = UUID(job_id)
    claim = _claim(identifier)
    if claim is None:
        return
    token, kind, build_id, run_id = claim
    error = None
    try:
        if kind == "prepare" and run_id is None:
            with Session() as db:
                build = db.get(VerifiedBuild, build_id)
                if build is None:
                    raise DeliveryError("verified_build_missing")
                pr_id = build.pull_request_id
            asyncio.run(prepare_release_candidate(
                pr_id, build_id, delivery_job_id=identifier,
                lease_token=token, queue_deployment=True,
            ))
        elif kind == "preflight" and run_id is not None:
            asyncio.run(_preflight(identifier, token, build_id, run_id))
        else:
            raise DeliveryError("delivery_job_invalid")
    except Exception as exc:
        error = _error(exc)
        log.warning("delivery_job_failed job_id=%s code=%s", identifier, error.code)
    _finish(identifier, token, error)
