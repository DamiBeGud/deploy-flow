"""Only an explicit first-deployment cleanup may delete journaled owned objects."""

from datetime import timedelta

from sqlalchemy import func, select
from test_phase7_execution import opted_in_job, prepared_run

from app.api.identity import Identity, get_identity
from app.config import settings
from app.deployments import cleanup, execution
from app.dispatcher import dispatch_execution_once
from app.main import app
from app.persistence.database import Session
from app.persistence.models import DeploymentResource, DeploymentRun, ExecutionJob


def partial_first_deployment(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)

    def apply(item, *_):
        if item["kind"] == "Service":
            raise execution.ClusterError("resource_apply_conflict")
        return {"uid": "owned-configmap", "generation": 1}

    monkeypatch.setattr(execution, "apply_owned", apply)
    execution.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "reconciling"
    return run_id, job_id


def test_cleanup_requires_exact_inventory_and_confirms_absence(client, monkeypatch):
    run_id, job_id = partial_first_deployment(client, monkeypatch)
    monkeypatch.setattr(settings(), "deployment_cleanup_enabled", True)
    monkeypatch.setattr(settings(), "execution_max_attempts", 1)
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    document = client.get(f"/api/v1/deployments/{run_id}").json()
    body = {
        "expected_release_digest": document["release_digest"],
        "resources": [
            {"kind": row["kind"], "name": row["name"], "uid": row["uid"]}
            for row in document["resources"]
        ],
    }
    stale = client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "stale-cleanup"},
        json={**body, "expected_release_digest": "0" * 64},
    )
    assert stale.status_code == 409
    queued = client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "first-cleanup"}, json=body,
    )
    assert queued.status_code == 202, queued.text
    assert client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "first-cleanup"}, json=body,
    ).json() == queued.json()
    with Session() as db:
        assert db.get(ExecutionJob, job_id).purpose == "cleanup"
        assert db.get(DeploymentRun, run_id).status == "cleanup_pending"
    deployed, cleaned = [], []
    assert dispatch_execution_once(
        publish=lambda value: deployed.append(value),
        cleanup_publish=lambda value: cleaned.append(value),
    ) == 1
    assert deployed == []
    assert cleaned == [str(job_id)]
    execution.execute(str(job_id))
    with Session() as db:
        assert db.get(ExecutionJob, job_id).status == "queued"
    seen = []

    def delete(kind, name, application, uid):
        seen.append((kind, uid))
        return kind == "Service" or len([row for row in seen if row[0] == kind]) > 1

    monkeypatch.setattr(cleanup, "delete_owned", delete)
    cleanup.execute(str(job_id))
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id)
        assert job.status == "retry_wait"
        assert db.get(DeploymentRun, run_id).status == "cleaning"
        job.next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    assert dispatch_execution_once(cleanup_publish=cleaned.append) == 1
    assert cleaned == [str(job_id), str(job_id)]
    cleanup.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "cleaned"
        assert db.get(ExecutionJob, job_id).status == "completed"
        assert {row.status for row in db.scalars(select(DeploymentResource)).all()} == {"deleted"}
    assert seen == [("Service", None), ("ConfigMap", "owned-configmap"), ("ConfigMap", "owned-configmap")]


def test_cleanup_denies_viewer_and_requires_opt_in(client, monkeypatch):
    run_id, _ = partial_first_deployment(client, monkeypatch)
    document = client.get(f"/api/v1/deployments/{run_id}").json()
    body = {
        "expected_release_digest": document["release_digest"],
        "resources": [
            {"kind": row["kind"], "name": row["name"], "uid": row["uid"]}
            for row in document["resources"]
        ],
    }
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"viewer"}),
    )
    assert client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "viewer-cleanup"}, json=body,
    ).status_code == 403
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    assert client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "disabled-cleanup"}, json=body,
    ).status_code == 409


def test_cleanup_replays_after_expired_worker_lease(client, monkeypatch):
    run_id, job_id = partial_first_deployment(client, monkeypatch)
    monkeypatch.setattr(settings(), "deployment_cleanup_enabled", True)
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    document = client.get(f"/api/v1/deployments/{run_id}").json()
    response = client.post(
        f"/api/v1/deployments/{run_id}/cleanup",
        headers={"Idempotency-Key": "lease-cleanup"},
        json={
            "expected_release_digest": document["release_digest"],
            "resources": [
                {"kind": row["kind"], "name": row["name"], "uid": row["uid"]}
                for row in document["resources"]
            ],
        },
    )
    assert response.status_code == 202
    token, _ = cleanup._claim(job_id)
    with Session.begin() as db:
        db.get(ExecutionJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    cleanup._finish(job_id, token, None)
    with Session() as db:
        assert db.get(ExecutionJob, job_id).status == "running"
    monkeypatch.setattr(cleanup, "delete_owned", lambda *_: True)
    cleanup.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "cleaned"
        assert db.get(ExecutionJob, job_id).attempts == 2
