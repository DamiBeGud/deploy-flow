"""Prepare an immutable, dry-run-validated release from a verified PR.

This operation reads GitHub and uses the validator only. It has no deployer
credential, deployment queue, or Kubernetes write capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.bootstrap.generator import TEMPLATE_VERSION, canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.release import ReleaseSpecification, bind_verified_image
from app.deployments.state import ACTIVE_STATES, transition
from app.integrations.github.api import GitHubAPI
from app.integrations.github.verification import checked_workflow_run
from app.kubernetes.gateway import call_kubernetes
from app.persistence.database import Session
from app.persistence.models import (
    Application,
    DeliveryJob,
    DeploymentRun,
    InfrastructurePlan,
    InfrastructurePlanFile,
    InfrastructurePullRequest,
    PlanApproval,
    ReleaseFile,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)


class ReleasePreparationError(ValueError):
    pass


@dataclass(frozen=True)
class _Source:
    pr_id: UUID
    plan_id: UUID
    application_id: UUID
    approval_id: UUID
    build_id: UUID
    ci_run_id: int
    ci_run_attempt: int
    owner: str
    repository: str
    repository_id: int
    number: int
    base_branch: str
    head_branch: str
    head_sha: str
    merged_sha: str
    source_sha: str
    artifact_id: int | None
    compatibility: dict | None
    bundle_digest: str
    image_repository: str
    image_digest: str
    image_binding: dict
    files: dict[str, str]


def _source(db, pr_id: UUID, build_id: UUID, profile: dict, *, lock: bool = False) -> _Source:
    pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=lock)
    if pr is None or pr.status != "verified" or pr.error_code:
        raise ReleasePreparationError("pull_request_not_verified")
    plan = db.get(InfrastructurePlan, pr.plan_id, with_for_update=lock)
    if plan is None:
        raise ReleasePreparationError("plan_missing")
    application = db.get(Application, plan.application_id)
    approval_query = select(PlanApproval).where(
        PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None),
    )
    approval = db.scalar(approval_query.with_for_update() if lock else approval_query)
    build = db.scalar(select(VerifiedBuild).where(
        VerifiedBuild.id == build_id,
        VerifiedBuild.pull_request_id == pr.id,
        VerifiedBuild.merged_sha == pr.merged_sha,
    ))
    latest_build = db.scalar(select(VerifiedBuild).where(
        VerifiedBuild.pull_request_id == pr.id,
        VerifiedBuild.merged_sha == pr.merged_sha,
    ).order_by(VerifiedBuild.verified_at.desc(), VerifiedBuild.run_id.desc(), VerifiedBuild.run_attempt.desc()))
    rows = db.scalars(select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan.id)).all()
    files = {row.path: row.content for row in rows}
    if (
        application is None or approval is None or build is None or latest_build is None
        or build.id != latest_build.id
        or plan.status != "approved" or approval.bundle_digest != plan.bundle_digest
        or plan.template_version != TEMPLATE_VERSION
        or plan.environment_profile_id != profile["id"]
        or plan.environment_profile_hash != profile["hash"]
        or not rows or len(files) != len(rows)
        or any(hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash for row in rows)
        or canonical_digest(files) != plan.bundle_digest
        or not isinstance(plan.validation_json, dict)
        or not isinstance(plan.validation_json.get("image_binding"), dict)
        or pr.repository_id is None or pr.number is None or pr.head_sha is None
        or pr.merged_sha is None or build.image_repository != plan.validation_json.get("image_binding", {}).get("repository")
    ):
        raise ReleasePreparationError("release_source_stale")
    return _Source(
        pr.id, plan.id, application.id, approval.id, build.id,
        build.run_id, build.run_attempt,
        pr.owner, pr.repository, pr.repository_id, pr.number, pr.base_branch,
        pr.head_branch, pr.head_sha, pr.merged_sha, build.source_sha or build.merged_sha,
        build.artifact_id, build.compatibility_json, plan.bundle_digest,
        build.image_repository, build.image_digest,
        plan.validation_json["image_binding"], files,
    )


def _check_github(source: _Source) -> None:
    with GitHubAPI(settings(), source.owner, source.repository, read_only=True) as api:
        pull = api.pull_request(source.number)
        merge_sha = pull.get("merge_commit_sha") or api.merged_event_sha(source.number)
        workflow = api.workflow_run(source.ci_run_id)
        if (
            api.repository_info().get("id") != source.repository_id
            or pull.get("merged") is not True
            or pull.get("base", {}).get("ref") != source.base_branch
            or pull.get("base", {}).get("repo", {}).get("id") != source.repository_id
            or pull.get("head", {}).get("ref") != source.head_branch
            or pull.get("head", {}).get("sha") != source.head_sha
            or merge_sha != source.merged_sha
            or api.branch_sha(source.base_branch) != source.source_sha
            or not checked_workflow_run(
                workflow, repository_id=source.repository_id,
                merged_sha=source.source_sha, base_branch=source.base_branch,
            )
            or workflow.get("run_attempt") != source.ci_run_attempt
        ):
            raise ReleasePreparationError("verified_revision_not_target_head")
        if source.source_sha != source.merged_sha:
            from app.integrations.github.compatibility import checked_source_compatibility
            from app.integrations.github.verification import (
                checked_artifact,
                verify_registry_digest,
            )
            compatibility = checked_source_compatibility(api, baseline=source.merged_sha, source=source.source_sha, files=source.files)
            if not source.compatibility or any(source.compatibility.get(key) != value for key, value in compatibility.items()):
                raise ReleasePreparationError("source_compatibility_changed")
            artifacts = [row for row in api.run_artifacts(source.ci_run_id) if row.get("name") == "deployflow-build" and not row.get("expired")]
            if len(artifacts) != 1 or artifacts[0].get("id") != source.artifact_id:
                raise ReleasePreparationError("build_artifact_changed")
            digest = checked_artifact(api.artifact_zip(source.artifact_id), repository_id=source.repository_id,
                                      commit_sha=source.source_sha, run_id=source.ci_run_id,
                                      run_attempt=source.ci_run_attempt, image_repository=source.image_repository)
            if digest != source.image_digest:
                raise ReleasePreparationError("build_artifact_changed")
            verify_registry_digest(source.image_repository, digest)


async def prepare_release_candidate(
    pr_id: UUID, build_id: UUID, *,
    delivery_job_id: UUID | None = None, lease_token: UUID | None = None,
    queue_deployment: bool = False,
) -> UUID:
    """Persist final manifest bytes only after GitHub and final admission checks."""
    profile = local_k3s_profile()
    with Session() as db:
        source = _source(db, pr_id, build_id, profile)
    _check_github(source)
    release: ReleaseSpecification = bind_verified_image(
        bundle_files=source.files,
        bundle_digest=source.bundle_digest,
        image_binding=source.image_binding,
        image_repository=source.image_repository,
        image_digest=source.image_digest,
        profile=profile,
        application_id=str(source.application_id),
    )
    validation = await call_kubernetes("validator", "kube.validate_manifests", {
        "files": release.files,
        "digest": release.digest,
        "application_id": str(source.application_id),
        "registry": profile["registry"],
    })
    if validation.get("status") != "passed":
        raise ReleasePreparationError(
            "release_admission_unavailable" if validation.get("status") == "unavailable"
            else "release_admission_failed"
        )
    with Session.begin() as db:
        db.get(Application, source.application_id, with_for_update=True)
        if _source(db, pr_id, build_id, profile, lock=True) != source:
            raise ReleasePreparationError("release_source_stale")
        if delivery_job_id is not None:
            job = db.get(DeliveryJob, delivery_job_id, with_for_update=True)
            now = db.scalar(select(func.now()))
            if (not job or job.lease_token != lease_token or job.status != "running"
                    or job.lease_expires_at is None or job.lease_expires_at <= now):
                raise ReleasePreparationError("release_job_stale")
        existing = db.scalar(select(ReleaseSpecificationRecord).where(
            ReleaseSpecificationRecord.plan_id == source.plan_id,
            ReleaseSpecificationRecord.verified_build_id == source.build_id,
        ))
        if existing:
            if (
                existing.manifest_digest != release.digest
                or existing.image_reference != release.image_reference
                or existing.approval_id != source.approval_id
            ):
                raise ReleasePreparationError("release_record_conflict")
            stored = db.scalars(select(ReleaseFile).where(ReleaseFile.release_id == existing.id)).all()
            if {row.path: row.content for row in stored} != release.files or any(
                hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash for row in stored
            ):
                raise ReleasePreparationError("release_record_corrupt")
            record = existing
        else:
            record = ReleaseSpecificationRecord(
                application_id=source.application_id, plan_id=source.plan_id,
                verified_build_id=source.build_id, approval_id=source.approval_id,
                environment_profile_id=profile["id"], environment_profile_hash=profile["hash"],
                source_bundle_digest=source.bundle_digest, manifest_digest=release.digest,
                merged_sha=source.merged_sha, source_sha=source.source_sha, image_reference=release.image_reference,
                validation_json=validation,
            )
            db.add(record)
            db.flush()
            for path, content in sorted(release.files.items()):
                db.add(ReleaseFile(
                    release_id=record.id, path=path, content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                ))
        if queue_deployment:
            run = db.scalar(select(DeploymentRun).where(DeploymentRun.release_id == record.id))
            if run is None:
                active = db.scalar(select(DeploymentRun).where(
                    DeploymentRun.application_id == source.application_id,
                    DeploymentRun.environment_profile_id == profile["id"],
                    DeploymentRun.status.in_(ACTIVE_STATES),
                ).with_for_update())
                if active:
                    if active.status != "waiting_for_executor":
                        raise ReleasePreparationError("deployment_slot_busy")
                    transition(db, active, "superseded", error_code="newer_verified_release")
                    db.flush()
                run = DeploymentRun(
                    application_id=source.application_id, release_id=record.id,
                    verified_build_id=source.build_id,
                    environment_profile_id=profile["id"], status="created",
                    event_sequence=0,
                )
                db.add(run)
                db.flush()
                transition(db, run, "queued")
                db.add(DeliveryJob(
                    kind="preflight", verified_build_id=source.build_id,
                    deployment_run_id=run.id, status="queued", attempts=0,
                ))
        return record.id
