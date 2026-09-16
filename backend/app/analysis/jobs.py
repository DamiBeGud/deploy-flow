import logging
import re
import shutil
import tempfile
import time
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.analysis.service import fake_assessment
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, AssessmentVersion, EvidenceItem, PendingJob
from app.repository.archive import extract_tarball
from app.repository.errors import RepositoryError
from app.repository.github import GitHubClient
from app.repository.scanner import Scanner
from app.telemetry.events import TERMINAL, emit

log = logging.getLogger(__name__)


def finish(db, run, status, message, error_code=None):
    run.status = status
    run.error_code = error_code
    run.finished_at = db.scalar(select(func.now()))
    run.lease_token = None
    run.lease_expires_at = None
    job = db.get(PendingJob, run.id)
    if job:
        db.delete(job)
    emit(db, run, f"analysis.{status}", message)


def retry_or_fail(db, run, code):
    run.lease_token = None
    run.lease_expires_at = None
    if run.attempts >= settings().max_attempts:
        finish(db, run, "failed", "Analysis failed after bounded attempts.", code)
    else:
        run.status = "queued"
        run.progress = 0
        run.error_code = code
        db.get(PendingJob, run.id).next_dispatch_at = db.scalar(select(func.now())) + timedelta(
            seconds=min(2**run.attempts, 30)
        )
        emit(db, run, "analysis.retrying", "Analysis will retry after an interrupted attempt.")


def claim(run_id: UUID) -> UUID | None:
    with Session.begin() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
        if not run or run.status != "queued":
            return None
        now = db.scalar(select(func.now()))
        if (now - run.created_at).total_seconds() >= settings().queue_timeout_seconds:
            finish(db, run, "failed", "Analysis exceeded its total queue deadline.", "job_timeout")
            return None
        job = db.get(PendingJob, run.id)
        if not job or run.attempts >= settings().max_attempts:
            return None
        # Retry deliveries must honor the durable retry backoff. Initial delivery
        # ignores next_dispatch_at, which the dispatcher advanced before publish.
        if run.error_code and job.next_dispatch_at > now:
            return None
        run.status = "running"
        run.attempts += 1
        run.lease_token = uuid4()
        run.lease_expires_at = now + timedelta(seconds=settings().job_timeout_seconds)
        run.attempt_deadline_at = now + timedelta(seconds=settings().analysis_attempt_seconds)
        run.error_code = None
        emit(db, run, "analysis.started", "Starting simulated repository analysis.")
        log.info("analysis_started run_id=%s correlation_id=%s", run.id, run.correlation_id)
        return run.lease_token


def advance(run_id, token, kind, percent, *, complete=False):
    with Session.begin() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
        if not run or run.status != "running" or run.lease_token != token:
            return False
        now = db.scalar(select(func.now()))
        if (now - run.created_at).total_seconds() >= settings().queue_timeout_seconds:
            finish(db, run, "failed", "Analysis exceeded its total queue deadline.", "job_timeout")
            return False
        if run.lease_expires_at <= now:
            retry_or_fail(db, run, "attempt_timeout")
            return False
        run.progress = percent
        if complete:
            run.assessment_version = 1
            db.add(
                AssessmentVersion(
                    run_id=run.id, version=1, document=fake_assessment(), actor_id="fake-analysis"
                )
            )
            finish(db, run, "completed", "Simulated assessment ready for review.")
        else:
            emit(db, run, kind, "Simulating " + kind.removeprefix("analysis.") + ".")
        return True


def execute(run_id: str):
    identifier = UUID(run_id)
    token = claim(identifier)
    if token is None:
        return
    try:
        if settings().analysis_provider == "github":
            execute_repository(identifier, token)
            return
        stages = [
            ("analysis.scanning", 25),
            ("analysis.agent_running", 55),
            ("analysis.validating", 85),
            ("analysis.completed", 100),
        ]
        for kind, progress in stages:
            time.sleep(settings().fake_step_seconds)
            if not advance(identifier, token, kind, progress, complete=progress == 100):
                return
    except RepositoryError as exc:
        with Session.begin() as db:
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update())
            if run and run.status not in TERMINAL and run.lease_token == token:
                if exc.retryable:
                    retry_or_fail(db, run, exc.code)
                else:
                    finish(db, run, "failed", "Repository analysis could not be completed.", exc.code)
    except Exception:
        # Do not log source contents, exception messages, credentials or request bodies.
        log.error("analysis_attempt_failed run_id=%s", identifier)
        with Session.begin() as db:
            run = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if run and run.status not in TERMINAL and run.lease_token == token:
                retry_or_fail(db, run, "analysis_error")
        # Process termination / database outage is recovered by the lease reaper.


def execute_repository(identifier: UUID, token: UUID):
    with Session() as db:
        run = db.get(AnalysisRun, identifier)
        application = db.get(__import__("app.persistence.models", fromlist=["Application"]).Application, run.application_id)
        url = application.repository_url
        name = application.name
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", url)
    if not match:
        raise RepositoryError()
    client = GitHubClient(settings())
    resolved = client.resolve(match.group(1), match.group(2), run.revision)
    workspace = None
    try:
        workspace = tempfile.mkdtemp(prefix=f"deployflow-{identifier}-")
        root = __import__("pathlib", fromlist=["Path"]).Path(workspace) / "repository"
        with Session.begin() as db:
            current = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update())
            if not current or current.lease_token != token or current.status != "running":
                return
            current.progress = 15
            current.commit_sha = resolved.commit_sha
            emit(db, current, "analysis.checkout_started", "Resolving and preparing the pinned repository revision.")
        extract_tarball(client.download(resolved), root, compressed_limit=settings().repository_archive_bytes, extracted_limit=settings().repository_extracted_bytes, file_limit=settings().repository_file_count, path_limit=settings().repository_path_length)
        document = Scanner(root).scan(repository={"url": resolved.url, "owner": resolved.owner, "name": resolved.name, "revision": resolved.revision, "commit_sha": resolved.commit_sha}, application_name=name)
        with Session.begin() as db:
            current = db.scalar(select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update())
            if not current or current.lease_token != token or current.status != "running":
                return
            current.progress = 85
            emit(db, current, "analysis.scanning", "Scanning repository files without executing application code.")
            for evidence in document["evidence"]:
                db.add(EvidenceItem(run_id=current.id, file_path=evidence["file_path"], line_start=evidence["line_start"], line_end=evidence["line_end"], excerpt_hash=evidence["excerpt_hash"], detector=evidence["detector"]))
            emit(db, current, "analysis.validating", "Validating deterministic findings and evidence.")
            current.assessment_version = 1
            current.incomplete_reason = document["incomplete_reason"]
            current.reproducibility_json = {"scanner_version": "phase2-v1", "github_api_version": settings().github_api_version, "commit_sha": resolved.commit_sha}
            db.add(AssessmentVersion(run_id=current.id, version=1, document=document, actor_id="deterministic-scanner"))
            current.progress = 100
            finish(db, current, "completed", "Repository assessment ready for review.")
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
