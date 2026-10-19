"""Create a new, explicitly authorized release from a prior successful release.

This module only writes durable intent. The isolated executor performs fresh
admission and all Kubernetes writes after claiming the queued job.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from sqlalchemy import func, select

from app.bootstrap.generator import canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.state import ACTIVE_STATES, transition
from app.kubernetes.policy import checked_manifests
from app.persistence.models import (
    DeploymentRun,
    ExecutionJob,
    ReleaseFile,
    ReleaseSpecificationRecord,
)


class RollbackError(ValueError):
    pass


def release_files(db, release: ReleaseSpecificationRecord) -> dict[str, str]:
    rows = db.scalars(select(ReleaseFile).where(ReleaseFile.release_id == release.id)).all()
    files = {row.path: row.content for row in rows}
    if (
        not rows or len(files) != len(rows)
        or any(hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash for row in rows)
        or canonical_digest(files) != release.manifest_digest
    ):
        raise RollbackError("release_record_corrupt")
    return files


def queue_rollback(db, current: DeploymentRun, actor_id: str) -> dict:
    """Caller holds the application row lock and passed deployer authorization."""
    profile = local_k3s_profile()
    if current.status != "succeeded" or current.environment_profile_id != profile["id"]:
        raise RollbackError("rollback_current_release_required")
    latest = db.scalar(select(DeploymentRun).where(
        DeploymentRun.application_id == current.application_id,
        DeploymentRun.environment_profile_id == current.environment_profile_id,
        DeploymentRun.status == "succeeded",
    ).order_by(DeploymentRun.created_at.desc(), DeploymentRun.id.desc()).limit(1))
    if latest is None or latest.id != current.id:
        raise RollbackError("rollback_current_release_required")
    if current.previous_release_id is None:
        raise RollbackError("rollback_previous_release_missing")
    active = db.scalar(select(DeploymentRun.id).where(
        DeploymentRun.application_id == current.application_id,
        DeploymentRun.environment_profile_id == current.environment_profile_id,
        DeploymentRun.status.in_(ACTIVE_STATES),
    ).limit(1))
    if active is not None:
        raise RollbackError("deployment_slot_busy")
    target = db.get(ReleaseSpecificationRecord, current.previous_release_id)
    if (
        target is None or target.application_id != current.application_id
        or target.environment_profile_id != profile["id"]
        or target.environment_profile_hash != profile["hash"]
    ):
        raise RollbackError("rollback_target_invalid")
    target_was_deployed = db.scalar(select(DeploymentRun.id).where(
        DeploymentRun.release_id == target.id,
        DeploymentRun.status == "succeeded",
        DeploymentRun.application_id == current.application_id,
    ).limit(1))
    if target_was_deployed is None:
        raise RollbackError("rollback_target_not_successful")
    files = release_files(db, target)
    manifests = checked_manifests(
        files, digest=target.manifest_digest, namespace=profile["namespace"],
        application_id=str(current.application_id), registry=profile["registry"],
    )
    deployment = next(item for item in manifests if item["kind"] == "Deployment")
    if deployment["spec"]["template"]["spec"]["containers"][0]["image"] != target.image_reference:
        raise RollbackError("rollback_image_mismatch")
    current_release = db.get(ReleaseSpecificationRecord, current.release_id)
    if current_release is None:
        raise RollbackError("rollback_current_release_invalid")
    current_manifests = checked_manifests(
        release_files(db, current_release), digest=current_release.manifest_digest,
        namespace=profile["namespace"], application_id=str(current.application_id),
        registry=profile["registry"],
    )
    target_objects = {(item["kind"], item["metadata"]["name"]) for item in manifests}
    if any((item["kind"], item["metadata"]["name"]) not in target_objects for item in current_manifests):
        raise RollbackError("rollback_resource_removal_requires_cleanup")
    record = ReleaseSpecificationRecord(
        application_id=current.application_id,
        plan_id=target.plan_id,
        verified_build_id=target.verified_build_id,
        kind="rollback",
        rollback_target_release_id=target.id,
        rollback_from_release_id=current.release_id,
        approval_id=target.approval_id,
        environment_profile_id=target.environment_profile_id,
        environment_profile_hash=target.environment_profile_hash,
        source_bundle_digest=target.source_bundle_digest,
        manifest_digest=target.manifest_digest,
        merged_sha=target.merged_sha,
        image_reference=target.image_reference,
        validation_json={"status": "pending", "kind": "manual_rollback", "actor_id": actor_id},
    )
    db.add(record)
    db.flush()
    for path, content in sorted(files.items()):
        db.add(ReleaseFile(
            release_id=record.id, path=path, content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        ))
    run = DeploymentRun(
        application_id=current.application_id, release_id=record.id,
        verified_build_id=record.verified_build_id,
        environment_profile_id=profile["id"], status="created",
        previous_release_id=current.release_id, event_sequence=0,
    )
    db.add(run)
    db.flush()
    transition(db, run, "checking")
    now = db.scalar(select(func.now()))
    job = ExecutionJob(
        deployment_run_id=run.id, status="queued", attempts=0,
        deadline_at=now + timedelta(seconds=settings().execution_deadline_seconds),
    )
    db.add(job)
    db.flush()
    return {
        "deployment_id": str(run.id), "release_id": str(record.id),
        "rollback_from_release_id": str(current.release_id),
        "rollback_target_release_id": str(target.id),
        "job_id": str(job.id), "status": run.status,
    }
