import logging
import signal
import time
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.analysis.jobs import finish, retry_or_fail
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, DeliveryJob, ExecutionJob, GitHubJob, PendingJob
from app.worker import analyze, cleanup_job, delivery_job, execution_job, github_job

log = logging.getLogger(__name__)


def dispatch_once(publish=analyze.send) -> int:
    dispatched = 0
    # Lock runs in the same order as workers and API mutations. SKIP LOCKED lets
    # more than one dispatcher run without holding up completions or cancellation.
    with Session.begin() as db:
        now = db.scalar(select(func.now()))
        runs = db.scalars(
            select(AnalysisRun)
            .join(PendingJob)
            .where(
                or_(
                    (AnalysisRun.status == "queued") & (PendingJob.next_dispatch_at <= now),
                    (AnalysisRun.status == "running") & (AnalysisRun.lease_expires_at <= now),
                )
            )
            .order_by(PendingJob.next_dispatch_at)
            .limit(20)
            .with_for_update(of=AnalysisRun, skip_locked=True)
        ).all()
        for run in runs:
            if (now - run.created_at).total_seconds() >= settings().queue_timeout_seconds:
                finish(
                    db, run, "failed", "Analysis exceeded its total queue deadline.", "job_timeout"
                )
                continue
            if run.status == "running":
                retry_or_fail(db, run, "lease_expired")
                continue
            job = db.get(PendingJob, run.id)
            try:
                publish(str(run.id))
            except Exception:
                log.warning(
                    "job_publish_unavailable run_id=%s correlation_id=%s",
                    run.id,
                    run.correlation_id,
                )
                # No outcome is lost; retry on the next dispatcher tick.
                break
            run.error_code = None
            job.publish_attempts += 1
            job.next_dispatch_at = now + timedelta(seconds=settings().redispatch_seconds)
            dispatched += 1
    return dispatched


def dispatch_github_once(publish=github_job.send) -> int:
    dispatched = 0
    with Session.begin() as db:
        now = db.scalar(select(func.now()))
        jobs = db.scalars(
            select(GitHubJob)
            .where(
                or_(
                    (GitHubJob.status == "queued") & (GitHubJob.next_dispatch_at <= now),
                    (GitHubJob.status == "running") & (GitHubJob.lease_expires_at <= now),
                )
            )
            .order_by(GitHubJob.next_dispatch_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            try:
                publish(str(job.id))
            except Exception:
                log.warning("github_job_publish_unavailable job_id=%s", job.id)
                break
            job.next_dispatch_at = now + timedelta(seconds=settings().redispatch_seconds)
            dispatched += 1
    return dispatched


def dispatch_delivery_once(publish=delivery_job.send) -> int:
    dispatched = 0
    with Session.begin() as db:
        now = db.scalar(select(func.now()))
        jobs = db.scalars(
            select(DeliveryJob)
            .where(or_(
                (DeliveryJob.status.in_({"queued", "retry_wait"})) & (DeliveryJob.next_dispatch_at <= now),
                (DeliveryJob.status == "running") & (DeliveryJob.lease_expires_at <= now),
            ))
            .order_by(DeliveryJob.next_dispatch_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            try:
                publish(str(job.id))
            except Exception:
                log.warning("delivery_job_publish_unavailable job_id=%s", job.id)
                break
            # The retry is due and published. Queue it before advancing the
            # publication throttle, otherwise the worker mistakes that new
            # timestamp for an unexpired retry backoff and refuses the claim.
            if job.status == "retry_wait":
                job.status = "queued"
            job.next_dispatch_at = now + timedelta(seconds=settings().redispatch_seconds)
            dispatched += 1
    return dispatched


def dispatch_execution_once(publish=execution_job.send, cleanup_publish=cleanup_job.send) -> int:
    if not settings().deployment_execution_enabled:
        return 0
    dispatched = 0
    with Session.begin() as db:
        now = db.scalar(select(func.now()))
        jobs = db.scalars(select(ExecutionJob).where(or_(
            (ExecutionJob.status.in_({"queued", "retry_wait"})) & (ExecutionJob.next_dispatch_at <= now),
            (ExecutionJob.status == "running") & (ExecutionJob.lease_expires_at <= now),
        )).order_by(ExecutionJob.next_dispatch_at).limit(20).with_for_update(skip_locked=True)).all()
        for job in jobs:
            if job.purpose == "cleanup" and not settings().deployment_cleanup_enabled:
                continue
            try:
                (cleanup_publish if job.purpose == "cleanup" else publish)(str(job.id))
            except Exception:
                log.warning("execution_job_publish_unavailable job_id=%s", job.id)
                break
            if job.status == "retry_wait":
                job.status = "queued"
            job.next_dispatch_at = now + timedelta(seconds=settings().redispatch_seconds)
            dispatched += 1
    return dispatched


def main():
    logging.basicConfig(level=logging.INFO)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            dispatch_once()
            dispatch_github_once()
            dispatch_delivery_once()
            dispatch_execution_once()
        except Exception:
            log.error("dispatcher_database_unavailable")
        time.sleep(1)


if __name__ == "__main__":
    main()
