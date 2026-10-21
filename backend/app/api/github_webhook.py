"""Signed GitHub App webhook inbox. The request handler has no GitHub write key."""

import hashlib
import hmac
import json
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.persistence.database import Session
from app.persistence.models import GitHubJob, GitHubWebhookDelivery

router = APIRouter()


@router.post("/webhooks/github", status_code=202)
async def github_webhook(request: Request):
    secret = settings().github_webhook_secret
    if not secret:
        raise HTTPException(503, "GitHub webhook is not configured")
    try:
        declared_length = int(request.headers.get("content-length", "0") or "0")
    except ValueError:
        raise HTTPException(400, "Invalid webhook content length") from None
    if declared_length > 1024 * 1024:
        raise HTTPException(413, "Webhook payload too large")
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(413, "Webhook payload too large")
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery_id):
        raise HTTPException(400, "Invalid GitHub delivery ID")
    event = request.headers.get("X-GitHub-Event", "")
    if event not in {"pull_request", "workflow_run"}:
        return {"accepted": True, "ignored": True}
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid GitHub webhook JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid GitHub webhook payload")
    if event == "workflow_run" and payload.get("action") != "completed":
        return {"accepted": True, "ignored": True}
    repository_id = (payload.get("repository") or {}).get("id")
    installation_id = (payload.get("installation") or {}).get("id")
    if type(repository_id) is not int or installation_id != settings().github_installation_id:
        raise HTTPException(400, "GitHub webhook repository or installation is invalid")
    with Session.begin() as db:
        inserted = db.execute(
            insert(GitHubWebhookDelivery)
            .values(delivery_id=delivery_id, event=event, repository_id=repository_id)
            .on_conflict_do_nothing(index_elements=[GitHubWebhookDelivery.delivery_id])
            .returning(GitHubWebhookDelivery.delivery_id)
        ).scalar_one_or_none()
        if inserted:
            db.add(GitHubJob(kind="reconcile", delivery_id=delivery_id, status="queued", attempts=0))
            db.add(GitHubJob(kind="source_reconcile", delivery_id=delivery_id, status="queued", attempts=0))
    return {"accepted": True, "duplicate": inserted is None}
