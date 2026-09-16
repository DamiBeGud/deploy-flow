from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import Assessment, Finding
from app.persistence.models import AnalysisRun, AssessmentVersion, PendingJob
from app.telemetry.events import emit


def enqueue(db: Session, application, identity, correlation_id, revision):
    run = AnalysisRun(
        application_id=application.id,
        actor_id=identity.actor_id,
        correlation_id=correlation_id,
        revision=revision or application.revision,
        status="queued",
        progress=0,
        event_sequence=0,
    )
    db.add(run)
    db.flush()
    db.add(PendingJob(run_id=run.id, next_dispatch_at=db.scalar(select(func.now()))))
    emit(db, run, "analysis.queued", "Fake analysis queued; no repository access will occur.")
    return {
        "run_id": str(run.id),
        "status": "queued",
        "events_url": f"/api/v1/analyses/{run.id}/events",
    }, 202


def fake_assessment() -> dict:
    return Assessment(
        runtime=Finding(value="nodejs", source="mock"),
        port=Finding(value=4587, source="mock"),
        health_path=Finding(value="/healthz", source="mock"),
        environment_variable_names=["PORT", "HOST", "NODE_ENV", "APP_GREETING"],
        unknowns=[
            "This is a fixture demonstration, not repository evidence.",
            "Repository revision has not been resolved to a commit SHA.",
        ],
    ).model_dump()


def run_document(db: Session, run: AnalysisRun):
    assessment = db.get(AssessmentVersion, (run.id, run.assessment_version))
    return {
        "id": str(run.id),
        "run_id": str(run.id),
        "application_id": str(run.application_id),
        "status": run.status,
        "progress": run.progress,
        "revision": run.revision,
        "commit_sha": None,
        "analysis_mode": "fake",
        "attempts": run.attempts,
        "assessment_version": run.assessment_version,
        "assessment": assessment.document if assessment else None,
        "error_code": run.error_code,
        "correlation_id": run.correlation_id,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
