import logging
import time
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.analysis.service import fake_assessment
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, AssessmentVersion, PendingJob
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
