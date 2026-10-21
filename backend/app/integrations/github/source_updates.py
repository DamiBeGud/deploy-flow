"""Verify current branch builds against an independently approved baseline.

Notifications always reconcile the current branch head. Existing source SHAs
are never re-admitted, including CI reruns and replay after manual rollback.
"""
from sqlalchemy import select

from app.bootstrap.generator import TEMPLATE_VERSION, canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.integrations.github.api import GitHubAPI, GitHubPhase6Error
from app.integrations.github.compatibility import checked_source_compatibility
from app.integrations.github.verification import (
    checked_artifact,
    checked_workflow_run,
    verify_registry_digest,
)
from app.persistence.database import Session
from app.persistence.models import (
    Application,
    DeliveryJob,
    GitHubWebhookDelivery,
    InfrastructurePlan,
    InfrastructurePlanFile,
    InfrastructurePullRequest,
    PlanApproval,
    VerifiedBuild,
)


def reconcile_sources(pr_id=None, delivery_id=None):
    with Session() as db:
        query = select(InfrastructurePullRequest.id).where(InfrastructurePullRequest.status == "verified")
        if pr_id:
            query = query.where(InfrastructurePullRequest.id == pr_id)
        else:
            delivery = db.get(GitHubWebhookDelivery, delivery_id)
            if not delivery:
                return
            query = query.where(InfrastructurePullRequest.repository_id == delivery.repository_id)
        ids = list(db.scalars(query))
    transient = None
    for identifier in ids:
        try:
            reconcile_source(identifier)
        except GitHubPhase6Error as exc:
            with Session.begin() as db:
                row = db.get(InfrastructurePullRequest, identifier, with_for_update=True)
                row.source_head_sha = None
                row.source_status = "unavailable" if exc.transient else "needs_review"
                row.source_error_code = exc.code
            if exc.transient:
                transient = exc
    if transient:
        raise transient


def reconcile_source(identifier):
    # Serialize admission per application, including concurrent webhook workers.
    # All network reads are bounded. No Kubernetes capability is called here.
    with Session.begin() as db:
        pr = db.get(InfrastructurePullRequest, identifier)
        plan = db.get(InfrastructurePlan, pr.plan_id)
        db.get(Application, plan.application_id, with_for_update=True)
        db.refresh(pr)
        approval = db.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None)))
        if (pr.status != "verified" or not pr.merged_sha or not approval or plan.status != "approved"
                or approval.bundle_digest != plan.bundle_digest or plan.template_version != TEMPLATE_VERSION
                or plan.environment_profile_hash != local_k3s_profile()["hash"]):
            raise GitHubPhase6Error("plan_no_longer_approved")
        rows = db.scalars(select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan.id)).all()
        files = {row.path: row.content for row in rows}
        if not files or canonical_digest(files) != plan.bundle_digest:
            raise GitHubPhase6Error("stored_bundle_corrupt")
        latest = db.scalar(select(VerifiedBuild).where(VerifiedBuild.pull_request_id == pr.id).order_by(VerifiedBuild.verified_at.desc(), VerifiedBuild.run_id.desc(), VerifiedBuild.run_attempt.desc()))
        if latest is None:
            raise GitHubPhase6Error("bootstrap_build_unverified")
        with GitHubAPI(settings(), pr.owner, pr.repository, read_only=True) as api:
            if api.repository_info().get("id") != pr.repository_id:
                raise GitHubPhase6Error("github_repository_changed")
            source = api.branch_sha(pr.base_branch)
            pr.source_head_sha = source
            pr.source_error_code = None
            api.require_descendant(latest.source_sha or latest.merged_sha, source)
            existing = db.scalar(select(VerifiedBuild).where(
                VerifiedBuild.pull_request_id == pr.id,
                (VerifiedBuild.source_sha == source) | ((VerifiedBuild.source_sha.is_(None)) & (VerifiedBuild.merged_sha == source)),
            ))
            if existing:
                pr.source_status = "verified"
                return  # Never queue again merely because a webhook was replayed.
            compatibility = checked_source_compatibility(api, baseline=pr.merged_sha, source=source, files=files)
            compatibility.update({"assessment_run_id": str(plan.analysis_run_id), "assessment_version": plan.assessment_version,
                                  "approval_id": str(approval.id), "bundle_digest": plan.bundle_digest})
            runs = api.workflow_runs(pr.base_branch, source)
            runs.sort(key=lambda row: (row.get("id", 0), row.get("run_attempt", 0)), reverse=True)
            if not runs:
                pr.source_status = "awaiting_ci"
                return
            run = api.workflow_run(runs[0]["id"])
            if run.get("status") != "completed":
                pr.source_status = "awaiting_ci"
                return
            if run.get("id") != runs[0]["id"] or not checked_workflow_run(run, repository_id=pr.repository_id, merged_sha=source, base_branch=pr.base_branch):
                raise GitHubPhase6Error("workflow_run_untrusted")
            artifacts = [a for a in api.run_artifacts(run["id"]) if a.get("name") == "deployflow-build" and not a.get("expired")]
            if len(artifacts) != 1 or type(artifacts[0].get("id")) is not int or not 0 < artifacts[0].get("size_in_bytes", 0) <= 128 * 1024:
                raise GitHubPhase6Error("build_artifact_missing")
            image = plan.validation_json["image_binding"]["repository"]
            digest = checked_artifact(api.artifact_zip(artifacts[0]["id"]), repository_id=pr.repository_id,
                                      commit_sha=source, run_id=run["id"], run_attempt=run["run_attempt"], image_repository=image)
            verify_registry_digest(image, digest)
            if api.branch_sha(pr.base_branch) != source:
                raise GitHubPhase6Error("source_superseded", transient=True)
        build = VerifiedBuild(pull_request_id=pr.id, merged_sha=pr.merged_sha, source_sha=source,
                              run_id=run["id"], run_attempt=run["run_attempt"], artifact_id=artifacts[0]["id"],
                              image_repository=image, image_digest=digest, compatibility_json=compatibility)
        db.add(build)
        db.flush()
        db.add(DeliveryJob(kind="prepare", verified_build_id=build.id, status="queued", attempts=0))
        pr.source_status = "verified"
