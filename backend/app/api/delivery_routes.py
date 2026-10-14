"""Read and retry the durable CI-to-release handoff."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.identity import Identity, get_identity
from app.bootstrap.profiles import local_k3s_profile
from app.deployments.preparation import ReleasePreparationError, _source
from app.deployments.state import ACTIVE_STATES, transition
from app.persistence.commands import application_for, idempotent
from app.persistence.database import Session
from app.persistence.models import (
    Application,
    DeliveryJob,
    DeploymentEvent,
    DeploymentRun,
    InfrastructurePlan,
    InfrastructurePullRequest,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

router = APIRouter(prefix="/api/v1")
Actor = Annotated[Identity, Depends(get_identity)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _job_document(job: DeliveryJob) -> dict:
    return {
        "id": str(job.id), "kind": job.kind,
        "verified_build_id": str(job.verified_build_id),
        "deployment_id": str(job.deployment_run_id) if job.deployment_run_id else None,
        "status": job.status, "attempts": job.attempts,
        "error_code": job.error_code,
    }


def _run_document(db, run: DeploymentRun) -> dict:
    release = db.get(ReleaseSpecificationRecord, run.release_id)
    return {
        "id": str(run.id), "application_id": str(run.application_id),
        "environment_profile_id": run.environment_profile_id,
        "release_id": str(run.release_id), "release_digest": release.manifest_digest,
        "image_reference": release.image_reference, "status": run.status,
        "error_code": run.error_code, "event_sequence": run.event_sequence,
        "preflight_validation": run.preflight_validation_json,
        "created_at": run.created_at.isoformat(),
    }


@router.get("/releases/{release_id}")
def get_release(release_id: UUID, identity: Actor):
    with Session() as db:
        release = db.get(ReleaseSpecificationRecord, release_id)
        if not release:
            raise HTTPException(404, "Release not found")
        application_for(db, release.application_id, identity)
        return {
            "id": str(release.id), "application_id": str(release.application_id),
            "plan_id": str(release.plan_id), "verified_build_id": str(release.verified_build_id),
            "environment_profile_id": release.environment_profile_id,
            "source_bundle_digest": release.source_bundle_digest,
            "manifest_digest": release.manifest_digest,
            "image_reference": release.image_reference,
            "validation": release.validation_json,
            "created_at": release.created_at.isoformat(),
        }


@router.get("/applications/{application_id}/deployments")
def list_deployments(application_id: UUID, identity: Actor):
    with Session() as db:
        application_for(db, application_id, identity)
        runs = db.scalars(select(DeploymentRun).where(
            DeploymentRun.application_id == application_id,
        ).order_by(DeploymentRun.created_at.desc(), DeploymentRun.id.desc()).limit(50)).all()
        return {"deployments": [_run_document(db, run) for run in runs]}


@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: UUID, identity: Actor):
    with Session() as db:
        run = db.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        return _run_document(db, run)


@router.get("/deployments/{deployment_id}/events")
def deployment_events(deployment_id: UUID, identity: Actor, after: int = Query(default=0, ge=0)):
    with Session() as db:
        run = db.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        if after > run.event_sequence:
            raise HTTPException(400, "Event cursor is ahead of this deployment")
        events = db.scalars(select(DeploymentEvent).where(
            DeploymentEvent.deployment_run_id == run.id,
            DeploymentEvent.sequence > after,
        ).order_by(DeploymentEvent.sequence).limit(100)).all()
        return {"events": [{
            "sequence": event.sequence, "type": event.kind,
            "occurred_at": event.occurred_at.isoformat(), **event.payload_json,
        } for event in events], "last_sequence": run.event_sequence}


@router.post("/pull-requests/{pull_request_id}/release-preparation", status_code=202)
def enqueue_release_preparation(pull_request_id: UUID, identity: Actor, key: Key):
    """Explicit recovery path for builds verified before automatic handoff existed."""
    identity.require_write()
    with Session.begin() as db:
        pr = db.get(InfrastructurePullRequest, pull_request_id, with_for_update=True)
        if not pr:
            raise HTTPException(404, "Pull request not found")
        plan = db.get(InfrastructurePlan, pr.plan_id)
        application_for(db, plan.application_id, identity)

        def operation():
            build = db.scalar(select(VerifiedBuild).where(
                VerifiedBuild.pull_request_id == pr.id,
                VerifiedBuild.merged_sha == pr.merged_sha,
            ).order_by(VerifiedBuild.run_id.desc(), VerifiedBuild.run_attempt.desc()))
            if build is None:
                raise HTTPException(409, "No verified build is available")
            try:
                _source(db, pr.id, build.id, local_k3s_profile())
            except (ReleasePreparationError, ValueError):
                raise HTTPException(409, "The verified release is no longer eligible") from None
            job = db.scalar(select(DeliveryJob).where(
                DeliveryJob.kind == "prepare", DeliveryJob.verified_build_id == build.id,
            ).with_for_update())
            if job is None:
                job = DeliveryJob(kind="prepare", verified_build_id=build.id, status="queued", attempts=0)
                db.add(job)
                db.flush()
            elif job.status in {"blocked", "failed"}:
                job.status = "queued"
                job.error_code = None
                job.attempts = 0
                job.next_dispatch_at = db.scalar(select(func.now()))
                job.lease_token = None
                job.lease_expires_at = None
            return _job_document(job), 202

        response, code = idempotent(
            db, identity, f"pull-requests/{pull_request_id}/release-preparation",
            key, {}, operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/deployments/{deployment_id}/retry-preflight", status_code=202)
def retry_preflight(deployment_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        run = db.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        db.get(Application, run.application_id, with_for_update=True)
        run = db.scalar(select(DeploymentRun).where(DeploymentRun.id == deployment_id).with_for_update())

        def operation():
            job = db.scalar(select(DeliveryJob).where(
                DeliveryJob.kind == "preflight",
                DeliveryJob.deployment_run_id == run.id,
            ).with_for_update())
            if not job or job.status not in {"blocked", "failed"} or run.status not in {"blocked", "failed"}:
                raise HTTPException(409, "Preflight cannot be retried in its current state")
            active = db.scalar(select(DeploymentRun).where(
                DeploymentRun.application_id == run.application_id,
                DeploymentRun.environment_profile_id == run.environment_profile_id,
                DeploymentRun.status.in_(ACTIVE_STATES),
            ))
            if active:
                raise HTTPException(409, "Another deployment is active for this application")
            try:
                release = db.get(ReleaseSpecificationRecord, run.release_id)
                build = db.get(VerifiedBuild, run.verified_build_id)
                _source(db, build.pull_request_id, build.id, local_k3s_profile())
                if release.verified_build_id != build.id:
                    raise ReleasePreparationError("release_record_corrupt")
            except (ReleasePreparationError, ValueError, AttributeError):
                raise HTTPException(409, "The verified release is no longer eligible") from None
            job.status = "queued"
            job.error_code = None
            job.attempts = 0
            job.lease_token = None
            job.lease_expires_at = None
            job.next_dispatch_at = db.scalar(select(func.now()))
            transition(db, run, "queued")
            return _job_document(job), 202

        response, code = idempotent(
            db, identity, f"deployments/{deployment_id}/retry-preflight",
            key, {}, operation,
        )
    return JSONResponse(response, status_code=code)
