import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.analysis.jobs import finish
from app.analysis.service import enqueue, run_document
from app.api.identity import Identity, get_identity
from app.api.schemas import AssessmentPatch, CreateApplication, StartAnalysis
from app.persistence.commands import application_for, idempotent, run_for
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, Application, AssessmentVersion, Event
from app.telemetry.events import TERMINAL, emit

router = APIRouter(prefix="/api/v1")
Actor = Annotated[Identity, Depends(get_identity)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


@router.get("/me")
def me(identity: Actor):
    return {
        "actor_id": identity.actor_id,
        "organization_id": identity.organization_id,
        "roles": sorted(identity.roles),
        "authentication": "development",
        "clusters": [],
    }


@router.post("/applications", status_code=201)
def create_application(body: CreateApplication, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:

        def create():
            app = Application(
                **body.model_dump(),
                organization_id=identity.organization_id,
                owner_id=identity.actor_id,
            )
            db.add(app)
            db.flush()
            return {
                "id": str(app.id),
                **body.model_dump(),
                "created_at": app.created_at.isoformat(),
            }, 201

        response, code = idempotent(
            db, identity, "applications.create", key, body.model_dump(), create
        )
    return JSONResponse(response, status_code=code)


@router.get("/applications")
def list_applications(identity: Actor, limit: int = Query(default=50, ge=1, le=100)):
    with Session() as db:
        query = select(Application).where(Application.organization_id == identity.organization_id)
        if "administrator" not in identity.roles:
            query = query.where(Application.owner_id == identity.actor_id)
        apps = db.scalars(query.order_by(Application.created_at.desc()).limit(limit)).all()
        return [
            {
                "id": str(app.id),
                "name": app.name,
                "repository_url": app.repository_url,
                "revision": app.revision,
            }
            for app in apps
        ]


@router.get("/applications/{app_id}")
def get_application(app_id: UUID, identity: Actor):
    with Session() as db:
        app = application_for(db, app_id, identity)
        latest = db.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.application_id == app.id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
        return {
            "id": str(app.id),
            "name": app.name,
            "repository_url": app.repository_url,
            "revision": app.revision,
            "latest_analysis": run_document(db, latest) if latest else None,
        }


@router.post("/applications/{app_id}/analyses", status_code=202)
def start_analysis(app_id: UUID, body: StartAnalysis, request: Request, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        application = application_for(db, app_id, identity)
        response, code = idempotent(
            db,
            identity,
            f"applications/{app_id}/analyses",
            key,
            body.model_dump(),
            lambda: enqueue(db, application, identity, request.state.correlation_id, body.revision),
        )
    return JSONResponse(response, status_code=code)


@router.get("/analyses/{run_id}")
def get_analysis(run_id: UUID, identity: Actor):
    with Session() as db:
        return run_document(db, run_for(db, run_id, identity))


@router.post("/analyses/{run_id}/reanalyze", status_code=202)
def reanalyze(run_id: UUID, body: StartAnalysis, request: Request, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        old = run_for(db, run_id, identity)
        application = application_for(db, old.application_id, identity)
        response, code = idempotent(
            db,
            identity,
            f"analyses/{run_id}/reanalyze",
            key,
            body.model_dump(),
            lambda: enqueue(
                db,
                application,
                identity,
                request.state.correlation_id,
                body.revision or old.revision,
            ),
        )
    return JSONResponse(response, status_code=code)


@router.post("/analyses/{run_id}/cancel")
def cancel(run_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        run = run_for(db, run_id, identity, lock=True)

        def operation():
            if run.status == "cancelled":
                return {"run_id": str(run.id), "status": run.status}, 200
            if run.status in TERMINAL:
                raise HTTPException(409, "A finished analysis cannot be cancelled")
            finish(db, run, "cancelled", "Analysis cancelled by user.")
            return {"run_id": str(run.id), "status": run.status}, 200

        response, code = idempotent(db, identity, f"analyses/{run_id}/cancel", key, {}, operation)
    return JSONResponse(response, status_code=code)


@router.patch("/analyses/{run_id}/assessment")
def patch_assessment(run_id: UUID, body: AssessmentPatch, identity: Actor, key: Key):
    identity.require_write()
    changes = body.model_dump(exclude_none=True, exclude={"expected_version"})
    if not changes:
        raise HTTPException(422, "Provide port or health_path")
    with Session.begin() as db:
        run = run_for(db, run_id, identity, lock=True)

        def operation():
            if run.status != "completed":
                raise HTTPException(409, "Assessment is not ready for review")
            if run.assessment_version != body.expected_version:
                raise HTTPException(409, "Assessment version changed; reload before editing")
            previous = db.get(AssessmentVersion, (run.id, run.assessment_version))
            document = {**previous.document}
            for field, value in changes.items():
                document[field] = {"value": value, "source": "user"}
            run.assessment_version += 1
            db.add(
                AssessmentVersion(
                    run_id=run.id,
                    version=run.assessment_version,
                    document=document,
                    actor_id=identity.actor_id,
                )
            )
            emit(db, run, "analysis.assessment_updated", "User corrections saved as a new version.")
            return {
                "run_id": str(run.id),
                "version": run.assessment_version,
                "assessment": document,
            }, 200

        response, code = idempotent(
            db, identity, f"analyses/{run_id}/assessment", key, body.model_dump(), operation
        )
    return JSONResponse(response, status_code=code)


def event_snapshot(run_id, identity, cursor):
    with Session() as db:
        run = run_for(db, run_id, identity)
        if cursor > run.event_sequence:
            raise HTTPException(400, "Event cursor is ahead of this run")
        # Bound to the observed sequence: if completion commits between these two
        # reads, its events and terminal status are returned together next poll.
        rows = db.scalars(
            select(Event)
            .where(
                Event.run_id == run_id,
                Event.event_id > cursor,
                Event.event_id <= run.event_sequence,
            )
            .order_by(Event.event_id)
            .limit(100)
        ).all()
        events = [
            {
                "event_id": e.event_id,
                "run_id": str(e.run_id),
                "type": e.kind,
                "occurred_at": e.occurred_at.isoformat(),
                **e.payload,
            }
            for e in rows
        ]
        return events, run.status in TERMINAL, run.event_sequence


@router.get("/analyses/{run_id}/events")
async def events(
    run_id: UUID,
    request: Request,
    identity: Actor,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: int = Query(default=0, ge=0),
):
    try:
        cursor = int(last_event_id) if last_event_id is not None else after
        if cursor < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Last-Event-ID must be a non-negative integer") from None
    initial = await run_in_threadpool(event_snapshot, run_id, identity, cursor)

    async def stream():
        nonlocal cursor
        snapshot = initial
        ticks = 0
        while not await request.is_disconnected():
            batch, terminal, last_sequence = snapshot
            for event in batch:
                cursor = event["event_id"]
                data = json.dumps(jsonable_encoder(event))
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n"
            if terminal and cursor >= last_sequence:
                return
            ticks += 1
            if ticks % 20 == 0:
                yield ": heartbeat\n\n"
            if cursor >= last_sequence:
                await asyncio.sleep(0.5)
            snapshot = await run_in_threadpool(event_snapshot, run_id, identity, cursor)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
