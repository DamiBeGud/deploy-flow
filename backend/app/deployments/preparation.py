"""Prepare an immutable, dry-run-validated release from a verified PR.

This operation reads GitHub and uses the validator only. It has no deployer
credential, deployment queue, or Kubernetes write capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.bootstrap.generator import TEMPLATE_VERSION, canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.release import ReleaseSpecification, bind_verified_image
from app.integrations.github.api import GitHubAPI
from app.kubernetes.gateway import call_kubernetes
from app.persistence.database import Session
from app.persistence.models import (
    Application,
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
    owner: str
    repository: str
    repository_id: int
    number: int
    base_branch: str
    head_branch: str
    head_sha: str
    merged_sha: str
    bundle_digest: str
    image_repository: str
    image_digest: str
    image_binding: dict
    files: dict[str, str]


def _source(db, pr_id: UUID, profile: dict, *, lock: bool = False) -> _Source:
    pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=lock)
    if pr is None or pr.status != "verified" or pr.error_code:
        raise ReleasePreparationError("pull_request_not_verified")
    plan = db.get(InfrastructurePlan, pr.plan_id)
    if plan is None:
        raise ReleasePreparationError("plan_missing")
    application = db.get(Application, plan.application_id)
    approval = db.scalar(select(PlanApproval).where(
        PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None),
    ))
    build = db.scalar(select(VerifiedBuild).where(
        VerifiedBuild.pull_request_id == pr.id,
        VerifiedBuild.merged_sha == pr.merged_sha,
    ).order_by(VerifiedBuild.verified_at.desc(), VerifiedBuild.id.desc()))
    rows = db.scalars(select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan.id)).all()
    files = {row.path: row.content for row in rows}
    if (
        application is None or approval is None or build is None
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
        pr.owner, pr.repository, pr.repository_id, pr.number, pr.base_branch,
        pr.head_branch, pr.head_sha, pr.merged_sha, plan.bundle_digest,
        build.image_repository, build.image_digest,
        plan.validation_json["image_binding"], files,
    )


def _check_github(source: _Source) -> None:
    with GitHubAPI(settings(), source.owner, source.repository) as api:
        pull = api.pull_request(source.number)
        merge_sha = pull.get("merge_commit_sha") or api.merged_event_sha(source.number)
        if (
            api.repository_info().get("id") != source.repository_id
            or pull.get("merged") is not True
            or pull.get("base", {}).get("ref") != source.base_branch
            or pull.get("base", {}).get("repo", {}).get("id") != source.repository_id
            or pull.get("head", {}).get("ref") != source.head_branch
            or pull.get("head", {}).get("sha") != source.head_sha
            or merge_sha != source.merged_sha
            or api.branch_sha(source.base_branch) != source.merged_sha
        ):
            raise ReleasePreparationError("verified_revision_not_target_head")


async def prepare_release_candidate(pr_id: UUID) -> UUID:
    """Persist final manifest bytes only after GitHub and final admission checks."""
    profile = local_k3s_profile()
    with Session() as db:
        source = _source(db, pr_id, profile)
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
        raise ReleasePreparationError("release_admission_failed")
    with Session.begin() as db:
        if _source(db, pr_id, profile, lock=True) != source:
            raise ReleasePreparationError("release_source_stale")
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
            return existing.id
        record = ReleaseSpecificationRecord(
            application_id=source.application_id, plan_id=source.plan_id,
            verified_build_id=source.build_id, approval_id=source.approval_id,
            environment_profile_id=profile["id"], environment_profile_hash=profile["hash"],
            source_bundle_digest=source.bundle_digest, manifest_digest=release.digest,
            merged_sha=source.merged_sha, image_reference=release.image_reference,
            validation_json=validation,
        )
        db.add(record)
        db.flush()
        for path, content in sorted(release.files.items()):
            db.add(ReleaseFile(
                release_id=record.id, path=path, content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ))
        return record.id
