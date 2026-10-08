"""Durable, retryable Phase 6 operations. No Kubernetes capability is imported."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import func, select

from app.bootstrap.generator import TEMPLATE_VERSION, canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.integrations.github.api import GitHubAPI, GitHubPhase6Error
from app.integrations.github.verification import (
    checked_artifact,
    checked_merged_files,
    checked_workflow_run,
    verify_registry_digest,
)
from app.persistence.database import Session
from app.persistence.models import (
    AnalysisRun,
    Application,
    AssessmentVersion,
    GitHubJob,
    GitHubWebhookDelivery,
    InfrastructurePlan,
    InfrastructurePlanFile,
    InfrastructurePlanValidation,
    InfrastructurePullRequest,
    PlanApproval,
    VerifiedBuild,
)

log = logging.getLogger(__name__)
NONBLOCKING_VERIFICATION_ERRORS = {
    "build_artifact_missing", "registry_digest_unverified", "merge_commit_sha_unavailable",
}


def _one_line(value: object, limit: int = 160) -> str:
    return " ".join(str(value).replace("`", "'").split())[:limit]


def _review_body(pr: InfrastructurePullRequest, plan: InfrastructurePlan, application: Application, approval: PlanApproval, files: dict[str, str], assessment: dict) -> str:
    lines = [
        f"Reviewed infrastructure for `{application.name}`.", "",
        f"- Source revision: `{pr.base_sha}`",
        f"- Target: `{plan.environment_profile_id}`",
        f"- Bundle SHA-256: `{plan.bundle_digest}`",
        f"- Approved by: `{approval.actor_id}`",
        "- Kubernetes admission: passed (server-side dry-run)",
        "- Image is provisional until post-merge CI verifies its digest.",
        "", "Assessment summary:", _one_line(assessment.get("summary") or "Repository evidence reviewed.", 500),
    ]
    evidence = {item.get("id"): item for item in assessment.get("evidence", []) if isinstance(item, dict)}
    findings = [item for item in assessment.get("findings", []) if isinstance(item, dict)][:12]
    if findings:
        lines.extend(("", "Evidence-backed findings:"))
    for finding in findings:
        category = _one_line(finding.get("category", "finding"), 80)
        if any(marker in category.lower() for marker in ("secret", "token", "password", "key")):
            continue
        value = _one_line(finding.get("value", "unknown"), 100)
        source = finding.get("status", "unknown")
        lines.append(f"- `{category}`: {value} ({_one_line(source, 30)})")
        for evidence_id in finding.get("evidence_ids", [])[:2]:
            item = evidence.get(evidence_id)
            if not item:
                continue
            path = str(item.get("file_path", ""))
            if path.startswith("/") or ".." in path.split("/"):
                continue
            line = item.get("line_start")
            suffix = f"#L{line}" if isinstance(line, int) and line > 0 else ""
            url = f"https://github.com/{quote(pr.owner)}/{quote(pr.repository)}/blob/{pr.base_sha}/{quote(path, safe='/')}{suffix}"
            lines.append(f"  - [Source evidence]({url})")
    assumptions = (assessment.get("unknowns") or [])[:8]
    if assumptions:
        lines.extend(("", "Open assumptions / unknowns:", *(_one_line(value) for value in assumptions)))
    lines.extend(("", "Generated files:", *(f"- `{path}`" for path in sorted(files)), "", "No deployment is performed by this PR."))
    return "\n".join(lines)


def _files(db, plan_id) -> dict[str, str]:
    rows = db.scalars(select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan_id)).all()
    files = {row.path: row.content for row in rows}
    if not rows or any(row.content_hash != hashlib.sha256(row.content.encode()).hexdigest() for row in rows):
        raise GitHubPhase6Error("stored_bundle_corrupt")
    return files


def _create(pr_id: UUID) -> None:
    with Session() as db:
        pr = db.get(InfrastructurePullRequest, pr_id)
        if pr is None:
            raise GitHubPhase6Error("pull_request_missing")
        plan = db.get(InfrastructurePlan, pr.plan_id)
        run = db.get(AnalysisRun, plan.analysis_run_id)
        application = db.get(Application, plan.application_id)
        assessment = db.get(AssessmentVersion, (run.id, plan.assessment_version))
        approval = db.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None)))
        validation = db.scalar(select(InfrastructurePlanValidation).where(InfrastructurePlanValidation.plan_id == plan.id).order_by(InfrastructurePlanValidation.created_at.desc(), InfrastructurePlanValidation.id.desc()))
        files = _files(db, plan.id)
        if (
            not approval or not assessment or approval.bundle_digest != plan.bundle_digest or plan.status != "approved"
            or not validation or validation.result_json.get("status") != "passed"
            or validation.bundle_digest != plan.bundle_digest or validation.environment_profile_hash != plan.environment_profile_hash
            or canonical_digest(files) != plan.bundle_digest or run.commit_sha != pr.base_sha
            or plan.template_version != TEMPLATE_VERSION
            or plan.environment_profile_hash != local_k3s_profile()["hash"]
        ):
            raise GitHubPhase6Error("plan_no_longer_approved")
        title = f"DeployFlow: bootstrap {application.name}"
        body = _review_body(pr, plan, application, approval, files, assessment.document)
        owner, repository, branch, base, expected_repo = pr.owner, pr.repository, pr.head_branch, pr.base_branch, pr.repository_id
    with GitHubAPI(settings(), owner, repository) as api:
        result = api.create_bundle_pull_request(branch=branch, base_branch=base, base_sha=pr.base_sha, files=files, title=title, body=body)
    if expected_repo is not None and expected_repo != result["repository_id"]:
        raise GitHubPhase6Error("github_repository_changed")
    with Session.begin() as db:
        pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
        pr.repository_id = result["repository_id"]
        pr.head_sha = result["head_sha"]
        pr.number = result["number"]
        pr.url = result["url"]
        pr.status = "open"
        pr.error_code = None
        db.add(GitHubJob(kind="reconcile", pull_request_id=pr.id, status="queued", attempts=0))


def _reconcile_one(pr_id: UUID) -> None:
    with Session() as db:
        pr = db.get(InfrastructurePullRequest, pr_id)
        if pr is None or pr.number is None or pr.repository_id is None:
            return
        plan = db.get(InfrastructurePlan, pr.plan_id)
        approval = db.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None)))
        files = _files(db, plan.id)
        if (
            not approval or approval.bundle_digest != plan.bundle_digest or plan.status != "approved"
            or canonical_digest(files) != plan.bundle_digest or plan.template_version != TEMPLATE_VERSION
            or plan.environment_profile_hash != local_k3s_profile()["hash"]
        ):
            raise GitHubPhase6Error("plan_no_longer_approved")
        repository_id, owner, repository, number = pr.repository_id, pr.owner, pr.repository, pr.number
        base_branch, base_sha, head_branch, head_sha = pr.base_branch, pr.base_sha, pr.head_branch, pr.head_sha
        image_repository = plan.validation_json["image_binding"]["repository"]
    with GitHubAPI(settings(), owner, repository) as api:
        pull = api.pull_request(number)
        if (
            api.repository_info().get("id") != repository_id
            or pull.get("base", {}).get("ref") != base_branch
            or pull.get("base", {}).get("repo", {}).get("id") != repository_id
            or pull.get("head", {}).get("ref") != head_branch
            or pull.get("head", {}).get("sha") != head_sha
        ):
            raise GitHubPhase6Error("github_pr_changed")
        if not pull.get("merged"):
            status = "closed" if pull.get("state") == "closed" else "open"
            with Session.begin() as db:
                row = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
                row.status = status
            return
        with Session.begin() as db:
            row = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
            row.status = "merged"
            row.error_code = None
        merged_sha = pull.get("merge_commit_sha")
        if merged_sha is None:
            merged_sha = api.merged_event_sha(number)
        checked_merged_files(api, base_sha=base_sha, merged_sha=merged_sha, files=files)
        with Session.begin() as db:
            row = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
            row.merged_sha = merged_sha
            row.status = "merged"
        runs = [run for run in api.workflow_runs(base_branch, merged_sha) if run.get("head_sha") == merged_sha]
        if not runs:
            return
        runs.sort(key=lambda row: (row.get("run_attempt", 0), row.get("id", 0)), reverse=True)
        run = api.workflow_run(runs[0]["id"])
        if run.get("status") != "completed":
            return
        if run.get("conclusion") != "success":
            with Session.begin() as db:
                row = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
                row.status = "ci_failed"
                row.error_code = "workflow_run_failed"
            return
        if not checked_workflow_run(run, repository_id=repository_id, merged_sha=merged_sha, base_branch=base_branch):
            raise GitHubPhase6Error("workflow_run_untrusted")
        artifacts = [row for row in api.run_artifacts(run["id"]) if row.get("name") == "deployflow-build" and not row.get("expired")]
        if len(artifacts) != 1 or artifacts[0].get("size_in_bytes", 0) > 128 * 1024:
            raise GitHubPhase6Error("build_artifact_missing")
        digest = checked_artifact(
            api.artifact_zip(artifacts[0]["id"]), repository_id=repository_id,
            commit_sha=merged_sha, run_id=run["id"], run_attempt=run["run_attempt"],
            image_repository=image_repository,
        )
        verify_registry_digest(image_repository, digest)
    with Session.begin() as db:
        row = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
        existing = db.scalar(select(VerifiedBuild).where(VerifiedBuild.pull_request_id == pr_id, VerifiedBuild.run_id == run["id"], VerifiedBuild.run_attempt == run["run_attempt"]))
        if existing and (existing.image_digest != digest or existing.merged_sha != merged_sha):
            raise GitHubPhase6Error("verified_build_conflict")
        if not existing:
            db.add(VerifiedBuild(pull_request_id=pr_id, run_id=run["id"], run_attempt=run["run_attempt"], merged_sha=merged_sha, image_repository=image_repository, image_digest=digest))
        row.status = "verified"
        row.error_code = None


def _reconcile(job: GitHubJob) -> None:
    if job.pull_request_id:
        _reconcile_one(job.pull_request_id)
        return
    with Session() as db:
        delivery = db.get(GitHubWebhookDelivery, job.delivery_id)
        pr_ids = db.scalars(select(InfrastructurePullRequest.id).where(InfrastructurePullRequest.repository_id == delivery.repository_id, InfrastructurePullRequest.status.in_({"open", "merged", "verified", "closed", "failed", "ci_failed"}))).all()
    for pr_id in pr_ids:
        try:
            _reconcile_one(pr_id)
        except GitHubPhase6Error as exc:
            if not exc.transient:
                with Session.begin() as db:
                    pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
                    pr.error_code = exc.code
                    if exc.code not in NONBLOCKING_VERIFICATION_ERRORS:
                        pr.status = "blocked"
            raise


def execute(job_id: str) -> None:
    identifier = UUID(job_id)
    with Session.begin() as db:
        job = db.get(GitHubJob, identifier, with_for_update=True)
        if job is None or job.status not in {"queued", "running"}:
            return
        now = db.scalar(select(func.now()))
        if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
            return
        job.status = "running"
        job.attempts += 1
        job.lease_expires_at = now + timedelta(minutes=5)
        kind, pr_id, delivery_id = job.kind, job.pull_request_id, job.delivery_id
    try:
        if kind == "create" and pr_id:
            _create(pr_id)
        elif kind == "reconcile" and (pr_id or delivery_id):
            with Session() as db:
                _reconcile(db.get(GitHubJob, identifier))
        else:
            raise GitHubPhase6Error("github_job_invalid")
    except GitHubPhase6Error as exc:
        log.warning("github_job_failed job_id=%s code=%s", identifier, exc.code)
        with Session.begin() as db:
            job = db.get(GitHubJob, identifier, with_for_update=True)
            job.error_code = exc.code
            if exc.transient and job.attempts < 5:
                job.status = "queued"
                job.next_dispatch_at = db.scalar(select(func.now())) + timedelta(seconds=min(30 * 2 ** (job.attempts - 1), 300))
            else:
                job.status = "failed"
                if pr_id:
                    pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
                    pr.error_code = exc.code
                    if kind == "create":
                        pr.status = "failed"
                    elif exc.code not in NONBLOCKING_VERIFICATION_ERRORS:
                        pr.status = "blocked"
        return
    except Exception:
        log.exception("github_job_unexpected job_id=%s", identifier)
        with Session.begin() as db:
            job = db.get(GitHubJob, identifier, with_for_update=True)
            job.error_code = "github_job_unexpected"
            job.status = "queued" if job.attempts < 5 else "failed"
            job.next_dispatch_at = db.scalar(select(func.now())) + timedelta(seconds=30)
            if job.status == "failed" and pr_id:
                pr = db.get(InfrastructurePullRequest, pr_id, with_for_update=True)
                pr.error_code = "github_job_unexpected"
                pr.status = "failed" if kind == "create" else "blocked"
        return
    with Session.begin() as db:
        job = db.get(GitHubJob, identifier, with_for_update=True)
        job.status = "completed"
        job.error_code = None
