import copy

import pytest
import yaml
from sqlalchemy import func, select

from app.bootstrap.generator import PlanError, render
from app.bootstrap.recommendations import recommend
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import (
    AnalysisRun,
    AssessmentVersion,
    DeploymentIntentVersion,
    InfrastructurePlan,
    PlanApproval,
)


def application(client):
    response = client.post(
        "/api/v1/applications",
        headers={"Idempotency-Key": "phase4-application"},
        json={
            "name": "fixture",
            "repository_url": "https://github.com/DamiBeGud/deployflow-node-express-fixture",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def completed_v2_run(client, app_id, key="phase4-analysis"):
    queued = client.post(
        f"/api/v1/applications/{app_id}/analyses",
        headers={"Idempotency-Key": key},
        json={},
    )
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["run_id"]
    document = assessment()
    document.update(
        {
            "mode": "deterministic",
            "incomplete_reason": None,
            "application": {"name": "fixture"},
            "questions": [],
        }
    )
    with Session.begin() as db:
        run = db.get(AnalysisRun, run_id)
        run.status = "completed"
        run.assessment_version = 1
        run.scanner_facts_json = {"paths": []}
        db.add(AssessmentVersion(run_id=run.id, version=1, document=document, actor_id="test"))
    return run_id


def save_intent(
    client,
    app_id,
    run_id,
    *,
    version=0,
    assessment_version=1,
    greeting="Hello",
    expected_status=201,
):
    document = intent()
    document["connectivity"]["environment"]["APP_GREETING"] = greeting
    response = client.put(
        f"/api/v1/applications/{app_id}/deployment-intent",
        headers={"Idempotency-Key": f"intent-{version}-{greeting}"},
        json={
            "expected_version": version,
            "analysis_run_id": run_id,
            "expected_assessment_version": assessment_version,
            "environment_profile_id": "local-k3s",
            **document,
        },
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def assessment():
    return {
        "schema_version": "infrastructure-assessment-v2",
        "completion": "complete",
        "repository": {"commit_sha": "a" * 40},
        "review": {"runtime": "nodejs"},
        "application": {"name": "fixture"},
        "questions": [],
    }


def intent():
    return {
        "connectivity": {
            "port": 4587,
            "health_path": "/healthz",
            "replicas": 1,
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
            "environment": {"APP_GREETING": "Hello"},
            "exposure": {"kind": "internal", "path": "/"},
        },
        "build": {
            "branch": "main",
            "image": "ghcr.io/example/fixture",
            "install_command": ["npm", "ci"],
            "test_command": ["npm", "test"],
            "start_command": ["npm", "start"],
        },
    }


def profile():
    return {
        "id": "local-k3s",
        "namespace": "deployflow-test",
        "registry": "ghcr.io",
        "hostname_suffix": ".deployflow.local",
        "ingress_class": "nginx",
        "max_replicas": 3,
        "allowed_secret_names": [],
        "node_base_image": "node:20-bookworm-slim",
        "checkout_action": "actions/checkout@v4",
        "setup_node_action": "actions/setup-node@v4",
    }


def test_node_bundle_is_deterministic_and_internal():
    first = render(assessment(), intent(), profile())
    second = render(assessment(), intent(), profile())
    assert first.digest == second.digest
    assert "deploy/kubernetes/ingress.yaml" not in first.files
    assert "containerPort: 4587" in first.files["deploy/kubernetes/deployment.yaml"]
    assert "ghcr.io/example/fixture:" + "a" * 40 in first.files["deploy/kubernetes/deployment.yaml"]
    workflow = first.files[".github/workflows/deployflow.yml"]
    assert "docker/login-action@v3" in workflow
    assert "docker/build-push-action@v6" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "${{ github.sha }}" in workflow
    jobs = yaml.safe_load(workflow)["jobs"]
    assert jobs["test"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["permissions"] == {"contents": "read", "packages": "write"}
    assert first.validation["schema_version"] == "infrastructure-plan-v1"
    assert first.validation["image_binding"] == {
        "name": "application-image",
        "repository": "ghcr.io/example/fixture",
        "preview_reference": "ghcr.io/example/fixture:" + "a" * 40,
        "status": "provisional",
        "final_reference_kind": "digest",
    }


def test_external_bundle_adds_ingress_and_rejects_unsafe_command():
    external = copy.deepcopy(intent())
    external["connectivity"]["exposure"] = {
        "kind": "external",
        "hostname": "fixture.deployflow.local",
        "path": "/",
    }
    assert "deploy/kubernetes/ingress.yaml" in render(assessment(), external, profile()).files
    missing_hostname = copy.deepcopy(intent())
    missing_hostname["connectivity"]["exposure"] = {"kind": "external", "path": "/"}
    with pytest.raises(PlanError, match="hostname_denied"):
        render(assessment(), missing_hostname, profile())
    unsafe = copy.deepcopy(intent())
    unsafe["build"]["test_command"] = ["npm", "test; curl attacker"]
    with pytest.raises(PlanError, match="unsafe_test_command"):
        render(assessment(), unsafe, profile())


def test_kubernetes_defaults_resolve_generic_analysis_questions():
    document = assessment() | {
        "completion": "partial",
        "questions": [
            {
                "id": "deployment-target",
                "required": True,
                "field_path": "deployment.target_platform",
            }
        ],
    }
    bundle = render(document, intent(), profile())
    assert "deploy/kubernetes/deployment.yaml" in bundle.files


def test_delivery_api_generates_repeatable_immutable_plans(client, monkeypatch):
    monkeypatch.setattr(settings(), "ghcr_registry", "ghcr.io")
    monkeypatch.setattr(settings(), "ghcr_image", "ghcr.io/example/fixture")
    app_id = application(client)
    run_id = completed_v2_run(client, app_id)
    first_intent = save_intent(client, app_id, run_id)
    assert first_intent["schema_version"] == "deployment-intent-v1"
    assert first_intent["build"]["schema_version"] == "build-specification-v1"
    port_provenance = first_intent["field_recommendations"]["connectivity.port"]
    assert port_provenance["recommended_value"] == 3000
    assert port_provenance["effective_value"] == 4587
    assert port_provenance["origin"] == "user_supplied"
    assert port_provenance["recommendation_origin"] == "policy_default"
    assert port_provenance["overridden"] is True

    def generate(intent_version, key):
        return client.post(
            f"/api/v1/applications/{app_id}/infrastructure-plans",
            headers={"Idempotency-Key": key},
            json={
                "analysis_run_id": run_id,
                "expected_assessment_version": 1,
                "intent_version": intent_version,
                "environment_profile_id": "local-k3s",
            },
        )

    first = generate(first_intent["version"], "plan-1")
    assert first.status_code == 201, first.text
    assert first.json()["validation"]["provisional_image"].endswith("a" * 40)
    second_intent = save_intent(
        client, app_id, run_id, version=first_intent["version"], expected_status=200
    )
    assert second_intent["version"] == first_intent["version"]
    second = generate(second_intent["version"], "plan-2")
    assert second.status_code == 201, second.text
    assert second.json()["bundle_digest"] == first.json()["bundle_digest"]
    assert second.json()["schema_version"] == "infrastructure-plan-v1"
    assert second.json()["comparison"] == {
        "previous_plan_id": first.json()["id"],
        "added": [],
        "changed": [],
        "removed": [],
    }
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(InfrastructurePlan)) == 2


def test_latest_plan_is_restored_and_intent_change_invalidates_approval(client, monkeypatch):
    monkeypatch.setattr(settings(), "ghcr_registry", "ghcr.io")
    monkeypatch.setattr(settings(), "ghcr_image", "ghcr.io/example/fixture")
    app_id = application(client)
    run_id = completed_v2_run(client, app_id)
    assert (
        client.get(
            f"/api/v1/applications/{app_id}/infrastructure-plans/latest?analysis_run_id={run_id}"
        ).json()
        is None
    )
    saved = save_intent(client, app_id, run_id)
    generated = client.post(
        f"/api/v1/applications/{app_id}/infrastructure-plans",
        headers={"Idempotency-Key": "plan-latest"},
        json={
            "analysis_run_id": run_id,
            "expected_assessment_version": 1,
            "intent_version": saved["version"],
            "environment_profile_id": "local-k3s",
        },
    )
    assert generated.status_code == 201, generated.text
    plan = generated.json()
    approved = client.post(
        f"/api/v1/infrastructure-plans/{plan['id']}/approve",
        headers={"Idempotency-Key": "approve-latest"},
        json={"expected_digest": plan["bundle_digest"]},
    )
    assert approved.status_code == 201, approved.text
    latest_url = (
        f"/api/v1/applications/{app_id}/infrastructure-plans/latest?analysis_run_id={run_id}"
    )
    assert client.get(latest_url).json()["status"] == "approved"

    unchanged = save_intent(client, app_id, run_id, version=saved["version"], expected_status=200)
    assert unchanged["version"] == saved["version"]
    assert client.get(latest_url).json()["status"] == "approved"
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(DeploymentIntentVersion)) == 1

    save_intent(client, app_id, run_id, version=saved["version"], greeting="Hi")
    latest = client.get(latest_url)
    assert latest.status_code == 200, latest.text
    assert latest.json()["status"] == "stale"
    assert latest.json()["approval"] is None
    with Session() as db:
        approval = db.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan["id"]))
        assert approval.invalidated_at is not None
        assert approval.invalidated_reason == "intent_changed"


def test_kubernetes_recommendation_is_complete_and_evidence_first(client, monkeypatch):
    monkeypatch.setattr(settings(), "ghcr_registry", "ghcr.io")
    monkeypatch.setattr(settings(), "ghcr_image", "ghcr.io/example/fixture")
    app_id = application(client)
    run_id = completed_v2_run(client, app_id)
    response = client.get(
        f"/api/v1/applications/{app_id}/kubernetes-recommendation?analysis_run_id={run_id}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"]["connectivity"]["port"] == 3000
    assert body["intent"]["build"]["image"] == "ghcr.io/example/fixture"
    assert body["recommendations"]["resources"]["source"] == "Kubernetes policy default"


def test_new_analysis_recommendation_continues_application_intent_version(client, monkeypatch):
    monkeypatch.setattr(settings(), "ghcr_registry", "ghcr.io")
    monkeypatch.setattr(settings(), "ghcr_image", "ghcr.io/example/fixture")
    app_id = application(client)
    first_run = completed_v2_run(client, app_id)
    first_intent = save_intent(client, app_id, first_run)
    second_run = completed_v2_run(client, app_id, key="phase4-analysis-next")
    recommendation = client.get(
        f"/api/v1/applications/{app_id}/kubernetes-recommendation?analysis_run_id={second_run}"
    )
    assert recommendation.status_code == 200, recommendation.text
    assert recommendation.json()["intent"]["version"] == first_intent["version"]
    save_intent(client, app_id, second_run, version=first_intent["version"])


def test_configuration_recommendation_deduplicates_port_and_explains_variables():
    document = assessment() | {
        "configuration": {
            "required_variables": [],
            "optional_variables": ["PORT", "HOST", "NODE_ENV", "APP_GREETING", "API_TOKEN"],
        },
        "evidence": [{"id": "env-1", "file_path": "src/app.js", "line_start": 14}],
        "findings": [
            {
                "category": "configuration.variable",
                "value": "APP_GREETING",
                "confidence": 0.95,
                "evidence_ids": ["env-1"],
            }
        ],
    }
    result = recommend(
        document, profile() | {"default_image": "ghcr.io/example/fixture"}, "main", 1
    )
    items = {item["name"]: item for item in result["application_configuration"]}
    assert "PORT" not in items
    assert "API_TOKEN" not in items
    assert items["NODE_ENV"]["recommended_value"] == "production"
    assert items["APP_GREETING"]["evidence"] == {"file_path": "src/app.js", "line_start": 14}
    assert result["intent"]["connectivity"]["environment"] == {
        "HOST": "0.0.0.0",
        "NODE_ENV": "production",
    }
    assert result["intent"]["schema_version"] == "deployment-intent-v1"
    assert result["intent"]["build"]["schema_version"] == "build-specification-v1"
    node_provenance = result["intent"]["field_recommendations"]["connectivity.environment.NODE_ENV"]
    assert node_provenance["recommended_value"] == "production"
    assert node_provenance["effective_value"] == "production"
    assert node_provenance["origin"] == "policy_default"
    assert node_provenance["overridden"] is False


def test_cors_allows_deployment_intent_put(client):
    response = client.options(
        "/api/v1/applications/00000000-0000-0000-0000-000000000000/deployment-intent",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_validation_log_names_fields_without_request_values(client, caplog):
    caplog.set_level("WARNING", logger="app.main")
    response = client.put(
        "/api/v1/applications/00000000-0000-0000-0000-000000000000/deployment-intent",
        headers={"Idempotency-Key": "invalid-intent"},
        json={"id": "do-not-log-this-value"},
    )
    assert response.status_code == 422
    messages = [record.getMessage() for record in caplog.records]
    assert any("body.id" in message for message in messages)
    assert all("do-not-log-this-value" not in message for message in messages)
