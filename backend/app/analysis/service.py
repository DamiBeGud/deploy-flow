from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import Assessment, Finding
from app.bootstrap.managed_rails import managed_profile
from app.config import Settings, settings
from app.persistence.models import AnalysisRun, Application, AssessmentVersion, Event, PendingJob
from app.telemetry.events import emit


def analysis_mode(application, actor_id, revision, *, config: Settings | None = None):
    config = config if config is not None else settings()
    study = config.study_agent_pin(application.id)
    if study is not None:
        if not study.admits(application, actor_id, revision):
            raise HTTPException(403, "Study analysis requires the approved application owner and source.")
        return "agent"
    profile = managed_profile() if config.managed_rails_agent_enabled else None
    if profile and profile.admits(application, actor_id, revision):
        return "agent"
    return config.resolved_analysis_mode


def enqueue(db: Session, application, identity, correlation_id, revision):
    revision = revision or application.revision
    config = settings()
    mode = analysis_mode(application, identity.actor_id, revision, config=config)
    pin = config.study_agent_pin(application.id)
    if pin:
        # Serialize claims for an existing app. The same logical run retains its normal worker retry path.
        db.execute(select(Application.id).where(Application.id == application.id).with_for_update()).scalar_one()
        existing = db.scalar(
            select(AnalysisRun.id)
            .where(AnalysisRun.application_id == application.id, AnalysisRun.analysis_mode == "agent")
            .limit(1)
        )
        if existing is not None:
            raise HTTPException(409, "This study assignment already has an agent analysis.")
    run = AnalysisRun(
        application_id=application.id,
        actor_id=identity.actor_id,
        correlation_id=correlation_id,
        revision=revision,
        status="queued",
        progress=0,
        event_sequence=0,
        analysis_mode=mode,
        reproducibility_json={"study_scope_sha256": pin.fingerprint()} if pin else None,
    )
    db.add(run)
    db.flush()
    db.add(PendingJob(run_id=run.id, next_dispatch_at=db.scalar(select(func.now()))))
    mode_message = {
        "fake": "Fake analysis queued; no repository access will occur.",
        "deterministic": "Repository scan queued; application code will not execute.",
        "agent": "Agent analysis queued; selected repository excerpts may be sent to DeepSeek.",
    }[run.analysis_mode]
    emit(db, run, "analysis.queued", mode_message)
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
    failure_event = db.scalar(
        select(Event)
        .where(Event.run_id == run.id, Event.kind == "analysis.failed")
        .order_by(Event.event_id.desc())
        .limit(1)
    )
    return {
        "id": str(run.id),
        "run_id": str(run.id),
        "application_id": str(run.application_id),
        "status": run.status,
        "progress": run.progress,
        "revision": run.revision,
        "commit_sha": run.commit_sha,
        "analysis_mode": run.analysis_mode,
        "completion": assessment.document.get("completion") if assessment else None,
        "incomplete_reason": run.incomplete_reason,
        "reproducibility": run.reproducibility_json,
        "attempts": run.attempts,
        "assessment_version": run.assessment_version,
        "assessment": assessment.document if assessment else None,
        "error_code": run.error_code,
        "error_message": failure_event.payload.get("message") if failure_event else None,
        "correlation_id": run.correlation_id,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
