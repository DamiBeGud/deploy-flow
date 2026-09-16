import hashlib
import json
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.identity import Identity
from app.persistence.models import AnalysisRun, Application, IdempotencyRecord


def application_for(db: Session, app_id, identity: Identity) -> Application:
    app = db.get(Application, app_id)
    if (
        app is None
        or app.organization_id != identity.organization_id
        or (app.owner_id != identity.actor_id and "administrator" not in identity.roles)
    ):
        raise HTTPException(404, "Application not found")
    return app


def run_for(db: Session, run_id, identity: Identity, *, lock=False) -> AnalysisRun:
    query = select(AnalysisRun).where(AnalysisRun.id == run_id)
    if lock:
        query = query.with_for_update()
    run = db.scalar(query)
    if run is None:
        raise HTTPException(404, "Analysis not found")
    application_for(db, run.application_id, identity)
    return run


def idempotent(
    db: Session,
    identity: Identity,
    action: str,
    key: str,
    body: dict,
    operation: Callable[[], tuple[dict, int]],
) -> tuple[dict, int]:
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    scope = dict(
        organization_id=identity.organization_id, actor_id=identity.actor_id, action=action, key=key
    )
    # Concurrent inserts wait for the winning transaction to commit. A rollback
    # releases the key, so no incomplete reservation can outlive its command.
    inserted = db.scalar(
        insert(IdempotencyRecord)
        .values(**scope, request_hash=digest)
        .on_conflict_do_nothing(constraint="uq_idempotency")
        .returning(IdempotencyRecord.id)
    )
    record = db.scalar(select(IdempotencyRecord).filter_by(**scope).with_for_update())
    if record.request_hash != digest:
        raise HTTPException(409, "Idempotency key was already used with a different request")
    if inserted is None:
        return record.response, record.status_code
    response, status_code = operation()
    record.response = response
    record.status_code = status_code
    return response, status_code
