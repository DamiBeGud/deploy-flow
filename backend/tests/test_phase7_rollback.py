"""Manual rollback copies a successful stored release, never caller YAML."""

import hashlib

from sqlalchemy import select
from test_phase7_execution import prepared_run

from app.api.identity import Identity, get_identity
from app.config import settings
from app.deployments import execution as executor
from app.main import app
from app.persistence.database import Session
from app.persistence.models import (
    DeploymentPolicy,
    DeploymentRun,
    ExecutionJob,
    ReleaseFile,
    ReleaseSpecificationRecord,
)


def two_successful_runs(client, monkeypatch):
    target_id = prepared_run(client, monkeypatch)
    with Session.begin() as db:
        target_run = db.get(DeploymentRun, target_id)
        target_run.status = "succeeded"
        target = db.get(ReleaseSpecificationRecord, target_run.release_id)
        current_release = ReleaseSpecificationRecord(
            application_id=target.application_id, plan_id=target.plan_id,
            verified_build_id=target.verified_build_id, kind="rollback",
            rollback_target_release_id=target.id, rollback_from_release_id=target.id,
            approval_id=target.approval_id,
            environment_profile_id=target.environment_profile_id,
            environment_profile_hash=target.environment_profile_hash,
            source_bundle_digest=target.source_bundle_digest,
            manifest_digest=target.manifest_digest, merged_sha=target.merged_sha,
            image_reference=target.image_reference, validation_json={"status": "passed"},
        )
        db.add(current_release)
        db.flush()
        for row in db.scalars(select(ReleaseFile).where(ReleaseFile.release_id == target.id)):
            db.add(ReleaseFile(
                release_id=current_release.id, path=row.path, content=row.content,
                content_hash=hashlib.sha256(row.content.encode()).hexdigest(),
            ))
        current_run = DeploymentRun(
            application_id=target.application_id, release_id=current_release.id,
            verified_build_id=target.verified_build_id,
            environment_profile_id=target.environment_profile_id,
            status="succeeded", previous_release_id=target.id, event_sequence=0,
        )
        db.add(current_run)
        db.add(DeploymentPolicy(
            application_id=target.application_id,
            environment_profile_id=target.environment_profile_id,
            enabled=True, actor_id="operator",
        ))
        db.flush()
        return target_id, current_run.id


def test_rollback_is_a_new_release_and_executes_without_rechecking_old_branch(client, monkeypatch):
    target_id, current_id = two_successful_runs(client, monkeypatch)
    monkeypatch.setattr(settings(), "auth_mode", "oidc")
    monkeypatch.setattr(settings(), "deployment_execution_enabled", True)
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    response = client.post(
        f"/api/v1/deployments/{current_id}/rollback",
        headers={"Idempotency-Key": "manual-rollback-1"},
    )
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["rollback_target_release_id"] != result["release_id"]
    assert client.post(
        f"/api/v1/deployments/{current_id}/rollback",
        headers={"Idempotency-Key": "manual-rollback-1"},
    ).json() == result
    assert client.post(
        f"/api/v1/deployments/{current_id}/rollback",
        headers={"Idempotency-Key": "second-rollback"},
    ).status_code == 409
    with Session() as db:
        target = db.get(DeploymentRun, target_id)
        run = db.get(DeploymentRun, result["deployment_id"])
        release = db.get(ReleaseSpecificationRecord, run.release_id)
        assert release.kind == "rollback"
        assert release.rollback_target_release_id == target.release_id
        assert run.previous_release_id != target.release_id
        assert db.get(ExecutionJob, result["job_id"]).status == "queued"
    monkeypatch.setattr(executor, "_check_github", lambda *_: (_ for _ in ()).throw(AssertionError("GitHub must not gate historical rollback")))

    async def admitted(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr(executor, "call_kubernetes", admitted)
    monkeypatch.setattr(executor, "apply_owned", lambda item, *_: {
        "uid": f"uid-{item['kind']}", "generation": 2,
    })
    monkeypatch.setattr(executor, "rollout_status", lambda *_: {
        "status": "ready", "desired_replicas": 1, "ready_replicas": 1,
        "available_replicas": 1, "observed_generation": 2,
    })
    monkeypatch.setattr(executor, "service_health", lambda name, path, *_: {
        "status": "passed", "service": name, "path": path,
    })
    executor.execute(result["job_id"])
    with Session() as db:
        assert db.get(DeploymentRun, result["deployment_id"]).status == "succeeded"


def test_rollback_requires_prior_release_and_deployer(client, monkeypatch):
    target_id, current_id = two_successful_runs(client, monkeypatch)
    monkeypatch.setattr(settings(), "auth_mode", "oidc")
    monkeypatch.setattr(settings(), "deployment_execution_enabled", True)
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"viewer"}),
    )
    assert client.post(
        f"/api/v1/deployments/{current_id}/rollback",
        headers={"Idempotency-Key": "viewer"},
    ).status_code == 403
    app.dependency_overrides[get_identity] = lambda: Identity(
        "local-developer", "local-organization", frozenset({"deployer"}),
    )
    first = client.post(
        f"/api/v1/deployments/{target_id}/rollback",
        headers={"Idempotency-Key": "no-prior"},
    )
    assert first.status_code == 409
    assert first.json()["detail"] == "rollback_current_release_required"
