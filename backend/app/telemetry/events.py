from sqlalchemy.orm import Session

from app.persistence.models import AnalysisRun, Event

TERMINAL = {"completed", "failed", "cancelled"}


def emit(db: Session, run: AnalysisRun, kind: str, message: str):
    # Caller holds FOR UPDATE on the run (or created it in this transaction).
    run.event_sequence = (run.event_sequence or 0) + 1
    db.add(
        Event(
            run_id=run.id,
            event_id=run.event_sequence,
            kind=kind,
            payload={
                "stage": kind.removeprefix("analysis."),
                "percent": run.progress or 0,
                "message": message,
                "correlation_id": run.correlation_id,
                "actor_id": run.actor_id,
            },
        )
    )
