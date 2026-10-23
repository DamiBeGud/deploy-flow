"""Read and retry the durable CI-to-release handoff."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.identity import Identity, get_identity
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.preparation import ReleasePreparationError, _source
from app.deployments.queue import queue_execution
from app.deployments.rollback import RollbackError, queue_rollback
from app.deployments.state import ACTIVE_STATES, transition
from app.kubernetes.policy import ManifestPolicyError
from app.persistence.commands import application_for, idempotent
from app.persistence.database import Session
from app.persistence.models import (
    Application,
    AuditEvent,
    DeliveryJob,
    DeploymentEvent,
    DeploymentPolicy,
    DeploymentResource,
    DeploymentRun,
    ExecutionJob,
    GitHubJob,
    InfrastructurePlan,
    InfrastructurePullRequest,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

router = APIRouter(prefix="/api/v1")
Actor = Annotated[Identity, Depends(get_identity)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class DeploymentPolicyWrite(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool
    allowed_source_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    source_compatibility_policy: Literal["node-source-compatibility-v1", "node-handler-compatibility-v2"] | None = None


class CleanupResource(BaseModel):
    kind: str
    name: str
    uid: str | None


class CleanupWrite(BaseModel):
    expected_release_digest: str
    resources: list[CleanupResource]


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
    job = db.scalar(select(ExecutionJob).where(ExecutionJob.deployment_run_id == run.id))
    resources = db.scalars(select(DeploymentResource).where(
        DeploymentResource.deployment_run_id == run.id,
    ).order_by(DeploymentResource.kind, DeploymentResource.name)).all()
    return {
        "id": str(run.id), "application_id": str(run.application_id),
        "environment_profile_id": run.environment_profile_id,
        "release_id": str(run.release_id), "release_digest": release.manifest_digest,
        "source_sha": release.source_sha or release.merged_sha,
        "baseline_sha": release.merged_sha,
        "kind": release.kind,
        "rollback_target_release_id": str(release.rollback_target_release_id) if release.rollback_target_release_id else None,
        "image_reference": release.image_reference, "status": run.status,
        "error_code": run.error_code, "event_sequence": run.event_sequence,
        "preflight_validation": run.preflight_validation_json,
        "rollout": run.rollout_json, "health": run.health_json,
        "previous_release_id": str(run.previous_release_id) if run.previous_release_id else None,
        "execution_job": {"id": str(job.id), "purpose": job.purpose, "status": job.status, "error_code": job.error_code} if job else None,
        "resources": [{"kind": row.kind, "name": row.name, "status": row.status,
                       "uid": row.uid, "generation": row.generation,
                       "error_code": row.error_code} for row in resources],
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
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
            "kind": release.kind,
            "rollback_target_release_id": str(release.rollback_target_release_id) if release.rollback_target_release_id else None,
            "rollback_from_release_id": str(release.rollback_from_release_id) if release.rollback_from_release_id else None,
            "plan_id": str(release.plan_id), "verified_build_id": str(release.verified_build_id),
            "environment_profile_id": release.environment_profile_id,
            "source_sha": release.source_sha or release.merged_sha,
            "baseline_sha": release.merged_sha,
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



@router.get("/applications/{application_id}/observation")
async def current_observation(application_id: UUID, identity: Actor):
    from app.deployments.observations import application_observation, cluster_snapshot
    with Session() as db:
        application_for(db, application_id, identity)
        row = db.execute(select(DeploymentResource, ReleaseSpecificationRecord).join(
            DeploymentRun, DeploymentResource.deployment_run_id == DeploymentRun.id,
        ).join(ReleaseSpecificationRecord, DeploymentRun.release_id == ReleaseSpecificationRecord.id).where(
            DeploymentRun.application_id == application_id, DeploymentResource.kind == "Deployment",
            DeploymentResource.uid.is_not(None),
        ).order_by(DeploymentRun.created_at.desc()).limit(1)).first()
        resource, release = row if row else (None, None)
        service = db.scalar(select(DeploymentResource).where(DeploymentResource.deployment_run_id == resource.deployment_run_id,
                            DeploymentResource.kind == "Service")) if resource else None
    snapshot = await cluster_snapshot()
    return application_observation(snapshot, application_id=application_id, resource=resource, release=release, service=service)


@router.get("/applications/{application_id}/release-status")
def release_status(application_id: UUID, identity: Actor):
    with Session() as db:
        application_for(db, application_id, identity)
        pr = db.scalar(select(InfrastructurePullRequest).join(InfrastructurePlan).where(
            InfrastructurePlan.application_id == application_id,
        ).order_by(InfrastructurePullRequest.created_at.desc()).limit(1))
        if not pr:
            return {"status": "awaiting_infrastructure_review", "build": None}
        build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.pull_request_id == pr.id).order_by(
            VerifiedBuild.verified_at.desc(), VerifiedBuild.run_id.desc(), VerifiedBuild.run_attempt.desc(),
        ).limit(1))
        job = db.scalar(select(DeliveryJob).where(DeliveryJob.verified_build_id == build.id, DeliveryJob.kind == "prepare")) if build else None
        pending = db.scalar(select(DeploymentRun).where(DeploymentRun.verified_build_id == build.id).order_by(
                            DeploymentRun.created_at.desc()).limit(1)) if build else None
        return {"pull_request_id": str(pr.id), "baseline_sha": pr.merged_sha,
                "source_sha": pr.source_head_sha, "status": pr.source_status or pr.status,
                "error_code": pr.source_error_code or pr.error_code,
                "preparation": _job_document(job) if job else None,
                "deployment": {"id": str(pending.id), "release_id": str(pending.release_id), "status": pending.status,
                               "error_code": pending.error_code} if pending else None,
                "build": {"id": str(build.id), "source_sha": build.source_sha or build.merged_sha,
                          "run_id": build.run_id, "run_attempt": build.run_attempt, "artifact_id": build.artifact_id,
                          "image_reference": f"{build.image_repository}@{build.image_digest}",
                          "compatibility": build.compatibility_json} if build else None}


@router.post("/applications/{application_id}/reconcile-source", status_code=202)
def reconcile_source(application_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        application_for(db, application_id, identity)
        db.get(Application, application_id, with_for_update=True)
        pr = db.scalar(select(InfrastructurePullRequest).join(InfrastructurePlan).where(
            InfrastructurePlan.application_id == application_id, InfrastructurePullRequest.status == "verified",
        ).order_by(InfrastructurePullRequest.created_at.desc()).limit(1))
        if not pr:
            raise HTTPException(409, "A verified infrastructure baseline is required")
        def operation():
            job = GitHubJob(kind="source_reconcile", pull_request_id=pr.id, status="queued", attempts=0)
            db.add(job)
            db.flush()
            return {"job_id": str(job.id), "status": "queued"}, 202
        response, code = idempotent(db, identity, f"applications/{application_id}/reconcile-source", key, {}, operation)
    return JSONResponse(response, status_code=code)


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
        } for event in events], "last_sequence": events[-1].sequence if events else after}


@router.get("/applications/{application_id}/deployment-policy")
def get_deployment_policy(application_id: UUID, identity: Actor):
    with Session() as db:
        application_for(db, application_id, identity)
        policy = db.scalar(select(DeploymentPolicy).where(
            DeploymentPolicy.application_id == application_id,
            DeploymentPolicy.environment_profile_id == "local-k3s",
        ))
        return {"enabled": bool(policy and policy.enabled),
                "source_compatibility_policy": policy.source_compatibility_policy if policy else "node-source-compatibility-v1",
                "allowed_source_sha": policy.allowed_source_sha if policy else None,
                "execution_available": settings().deployment_execution_enabled,
                "cleanup_available": settings().deployment_cleanup_enabled,
                "authorization_state": ("execution_disabled" if not settings().deployment_execution_enabled else
                                        "policy_disabled" if not policy or not policy.enabled else
                                        "single_source" if policy.allowed_source_sha else "future_compatible_sources"),
                "actor_id": policy.actor_id if policy else None}


@router.put("/applications/{application_id}/deployment-policy")
def set_deployment_policy(application_id: UUID, body: DeploymentPolicyWrite, identity: Actor, key: Key):
    identity.require_deploy()
    if settings().auth_mode != "oidc" or not settings().deployment_execution_enabled:
        raise HTTPException(409, "Authenticated deployment execution is not configured")
    with Session.begin() as db:
        application_for(db, application_id, identity)
        db.get(Application, application_id, with_for_update=True)

        def operation():
            policy = db.scalar(select(DeploymentPolicy).where(
                DeploymentPolicy.application_id == application_id,
                DeploymentPolicy.environment_profile_id == "local-k3s",
            ).with_for_update())
            if policy is None:
                policy = DeploymentPolicy(
                    application_id=application_id, environment_profile_id="local-k3s",
                    enabled=body.enabled, actor_id=identity.actor_id,
                )
                db.add(policy)
            else:
                policy.enabled = body.enabled
                policy.actor_id = identity.actor_id
            if "allowed_source_sha" in body.model_fields_set:
                policy.allowed_source_sha = body.allowed_source_sha
            if "source_compatibility_policy" in body.model_fields_set:
                if body.source_compatibility_policy is None:
                    raise HTTPException(422, "Select an explicit source compatibility policy")
                policy.source_compatibility_policy = body.source_compatibility_policy
            return {"enabled": body.enabled, "actor_id": identity.actor_id,
                    "source_compatibility_policy": policy.source_compatibility_policy or "node-source-compatibility-v1",
                    "allowed_source_sha": policy.allowed_source_sha}, 200

        response, code = idempotent(
            db, identity, f"applications/{application_id}/deployment-policy",
            key, body.model_dump(exclude_unset=True), operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/deployments/{deployment_id}/execute", status_code=202)
def execute_deployment(deployment_id: UUID, identity: Actor, key: Key):
    identity.require_deploy()
    if settings().auth_mode != "oidc" or not settings().deployment_execution_enabled:
        raise HTTPException(409, "Authenticated deployment execution is not configured")
    with Session.begin() as db:
        run = db.get(DeploymentRun, deployment_id)
        if run is None:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        db.get(Application, run.application_id, with_for_update=True)
        run = db.get(DeploymentRun, deployment_id, with_for_update=True)

        def operation():
            job = queue_execution(db, run)
            if job is None:
                raise HTTPException(409, "Enable deployment for this application and complete preflight first")
            db.flush()
            return {"deployment_id": str(run.id), "job_id": str(job.id), "status": run.status}, 202

        response, code = idempotent(
            db, identity, f"deployments/{deployment_id}/execute", key, {}, operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/deployments/{deployment_id}/retry-execution", status_code=202)
def retry_execution(deployment_id: UUID, identity: Actor, key: Key):
    """Explicitly reconcile a partly applied release, retaining its UID journal."""
    identity.require_deploy()
    if settings().auth_mode != "oidc" or not settings().deployment_execution_enabled:
        raise HTTPException(409, "Authenticated deployment execution is not configured")
    with Session.begin() as db:
        run = db.get(DeploymentRun, deployment_id)
        if run is None:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        db.get(Application, run.application_id, with_for_update=True)
        run = db.get(DeploymentRun, deployment_id, with_for_update=True)

        def operation():
            policy = db.scalar(select(DeploymentPolicy).where(
                DeploymentPolicy.application_id == run.application_id,
                DeploymentPolicy.environment_profile_id == run.environment_profile_id,
                DeploymentPolicy.enabled.is_(True),
            ))
            job = db.scalar(select(ExecutionJob).where(
                ExecutionJob.deployment_run_id == run.id,
            ).with_for_update())
            if not policy or not job or job.purpose != "deploy" or run.status != "reconciling" or job.status != "blocked":
                raise HTTPException(409, "This deployment is not eligible for reconciliation")
            now = db.scalar(select(func.now()))
            job.status = "queued"
            job.attempts = 0
            job.error_code = None
            job.next_dispatch_at = now
            job.deadline_at = now + timedelta(seconds=settings().execution_deadline_seconds)
            job.lease_token = None
            job.lease_expires_at = None
            transition(db, run, "checking")
            return {"deployment_id": str(run.id), "job_id": str(job.id), "status": run.status}, 202

        response, code = idempotent(
            db, identity, f"deployments/{deployment_id}/retry-execution", key, {}, operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/deployments/{deployment_id}/rollback", status_code=202)
def rollback_deployment(deployment_id: UUID, identity: Actor, key: Key):
    """Queue a new release containing the prior successful manifest bytes."""
    identity.require_deploy()
    if settings().auth_mode != "oidc" or not settings().deployment_execution_enabled:
        raise HTTPException(409, "Authenticated deployment execution is not configured")
    with Session.begin() as db:
        current = db.get(DeploymentRun, deployment_id)
        if current is None:
            raise HTTPException(404, "Deployment not found")
        application_for(db, current.application_id, identity)
        db.get(Application, current.application_id, with_for_update=True)
        current = db.get(DeploymentRun, deployment_id, with_for_update=True)

        def operation():
            policy = db.scalar(select(DeploymentPolicy).where(
                DeploymentPolicy.application_id == current.application_id,
                DeploymentPolicy.environment_profile_id == current.environment_profile_id,
                DeploymentPolicy.enabled.is_(True),
            ))
            if policy is None:
                raise HTTPException(409, "Enable deployment for this application first")
            try:
                return queue_rollback(db, current, identity.actor_id), 202
            except (RollbackError, ManifestPolicyError) as exc:
                raise HTTPException(409, str(exc)) from None

        response, code = idempotent(
            db, identity, f"deployments/{deployment_id}/rollback", key, {}, operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/deployments/{deployment_id}/cleanup", status_code=202)
def cleanup_deployment(deployment_id: UUID, body: CleanupWrite, identity: Actor, key: Key):
    """Explicitly delete only the recorded first-deployment inventory."""
    identity.require_deploy()
    if settings().auth_mode != "oidc" or not settings().deployment_cleanup_enabled:
        raise HTTPException(409, "Authenticated cleanup is not configured")
    with Session.begin() as db:
        run = db.get(DeploymentRun, deployment_id)
        if run is None:
            raise HTTPException(404, "Deployment not found")
        application_for(db, run.application_id, identity)
        db.get(Application, run.application_id, with_for_update=True)
        run = db.get(DeploymentRun, deployment_id, with_for_update=True)

        def operation():
            policy = db.scalar(select(DeploymentPolicy).where(
                DeploymentPolicy.application_id == run.application_id,
                DeploymentPolicy.environment_profile_id == run.environment_profile_id,
                DeploymentPolicy.enabled.is_(True),
            ))
            job = db.scalar(select(ExecutionJob).where(
                ExecutionJob.deployment_run_id == run.id,
            ).with_for_update())
            release = db.get(ReleaseSpecificationRecord, run.release_id)
            resources = db.scalars(select(DeploymentResource).where(
                DeploymentResource.deployment_run_id == run.id,
            )).all()
            expected = sorted((row.kind, row.name, row.uid) for row in resources)
            submitted = sorted(
                ((row.kind, row.name, row.uid) for row in body.resources),
                key=lambda row: (row[0], row[1], row[2] or ""),
            )
            if (
                policy is None or job is None or job.status != "blocked"
                or run.status != "reconciling" or run.previous_release_id is not None
                or release is None or body.expected_release_digest != release.manifest_digest
                or not resources or expected != submitted
            ):
                raise HTTPException(409, "Cleanup inventory changed or this deployment is not eligible")
            now = db.scalar(select(func.now()))
            job.purpose = "cleanup"
            job.status = "queued"
            job.attempts = 0
            job.error_code = None
            job.next_dispatch_at = now
            job.deadline_at = now + timedelta(seconds=settings().execution_deadline_seconds)
            job.lease_token = None
            job.lease_expires_at = None
            transition(db, run, "cleanup_pending")
            db.add(AuditEvent(
                organization_id=identity.organization_id, actor_id=identity.actor_id,
                action="deployment.cleanup.requested", resource_type="deployment",
                resource_id=str(run.id), metadata_json={
                    "release_digest": release.manifest_digest,
                    "resource_count": len(resources),
                },
            ))
            return {"deployment_id": str(run.id), "job_id": str(job.id), "status": run.status}, 202

        response, code = idempotent(
            db, identity, f"deployments/{deployment_id}/cleanup", key,
            body.model_dump(), operation,
        )
    return JSONResponse(response, status_code=code)


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
