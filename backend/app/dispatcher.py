import logging
import signal
import time
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.analysis.jobs import finish, retry_or_fail
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, PendingJob
from app.worker import analyze

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
        except Exception:
            log.error("dispatcher_database_unavailable")
        time.sleep(1)


if __name__ == "__main__":
    main()
