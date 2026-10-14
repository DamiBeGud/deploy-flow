"""The verified-CI handoff remains durable and never reports a deployment."""

from datetime import timedelta

from sqlalchemy import func, select
from test_phase7_release import fake_github, release_source

from app.api.identity import Identity, get_identity
from app.config import settings
from app.deployments import jobs as delivery_jobs
from app.deployments.jobs import _claim, _finish, execute
from app.dispatcher import dispatch_delivery_once
from app.main import app
from app.persistence.database import Session
from app.persistence.models import (
    DeliveryJob,
    DeploymentEvent,
    DeploymentRun,
    ReleaseFile,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)


def queue_preparation(client, monkeypatch, *, branch_sha="c" * 40):
    pr_id, build_id = release_source(client, monkeypatch)
    fake_github(monkeypatch, branch_sha=branch_sha)
    with Session.begin() as db:
        job = DeliveryJob(kind="prepare", verified_build_id=build_id, status="queued", attempts=0)
        db.add(job)
        db.flush()
        return pr_id, build_id, job.id


def test_verified_build_prepares_one_release_and_preflight_waits(client, monkeypatch):
    pr_id, _, job_id = queue_preparation(client, monkeypatch)
    calls = []

    async def passed(role, tool, arguments):
        calls.append((role, tool, arguments))
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", passed)
    published = []
    assert dispatch_delivery_once(published.append) == 1
    assert published == [str(job_id)]
    execute(str(job_id))
    execute(str(job_id))
    with Session() as db:
        assert db.get(DeliveryJob, job_id).status == "completed"
        release = db.scalars(select(ReleaseSpecificationRecord)).one()
        run = db.scalars(select(DeploymentRun)).one()
        preflight = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight"))
        assert run.release_id == release.id
        assert run.status == "queued"
        assert len(db.scalars(select(ReleaseFile)).all()) == 3
        preflight_id, run_id = preflight.id, run.id
    execute(str(preflight_id))
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeliveryJob, preflight_id).status == "completed"
        assert db.get(DeploymentRun, run_id).status == "waiting_for_executor"
        assert [event.kind for event in db.scalars(select(DeploymentEvent).order_by(DeploymentEvent.sequence)).all()] == [
            "deployment.queued", "deployment.validating", "deployment.waiting_for_executor",
        ]
        assert len(db.scalars(select(DeploymentRun)).all()) == 1
    assert len(calls) == 2
    assert all(role == "validator" and tool == "kube.validate_manifests" for role, tool, _ in calls)
    pr = client.get(f"/api/v1/pull-requests/{pr_id}")
    assert pr.status_code == 200
    assert pr.json()["release"]["id"] == str(release.id)
    assert pr.json()["deployment"]["status"] == "waiting_for_executor"
    deployment = client.get(f"/api/v1/deployments/{run_id}")
    assert deployment.status_code == 200
    assert deployment.json()["status"] == "waiting_for_executor"
    assert deployment.json()["preflight_validation"]["status"] == "passed"
    events = client.get(f"/api/v1/deployments/{run_id}/events?after=1").json()["events"]
    assert [item["sequence"] for item in events] == [2, 3]


def test_reverted_merge_blocks_preparation_without_release(client, monkeypatch):
    pr_id, _, job_id = queue_preparation(client, monkeypatch, branch_sha="d" * 40)

    async def unexpected(*_):
        raise AssertionError("Reverted merge must not reach Kubernetes")

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", unexpected)
    execute(str(job_id))
    with Session() as db:
        job = db.get(DeliveryJob, job_id)
        assert job.status == "blocked"
        assert job.error_code == "verified_revision_not_target_head"
        assert db.scalars(select(ReleaseSpecificationRecord)).all() == []
    assert client.get(f"/api/v1/pull-requests/{pr_id}").json()["status"] == "verified"
    retry = client.post(
        f"/api/v1/pull-requests/{pr_id}/release-preparation",
        headers={"Idempotency-Key": "retry-reverted"},
    )
    assert retry.status_code == 202
    assert retry.json()["id"] == str(job_id)
    execute(str(job_id))
    with Session() as db:
        assert db.get(DeliveryJob, job_id).status == "blocked"


def test_preflight_retries_unavailable_admission_then_waits(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    execute(str(job_id))
    with Session() as db:
        preflight = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight"))
        run_id, preflight_id = preflight.deployment_run_id, preflight.id

    async def unavailable(*_):
        return {"status": "unavailable", "checks": [{"code": "cluster_access_expired"}]}

    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", unavailable)
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeliveryJob, preflight_id).status == "retry_wait"
        assert db.get(DeploymentRun, run_id).status == "retry_wait"
    with Session.begin() as db:
        db.get(DeliveryJob, preflight_id).next_dispatch_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", passed)
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "waiting_for_executor"
        assert db.get(DeliveryJob, preflight_id).attempts == 2


def test_expired_lease_fences_old_worker(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)
    original = _claim(job_id)
    assert original is not None
    with Session.begin() as db:
        db.get(DeliveryJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    replacement = _claim(job_id)
    assert replacement is not None and replacement[0] != original[0]
    _finish(job_id, original[0], None)
    with Session() as db:
        job = db.get(DeliveryJob, job_id)
        assert job.status == "running"
        assert job.lease_token == replacement[0]
        assert job.attempts == 2


def test_crash_after_release_commit_replays_without_duplicate(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    original_finish = delivery_jobs._finish
    monkeypatch.setattr(delivery_jobs, "_finish", lambda *_: None)
    execute(str(job_id))
    with Session() as db:
        assert db.get(DeliveryJob, job_id).status == "running"
        assert len(db.scalars(select(ReleaseSpecificationRecord)).all()) == 1
        assert len(db.scalars(select(DeploymentRun)).all()) == 1
    with Session.begin() as db:
        db.get(DeliveryJob, job_id).lease_expires_at = db.scalar(select(func.now())) - timedelta(seconds=1)
    monkeypatch.setattr(delivery_jobs, "_finish", original_finish)
    execute(str(job_id))
    with Session() as db:
        assert db.get(DeliveryJob, job_id).status == "completed"
        assert len(db.scalars(select(ReleaseSpecificationRecord)).all()) == 1
        assert len(db.scalars(select(DeploymentRun)).all()) == 1
        assert len(db.scalars(select(DeliveryJob)).all()) == 2


def test_preflight_rejects_changed_stored_release(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    execute(str(job_id))
    with Session.begin() as db:
        file = db.scalar(select(ReleaseFile))
        file.content += "# changed after validation\n"
        preflight = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight"))
        preflight_id, run_id = preflight.id, preflight.deployment_run_id

    async def unexpected(*_):
        raise AssertionError("Changed release must not reach Kubernetes")

    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", unexpected)
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeliveryJob, preflight_id).status == "failed"
        assert db.get(DeploymentRun, run_id).error_code == "release_record_corrupt"


def test_newer_verified_build_supersedes_waiting_run(client, monkeypatch):
    pr_id, build_id, job_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", passed)
    execute(str(job_id))
    with Session() as db:
        first_run = db.scalar(select(DeploymentRun))
        first_id = first_run.id
        preflight_id = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight")).id
    execute(str(preflight_id))
    with Session.begin() as db:
        old = db.get(VerifiedBuild, build_id)
        newer = VerifiedBuild(
            pull_request_id=pr_id, run_id=13, run_attempt=1,
            merged_sha=old.merged_sha, image_repository=old.image_repository,
            image_digest="sha256:" + "e" * 64,
        )
        db.add(newer)
        db.flush()
        job = DeliveryJob(kind="prepare", verified_build_id=newer.id, status="queued", attempts=0)
        db.add(job)
        db.flush()
        newer_job_id = job.id
    execute(str(newer_job_id))
    with Session() as db:
        assert db.get(DeploymentRun, first_id).status == "superseded"
        runs = db.scalars(select(DeploymentRun).order_by(DeploymentRun.created_at)).all()
        assert len(runs) == 2
        assert next(run for run in runs if run.id != first_id).status == "queued"
        assert db.get(DeliveryJob, newer_job_id).status == "completed"


def test_blocked_preflight_can_retry_after_validator_recovers(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)
    monkeypatch.setattr(settings(), "delivery_max_attempts", 1)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    execute(str(job_id))
    with Session() as db:
        preflight = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == "preflight"))
        preflight_id, run_id = preflight.id, preflight.deployment_run_id

    async def unavailable(*_):
        return {"status": "unavailable", "checks": [{"code": "cluster_access_expired"}]}

    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", unavailable)
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "blocked"
    retry = client.post(
        f"/api/v1/deployments/{run_id}/retry-preflight",
        headers={"Idempotency-Key": "retry-after-access-renewal"},
    )
    assert retry.status_code == 202
    assert retry.json()["id"] == str(preflight_id)
    monkeypatch.setattr("app.deployments.jobs.call_kubernetes", passed)
    execute(str(preflight_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == "waiting_for_executor"


def test_other_application_owner_cannot_read_delivery(client, monkeypatch):
    _, _, job_id = queue_preparation(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    execute(str(job_id))
    with Session() as db:
        run = db.scalar(select(DeploymentRun))
        run_id, release_id, application_id = run.id, run.release_id, run.application_id
    app.dependency_overrides[get_identity] = lambda: Identity(
        "another-owner", settings().dev_organization_id, frozenset({"viewer"})
    )
    assert client.get(f"/api/v1/deployments/{run_id}").status_code == 404
    assert client.get(f"/api/v1/releases/{release_id}").status_code == 404
    assert client.get(f"/api/v1/applications/{application_id}/deployments").status_code == 404
    assert client.post(
        f"/api/v1/deployments/{run_id}/retry-preflight",
        headers={"Idempotency-Key": "forbidden"},
    ).status_code == 403
