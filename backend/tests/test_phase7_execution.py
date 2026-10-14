"""An opted-in release is executed once, with durable recovery boundaries."""

from datetime import timedelta

from sqlalchemy import func, select
from test_phase7_delivery import queue_preparation

from app.api.identity import Identity, get_identity
from app.config import settings
from app.deployments import execution as executor
from app.deployments.jobs import execute as execute_delivery
from app.deployments.queue import queue_execution
from app.kubernetes.client import ClusterError
from app.main import app
from app.persistence.database import Session
from app.persistence.models import (
    DeliveryJob,
    DeploymentPolicy,
    DeploymentResource,
    DeploymentRun,
    ExecutionJob,
)


def prepared_run(client, monkeypatch):
    _, _, preparation_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", passed)
    monkeypatch.setattr(executor, "call_kubernetes", passed)
    execute_delivery(str(preparation_id))
    with Session() as db:
        preflight_id = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight")).id
    execute_delivery(str(preflight_id))
    with Session() as db:
        return db.scalar(select(DeploymentRun)).id


def opted_in_job(run_id, monkeypatch):
    monkeypatch.setattr(settings(), "auth_mode", "oidc")
    monkeypatch.setattr(settings(), "deployment_execution_enabled", True)
    monkeypatch.setattr(executor, "_check_github", lambda source: None)
    with Session.begin() as db:
        run = db.get(DeploymentRun, run_id, with_for_update=True)
        db.add(DeploymentPolicy(
            application_id=run.application_id, environment_profile_id=run.environment_profile_id,
            enabled=True, actor_id="operator",
        ))
        db.flush()
        job = queue_execution(db, run)
        db.flush()
        return job.id


def test_execution_records_resources_rollout_and_health(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    applied = []

    def apply(item, application_id, uid):
        applied.append((item["kind"], uid))
        return {"uid": f"uid-{item['kind']}", "generation": 2}

    monkeypatch.setattr(executor, "apply_owned", apply)
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "ready", "desired_replicas": 1, "ready_replicas": 1,
        "available_replicas": 1, "observed_generation": 2,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(str(job_id))
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).status == "completed"
        assert [row.status for row in db.scalars(select(DeploymentResource)).all()] == ["applied"] * 3
    assert [kind for kind, _ in applied] == ["ConfigMap", "Service", "Deployment"]


def test_partial_apply_fails_closed_and_keeps_journal(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)

    def apply(item, *_):
        if item["kind"] == "Service":
            raise ClusterError("resource_apply_conflict")
        return {"uid": "owned-configmap", "generation": 1}

    monkeypatch.setattr(executor, "apply_owned", apply)
    executor.execute(str(job_id))
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        assert run.status == "reconciling"
        assert run.error_code == "resource_apply_conflict"
        assert db.get(ExecutionJob, job_id).status == "blocked"
        rows = {row.kind: row for row in db.scalars(select(DeploymentResource)).all()}
        assert rows["ConfigMap"].uid == "owned-configmap"
        assert rows["Service"].status == "sending"

    app.dependency_overrides[get_identity] = lambda: Identity(
        "viewer", "local-organization", frozenset({"viewer"}),
    )
    denied = client.post(
        f"/api/v1/deployments/{run_id}/retry-execution",
        headers={"Idempotency-Key": "reconcile"},
    )
    assert denied.status_code == 403
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    retry = client.post(
        f"/api/v1/deployments/{run_id}/retry-execution",
        headers={"Idempotency-Key": "reconcile"},
    )
    assert retry.status_code == 202
    assert retry.json()["job_id"] == str(job_id)
    monkeypatch.setattr(executor, "apply_owned", lambda item, _, uid: {
        "uid": uid or f"owned-{item['kind']}", "generation": 2,
    })
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "ready", "desired_replicas": 1,
        "ready_replicas": 1, "available_replicas": 1,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).attempts == 1


def test_pending_rollout_retries_and_reuses_owned_resources(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    seen = []
    monkeypatch.setattr(executor, "apply_owned", lambda item, _, uid: (
        seen.append((item["kind"], uid)) or {"uid": f"uid-{item['kind']}", "generation": 1}
    ))
    statuses = iter(["pending", "ready"])
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": next(statuses), "desired_replicas": 1,
        "ready_replicas": 1, "available_replicas": 1,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(str(job_id))
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id)
        assert job.status == "retry_wait"
        job.next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).attempts == 2
    assert len(seen) == 6
    assert seen[3:] == [("ConfigMap", "uid-ConfigMap"), ("Service", "uid-Service"), ("Deployment", "uid-Deployment")]


def test_expired_execution_lease_cannot_finish(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    token, _ = executor._claim(job_id)
    with Session.begin() as db:
        db.get(ExecutionJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    executor._finish(job_id, token, None)
    with Session() as db:
        assert db.get(ExecutionJob, job_id).status == "running"
