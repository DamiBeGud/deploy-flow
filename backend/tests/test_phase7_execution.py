"""An opted-in release is executed once, with durable recovery boundaries."""

from datetime import timedelta

import pytest
import yaml
from sqlalchemy import func, select
from test_phase7_delivery import queue_preparation

from app.api.identity import Identity, get_identity
from app.config import settings
from app.deployments import execution as executor
from app.deployments.jobs import execute as execute_delivery
from app.deployments.queue import queue_execution
from app.dispatcher import dispatch_execution_once
from app.kubernetes.client import ClusterError
from app.main import app
from app.persistence.database import Session
from app.persistence.models import (
    DeliveryJob,
    DeploymentPolicy,
    DeploymentResource,
    DeploymentRun,
    ExecutionJob,
    ReleaseFile,
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
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
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
    retried = []
    monkeypatch.setattr(executor, "apply_owned", lambda item, _, uid: (
        retried.append(item["kind"]) or {"uid": uid or f"owned-{item['kind']}", "generation": 2}
    ))
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "ready", "desired_replicas": 1,
        "ready_replicas": 1, "available_replicas": 1,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).attempts == 1
    assert retried == ["Service", "Deployment"]


def test_pending_rollout_retries_and_reuses_owned_resources(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    monkeypatch.setattr(settings(), "execution_max_attempts", 1)
    seen = []
    monkeypatch.setattr(executor, "apply_owned", lambda item, _, uid: (
        seen.append((item["kind"], uid)) or {"uid": f"uid-{item['kind']}", "generation": 1}
    ))
    statuses = iter(["pending", "ready"])
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": next(statuses), "desired_replicas": 1,
        "ready_replicas": 1, "available_replicas": 1,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(str(job_id))
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id)
        assert job.status == "retry_wait"
        job.next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    # Once every resource is journaled, an altered PR/approval must not strand
    # read-only rollout observation or trigger another mutation.
    monkeypatch.setattr(executor, "_source", lambda *_: pytest.fail("source was rechecked during observation"))
    monkeypatch.setattr(executor, "_check_github", lambda *_: pytest.fail("GitHub was rechecked during observation"))
    published = []
    assert dispatch_execution_once(published.append) == 1
    assert published == [str(job_id)]
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).attempts == 2
    assert len(seen) == 3


def test_pending_health_resumes_without_reapplying_resources(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    monkeypatch.setattr(settings(), "execution_max_attempts", 1)
    applied = []
    monkeypatch.setattr(executor, "apply_owned", lambda item, _, uid: (
        applied.append(item["kind"]) or {"uid": f"uid-{item['kind']}", "generation": 1}
    ))
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "ready", "desired_replicas": 1,
        "ready_replicas": 1, "available_replicas": 1,
    })
    health = iter(["pending", "passed"])
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
        "status": next(health), "service": name, "path": path,
    })
    executor.execute(str(job_id))
    with Session.begin() as db:
        assert db.get(DeploymentRun, run_id).status == "checking_health"
        job = db.get(ExecutionJob, job_id)
        assert job.status == "retry_wait"
        job.next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    published = []
    assert dispatch_execution_once(published.append) == 1
    assert published == [str(job_id)]
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"
        assert db.get(ExecutionJob, job_id).attempts == 2
    assert applied == ["ConfigMap", "Service", "Deployment"]


@pytest.mark.parametrize("mode", ["not_due", "publish_failed"])
def test_dispatch_does_not_release_retry_before_due_or_after_publish_failure(client, monkeypatch, mode):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id)
        job.status = "retry_wait"
        deadline = db.scalar(select(func.now())) + timedelta(seconds=60 if mode == "not_due" else -1)
        job.next_dispatch_at = deadline

    def fail_publish(_):
        if mode == "not_due":
            pytest.fail("future retry must not be published")
        raise ConnectionError("broker unavailable")

    assert dispatch_execution_once(fail_publish) == 0
    with Session() as db:
        job = db.get(ExecutionJob, job_id)
        assert job.status == "retry_wait"
        assert job.next_dispatch_at == deadline
        assert job.attempts == 0


def test_expired_execution_lease_cannot_finish(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    token, _ = executor._claim(job_id)
    with Session.begin() as db:
        db.get(ExecutionJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    executor._finish(job_id, token, None)
    with Session() as db:
        assert db.get(ExecutionJob, job_id).status == "running"


@pytest.mark.parametrize(("reason", "expected"), [
    ("ImagePullBackOff", "image_pull_failed"),
    ("CrashLoopBackOff", "application_start_failed"),
    ("CreateContainerConfigError", "dependency_configuration_missing"),
    ("ProgressDeadlineExceeded", "rollout_deadline_exceeded"),
])
def test_rollout_failure_has_actionable_code(client, monkeypatch, reason, expected):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    monkeypatch.setattr(executor, "apply_owned", lambda item, *_: {
        "uid": f"uid-{item['kind']}", "generation": 1,
    })
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "failed", "reason": reason,
        "desired_replicas": 1, "ready_replicas": 0, "available_replicas": 0,
    })
    executor.execute(str(job_id))
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        assert run.status == "reconciling"
        assert run.error_code == expected


def test_external_release_requires_internal_and_external_health(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    token, _ = executor._claim(job_id)
    with Session.begin() as db:
        run = db.get(DeploymentRun, run_id)
        files = {row.path: row.content for row in db.scalars(select(ReleaseFile).where(
            ReleaseFile.release_id == run.release_id,
        ))}
        db.add(DeploymentResource(
            deployment_run_id=run_id, kind="Service", name="fixture",
            status="applied", uid="owned-service", generation=1,
        ))
    service = yaml.safe_load(files["deploy/kubernetes/service.yaml"])
    deployment = yaml.safe_load(files["deploy/kubernetes/deployment.yaml"])
    with Session.begin() as db:
        row = db.scalar(select(DeploymentResource).where(DeploymentResource.deployment_run_id == run_id))
        row.name = service["metadata"]["name"]
    ingress = {"spec": {"rules": [{"host": "fixture.deployflow.local", "http": {
        "paths": [{"path": "/"}],
    }}]}}
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
        "status": "passed", "service": name, "path": path,
    })
    observed = iter([
        {"status": "pending", "code": "external_dns_or_connect_failed"},
        {"status": "passed"},
    ])
    monkeypatch.setattr(executor, "external_health", lambda host, path, tls: next(observed))
    assert executor._health(job_id, token, run_id, service, deployment, ingress) is False
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "checking_health"
        assert db.get(DeploymentRun, run_id).health_json["code"] == "external_dns_or_connect_failed"
    assert executor._health(job_id, token, run_id, service, deployment, ingress) is True
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "succeeded"


def test_worker_published_before_dispatch_commit_waits_then_claims_and_observes(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    applied = []
    monkeypatch.setattr(executor, "apply_owned", lambda item, *_: (
        applied.append(item["kind"]) or {"uid": f"uid-{item['kind']}", "generation": 1}
    ))
    statuses = iter([0, 0, 0, 1])
    def observed(*_):
        ready = next(statuses)
        return {"status": "ready" if ready else "pending", "desired_replicas": 1,
                "ready_replicas": ready, "available_replicas": ready}
    monkeypatch.setattr(executor, "rollout_status", observed)
    monkeypatch.setattr(executor, "service_health", lambda *_: {"status": "passed"})
    executor.execute(str(job_id))
    for attempt in range(3):
        with Session.begin() as db:
            db.get(ExecutionJob, job_id).next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = []
            started = Event()
            def worker(identifier):
                started.set()
                executor.execute(identifier)
            def publish(identifier):
                futures.append(pool.submit(worker, identifier))
                assert started.wait(2)
                # Dispatcher still holds the DB row lock here. The worker's
                # actual SELECT FOR UPDATE claim must wait for transaction commit.
                assert not futures[0].done()
            assert dispatch_execution_once(publish) == 1
            futures[0].result(timeout=10)
        with Session() as db:
            run = db.get(DeploymentRun, run_id)
            assert run.rollout_json['ready_replicas'] == (1 if attempt == 2 else 0)
            assert run.status == ('succeeded' if attempt == 2 else 'monitoring')
    assert applied == ['ConfigMap', 'Service', 'Deployment']
    with Session() as db:
        assert db.get(ExecutionJob, job_id).attempts == 4


def test_expired_lease_reclaimed_via_dispatcher_fences_previous_owner(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    old_token, _ = executor._claim(job_id)
    with Session.begin() as db:
        db.get(ExecutionJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    published = []
    assert dispatch_execution_once(published.append) == 1
    new_token, _ = executor._claim(job_id)
    assert new_token != old_token
    executor._finish(job_id, old_token, None)
    with Session() as db:
        assert db.get(ExecutionJob, job_id).lease_token == new_token
    executor._finish(job_id, new_token, executor.ExecutionError('rollout_pending', transient=True))
