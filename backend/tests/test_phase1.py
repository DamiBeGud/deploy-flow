from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.analysis.jobs import advance, claim, execute
from app.api.identity import Identity, get_identity
from app.config import Settings, settings
from app.dispatcher import dispatch_once
from app.main import app
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, Application, AssessmentVersion, Event, PendingJob


def application(client, key="app", name="fixture"):
    response = client.post(
        "/api/v1/applications",
        headers={"Idempotency-Key": key},
        json={
            "name": name,
            "repository_url": "https://github.com/DamiBeGud/deployflow-node-express-fixture",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def queue(client, app_id=None, key="analysis"):
    app_id = app_id or application(client)
    response = client.post(
        f"/api/v1/applications/{app_id}/analyses",
        headers={"Idempotency-Key": key, "X-Correlation-ID": "test-correlation"},
        json={},
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def expire(run_id, *, lease=False):
    with Session.begin() as db:
        run = db.get(AnalysisRun, UUID(run_id))
        past = db.scalar(select(func.now())) - timedelta(seconds=1)
        if lease:
            run.lease_expires_at = past
        else:
            db.get(PendingJob, run.id).next_dispatch_at = past


def test_concurrent_idempotency_and_conflict(client):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: application(client), range(8)))
    assert len(set(ids)) == 1
    app_id = ids[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: queue(client, app_id), range(8)))
    assert len(set(runs)) == 1
    response = client.post(
        f"/api/v1/applications/{app_id}/analyses",
        headers={"Idempotency-Key": "analysis"},
        json={"revision": "other"},
    )
    assert response.status_code == 409
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1
        assert db.scalar(select(func.count()).select_from(PendingJob)) == 1
        assert db.scalar(select(func.count()).select_from(Event)) == 1


def test_completion_replay_and_duplicate_delivery(client):
    run_id = queue(client)
    execute(run_id)
    execute(run_id)
    result = client.get(f"/api/v1/analyses/{run_id}").json()
    assert result["status"] == "completed"
    assert result["attempts"] == 1
    assert result["assessment"]["mode"] == "fake"
    assert result["assessment"]["port"] == {"value": 4587, "source": "mock"}
    events = client.get(f"/api/v1/analyses/{run_id}/events").text
    ids = [int(line[4:]) for line in events.splitlines() if line.startswith("id: ")]
    assert ids == list(range(1, 7))
    replay = client.get(f"/api/v1/analyses/{run_id}/events", headers={"Last-Event-ID": "3"}).text
    assert "id: 3\n" not in replay and "id: 4\n" in replay
    assert "analysis.completed" in replay
    assert client.get(f"/api/v1/analyses/{run_id}/events?after=6").text == ""
    assert client.get(f"/api/v1/analyses/{run_id}/events?after=99").status_code == 400
    assert (
        client.get(
            f"/api/v1/analyses/{run_id}/events", headers={"Last-Event-ID": "bad"}
        ).status_code
        == 400
    )
    with Session() as db:
        assert db.get(PendingJob, UUID(run_id)) is None


def test_worker_crash_recovery_fences_old_worker(client):
    run_id = queue(client)
    old_token = claim(UUID(run_id))
    assert old_token
    assert claim(UUID(run_id)) is None
    expire(run_id, lease=True)
    dispatch_once(lambda _: None)
    expire(run_id)
    sent = []
    assert dispatch_once(sent.append) == 1
    assert sent == [run_id]
    new_token = claim(UUID(run_id))
    assert new_token and new_token != old_token
    assert not advance(UUID(run_id), old_token, "analysis.completed", 100, complete=True)
    assert advance(UUID(run_id), new_token, "analysis.completed", 100, complete=True)
    assert client.get(f"/api/v1/analyses/{run_id}").json()["attempts"] == 2


def test_redis_publish_failure_and_lost_delivery_recover(client):
    run_id = queue(client)

    def unavailable(_):
        raise ConnectionError("Simulated unavailable Redis")

    assert dispatch_once(unavailable) == 0
    sent = []
    assert dispatch_once(sent.append) == 1
    # Simulate an acknowledged publish whose Redis message was subsequently lost.
    expire(run_id)
    assert dispatch_once(sent.append) == 1
    assert sent == [run_id, run_id]
    execute(sent[-1])
    assert client.get(f"/api/v1/analyses/{run_id}").json()["status"] == "completed"


def test_cancellation_fences_worker_and_is_idempotent(client):
    run_id = queue(client)
    token = claim(UUID(run_id))
    for _ in range(2):
        response = client.post(
            f"/api/v1/analyses/{run_id}/cancel", headers={"Idempotency-Key": "cancel"}
        )
        assert response.status_code == 200
    assert not advance(UUID(run_id), token, "analysis.completed", 100, complete=True)
    execute(run_id)
    assert client.get(f"/api/v1/analyses/{run_id}").json()["status"] == "cancelled"


def test_bounded_crash_retries(client):
    run_id = queue(client)
    for _ in range(settings().max_attempts):
        assert claim(UUID(run_id))
        expire(run_id, lease=True)
        dispatch_once(lambda _: None)
        with Session() as db:
            if db.get(PendingJob, UUID(run_id)):
                expire(run_id)
    result = client.get(f"/api/v1/analyses/{run_id}").json()
    assert result["status"] == "failed"
    assert result["attempts"] == settings().max_attempts
    assert result["error_code"] == "lease_expired"


def test_attempt_deadline_and_total_deadline(client):
    run_id = queue(client)
    token = claim(UUID(run_id))
    expire(run_id, lease=True)
    assert not advance(UUID(run_id), token, "analysis.completed", 100, complete=True)
    with Session.begin() as db:
        run = db.get(AnalysisRun, UUID(run_id))
        run.created_at -= timedelta(seconds=settings().queue_timeout_seconds + 1)
    expire(run_id)
    dispatch_once(lambda _: None)
    result = client.get(f"/api/v1/analyses/{run_id}").json()
    assert result["status"] == "failed" and result["error_code"] == "job_timeout"


def test_review_versions_and_reanalysis(client):
    run_id = queue(client)
    execute(run_id)
    path = f"/api/v1/analyses/{run_id}/assessment"
    for _ in range(2):
        response = client.patch(
            path, headers={"Idempotency-Key": "edit"}, json={"expected_version": 1, "port": 8080}
        )
        assert response.status_code == 200
        assert response.json()["version"] == 2
    assert (
        client.patch(
            path, headers={"Idempotency-Key": "stale"}, json={"expected_version": 1, "port": 8081}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            path, headers={"Idempotency-Key": "bad"}, json={"expected_version": 2, "port": 70000}
        ).status_code
        == 422
    )
    with Session() as db:
        assert db.get(AssessmentVersion, (UUID(run_id), 1)).document["port"]["value"] == 4587
        assert db.get(AssessmentVersion, (UUID(run_id), 2)).document["port"]["source"] == "user"
    new = client.post(
        f"/api/v1/analyses/{run_id}/reanalyze", headers={"Idempotency-Key": "again"}, json={}
    )
    assert new.status_code == 202 and new.json()["run_id"] != run_id


@pytest.mark.parametrize(
    "identity",
    [
        Identity("other", "local-organization", frozenset({"developer"})),
        Identity("local-developer", "other-organization", frozenset({"administrator"})),
    ],
)
def test_ownership_every_resource(client, identity):
    app_id = application(client)
    run_id = queue(client, app_id)
    app.dependency_overrides[get_identity] = lambda: identity
    for path in [f"applications/{app_id}", f"analyses/{run_id}", f"analyses/{run_id}/events"]:
        assert client.get(f"/api/v1/{path}").status_code == 404
    assert client.get("/api/v1/applications").json() == []
    assert (
        client.post(
            f"/api/v1/analyses/{run_id}/cancel", headers={"Idempotency-Key": "no"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/applications/{app_id}/analyses", headers={"Idempotency-Key": "no"}, json={}
        ).status_code
        == 404
    )


def test_viewer_and_untrusted_identity_headers(client):
    app_id = application(client)
    response = client.get(
        "/api/v1/me", headers={"X-Actor-ID": "attacker", "X-Roles": "administrator"}
    )
    assert response.json()["actor_id"] == "local-developer"
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"viewer"})
    )
    assert client.get(f"/api/v1/applications/{app_id}").status_code == 200
    assert (
        client.post(
            f"/api/v1/applications/{app_id}/analyses", headers={"Idempotency-Key": "no"}, json={}
        ).status_code
        == 403
    )


def test_atomic_rollback(client, monkeypatch):
    app_id = application(client)
    import app.analysis.service as service

    def fail_after_write(*args, **kwargs):
        raise RuntimeError("Simulated failure after run and job insertion")

    monkeypatch.setattr(service, "emit", fail_after_write)
    response = client.post(
        f"/api/v1/applications/{app_id}/analyses", headers={"Idempotency-Key": "atomic"}, json={}
    )
    assert response.status_code == 500
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 0
        assert db.scalar(select(func.count()).select_from(PendingJob)) == 0


def test_actor_scoping_of_idempotency(client):
    first = application(client)
    app.dependency_overrides[get_identity] = lambda: Identity(
        "second", "local-organization", frozenset({"developer"})
    )
    assert application(client) != first
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Application)) == 2


def test_errors_and_configuration(client):
    response = client.post("/api/v1/applications", json={})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["X-Correlation-ID"]
    assert client.get(f"/api/v1/analyses/{uuid4()}").status_code == 404
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_concurrent_worker_claims(client):
    run_id = queue(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: claim(UUID(run_id)), range(8)))
    assert sum(token is not None for token in tokens) == 1
    with Session() as db:
        assert db.get(AnalysisRun, UUID(run_id)).attempts == 1


def test_failed_analysis_has_bounded_durable_retries(client, monkeypatch):
    import app.analysis.jobs as jobs

    def unavailable():
        raise RuntimeError("Simulated analysis failure")

    monkeypatch.setattr(jobs, "fake_assessment", unavailable)
    run_id = queue(client)
    for attempt in range(settings().max_attempts):
        if attempt:
            expire(run_id)
        execute(run_id)
    result = client.get(f"/api/v1/analyses/{run_id}").json()
    assert result["status"] == "failed"
    assert result["attempts"] == settings().max_attempts
    assert result["error_code"] == "analysis_error"
    assert result["assessment"] is None
    with Session() as db:
        assert db.get(PendingJob, UUID(run_id)) is None


def test_cancel_before_worker_claim(client):
    run_id = queue(client)
    response = client.post(
        f"/api/v1/analyses/{run_id}/cancel", headers={"Idempotency-Key": "early-cancel"}
    )
    assert response.status_code == 200
    assert claim(UUID(run_id)) is None
    assert client.get(f"/api/v1/analyses/{run_id}").json()["attempts"] == 0


def test_roleless_identity_is_denied(client, monkeypatch):
    monkeypatch.setattr(settings(), "dev_roles", [])
    assert client.get("/api/v1/me").status_code == 403


def test_worker_rejects_expired_queued_run(client):
    run_id = queue(client)
    with Session.begin() as db:
        db.get(AnalysisRun, UUID(run_id)).created_at -= timedelta(
            seconds=settings().queue_timeout_seconds + 1
        )
    assert claim(UUID(run_id)) is None
    result = client.get(f"/api/v1/analyses/{run_id}").json()
    assert result["error_code"] == "job_timeout"
    assert result["status"] == "failed"
