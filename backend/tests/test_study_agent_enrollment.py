import io
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from test_native_delivery import PYTHON

from app.agents.harness import COMMAND_PROMPT_VERSION, AgentError, HarnessOutcome
from app.analysis import jobs, service
from app.api.identity import Identity, get_identity
from app.bootstrap.profiles import local_k3s_profile
from app.config import Settings, StudyAgentPin
from app.deployments.release import bind_verified_image
from app.main import app
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, Application, PendingJob

OWNER = Identity("study-operator", "study-organization", frozenset({"developer"}))
OIDC = {
    "auth_mode": "oidc",
    "oidc_issuer": "https://id.example.invalid",
    "oidc_jwks_url": "https://id.example.invalid/keys",
    "oidc_authorization_endpoint": "https://id.example.invalid/authorize",
    "oidc_token_endpoint": "https://id.example.invalid/token",
    "oidc_audience": "deployflow",
    "oidc_client_id": "study",
}


def source(repository="study/example-app", revision="study-test-r1", source_sha="a" * 40):
    pin = StudyAgentPin(
        application_id=uuid4(),
        owner_id=OWNER.actor_id,
        organization_id=OWNER.organization_id,
        repository=repository,
        revision=revision,
        source_sha=source_sha,
    )
    application = Application(
        id=pin.application_id,
        name="scope-test",
        owner_id=pin.owner_id,
        organization_id=pin.organization_id,
        repository_url=f"https://github.com/{repository}",
        revision=revision,
    )
    return application, pin


def enrolled(*pins, **overrides):
    return Settings(
        **(OIDC | {"analysis_mode": "deterministic", "study_agent_pins": pins} | overrides)
    )


def saved_binding(pin):
    return {"study_scope_sha256": pin.fingerprint()}


def test_selects_two_pins_and_leaves_unrelated_applications_deterministic():
    first, first_pin = source()
    second, second_pin = source("study/second-app", "study-test-r2", "b" * 40)
    same_repository, _ = source()
    config = enrolled(first_pin, second_pin)
    assert service.analysis_mode(first, OWNER.actor_id, first.revision, config=config) == "agent"
    assert service.analysis_mode(second, OWNER.actor_id, second.revision, config=config) == "agent"
    assert service.analysis_mode(
        same_repository, OWNER.actor_id, same_repository.revision, config=config
    ) == "deterministic"
    assert config.use_agent_delivery_recommendations


@pytest.mark.parametrize("field,value", [
    ("owner_id", "other-owner"), ("organization_id", "other-org"),
    ("repository_url", "https://github.com/study/other-repository"), ("revision", "different-ref"),
])
def test_rejects_mismatching_application_fields(field, value):
    application, pin = source()
    setattr(application, field, value)
    with pytest.raises(HTTPException) as error:
        service.analysis_mode(application, OWNER.actor_id, pin.revision, config=enrolled(pin))
    assert error.value.status_code == 403


@pytest.mark.parametrize("actor,revision", [("other-user", "study-test-r1"), (OWNER.actor_id, "other-ref")])
def test_rejects_mismatching_actor_or_requested_revision(actor, revision):
    application, pin = source()
    with pytest.raises(HTTPException) as error:
        service.analysis_mode(application, actor, revision, config=enrolled(pin))
    assert error.value.status_code == 403


def test_enrollment_loads_from_environment(monkeypatch):
    _, pin = source()
    monkeypatch.setenv("DF_STUDY_AGENT_PINS", json.dumps([pin.model_dump(mode="json")]))
    config = Settings(analysis_mode="deterministic", **OIDC)
    assert config.study_agent_pin(pin.application_id) == pin


def test_configuration_rejects_ambiguous_enrollment_and_non_oidc_or_global_agent():
    _, first = source()
    _, same_ref = source()
    _, second = source("study/second-app")
    same_application = second.model_copy(update={"application_id": first.application_id})
    for config in (
        {"study_agent_pins": (first, same_ref)},
        {"study_agent_pins": (first, same_application)},
        {"study_agent_pins": (first,), "analysis_mode": "agent"},
        {"study_agent_pins": (first,), "auth_mode": "development"},
    ):
        with pytest.raises(ValidationError):
            enrolled(**config)


def test_worker_rejects_revocation_and_pin_drift_and_checks_provider(monkeypatch):
    application, pin = source()
    run = SimpleNamespace(
        analysis_mode="agent", actor_id=OWNER.actor_id, revision=pin.revision,
        reproducibility_json=saved_binding(pin),
    )
    config = enrolled(pin, deepseek_api_key="test-placeholder")
    monkeypatch.setattr(jobs, "settings", lambda: config)
    assert jobs.validate_scoped_agent(run, application) == pin
    unbound = SimpleNamespace(**(vars(run) | {"reproducibility_json": None}))
    with pytest.raises(AgentError, match="study_agent_source_outside_scope"):
        jobs.validate_scoped_agent(unbound, application)
    for changes, message in (
        ({"study_agent_pins": ()}, "study_agent_source_outside_scope"),
        ({"study_agent_pins": (pin.model_copy(update={"source_sha": "b" * 40}),)}, "study_agent_source_outside_scope"),
        ({"analysis_mode": "agent", "study_agent_pins": ()}, "study_agent_source_outside_scope"),
        ({"deepseek_api_key": None}, "study_agent_provider_unconfigured"),
        ({"deepseek_base_url": "https://unknown.example.invalid"}, "study_agent_provider_unconfigured"),
    ):
        changed = config.model_copy(update=changes)
        monkeypatch.setattr(jobs, "settings", lambda: changed)
        with pytest.raises(AgentError, match=message):
            jobs.validate_scoped_agent(run, application)


@pytest.mark.parametrize("changed", ["commit", "repository"])
def test_worker_rejects_resolution_mismatch_before_download(monkeypatch, changed):
    application, pin = source()
    run = AnalysisRun(
        id=uuid4(), application_id=application.id, actor_id=application.owner_id,
        revision=pin.revision, analysis_mode="agent", reproducibility_json=saved_binding(pin),
    )
    config = enrolled(pin, deepseek_api_key="test-placeholder")
    monkeypatch.setattr(jobs, "settings", lambda: config)

    class Source:
        def __init__(self, settings):
            assert settings is config

        def resolve(self, owner, name, revision):
            assert revision == pin.revision
            return SimpleNamespace(
                owner=owner, name="replaced" if changed == "repository" else name,
                commit_sha="b" * 40 if changed == "commit" else pin.source_sha,
            )

        def download(self, _):
            pytest.fail("A mismatched source must be rejected before its archive is read")

    class ReadSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get(self, model, _identity):
            return run if model is AnalysisRun else application

    monkeypatch.setattr(jobs, "Session", ReadSession)
    monkeypatch.setattr(jobs, "GitHubClient", Source)
    with pytest.raises(AgentError, match="study_agent_source_outside_scope"):
        jobs.execute_repository(run.id, uuid4())


def test_study_api_scopes_concurrent_starts_and_preserves_worker_retry(monkeypatch):
    application, pin = source()
    with Session.begin() as db:
        db.add(application)
    config = enrolled(pin)
    monkeypatch.setattr(service, "settings", lambda: config)
    monkeypatch.setattr(jobs, "settings", lambda: config)
    monkeypatch.setitem(app.dependency_overrides, get_identity, lambda: OWNER)
    endpoint = f"/api/v1/applications/{pin.application_id}/analyses"
    barrier = Barrier(2)

    def start(key):
        barrier.wait(timeout=5)
        return key, client.post(endpoint, headers={"Idempotency-Key": key}, json={})

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as threads:
        outcomes = list(threads.map(start, ("study-one", "study-two")))
        assert sorted(response.status_code for _, response in outcomes) == [202, 409]
        key, queued = next((key, response) for key, response in outcomes if response.status_code == 202)
        replay = client.post(endpoint, headers={"Idempotency-Key": key}, json={})
        assert replay.status_code == 202 and replay.json()["run_id"] == queued.json()["run_id"]
        run_id = UUID(queued.json()["run_id"])
        reanalysis = client.post(
            f"/api/v1/analyses/{run_id}/reanalyze", headers={"Idempotency-Key": "study-reanalysis"}, json={}
        )
        assert reanalysis.status_code == 409
        ordinary = client.post(
            endpoint, headers={"Idempotency-Key": "wrong-revision"}, json={"revision": "unapproved"}
        )
        assert ordinary.status_code == 403

    token = jobs.claim(run_id)
    assert token is not None
    with Session.begin() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.reproducibility_json["study_scope_sha256"] == pin.fingerprint()
        assert run.reproducibility_json["attempt_history"][0]["status"] == "started"
        assert run.lease_token == token and run.analysis_mode == "agent" and run.attempts == 1
        jobs.retry_or_fail(db, run, "transient")
        db.get(PendingJob, run_id).next_dispatch_at = db.scalar(select(func.now()))
    assert jobs.claim(run_id) is not None
    with Session() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.attempts == 2
        assert run.reproducibility_json["study_scope_sha256"] == pin.fingerprint()
        first, second = run.reproducibility_json["attempt_history"]
        assert (first["attempt"], first["status"], first["error_code"]) == (1, "retrying", "transient")
        assert first["provenance"] is None and second["status"] == "started" and second["attempt"] == 2
        assert db.scalar(select(func.count()).select_from(AnalysisRun).where(
            AnalysisRun.application_id == pin.application_id
        )) == 1


def test_study_worker_and_shared_redis_binding_roundtrip_through_api(monkeypatch):
    application, pin = source()
    with Session.begin() as db:
        db.add(application)
    config = enrolled(
        pin, deepseek_api_key="test-placeholder",
        skill_directory=Path(__file__).resolve().parents[1] / "skills",
    )
    monkeypatch.setattr(service, "settings", lambda: config)
    monkeypatch.setattr(jobs, "settings", lambda: config)
    with Session.begin() as db:
        payload, _ = service.enqueue(db, db.get(Application, pin.application_id), OWNER, "test-scope", None)
        run_id = UUID(payload["run_id"])

    sources = PYTHON | {
        "requirements.txt": PYTHON["requirements.txt"] + "redis==5.2.1\ncelery==5.5.3\n",
        "README.md": PYTHON["README.md"]
        + "Run `celery -A service.worker.celery worker --loglevel=warning --concurrency=1`.\n",
        "service/main.py": PYTHON["service/main.py"] + '@app.get("/health/live")\ndef live(): return {}\n',
        "service/worker.py": (
            "import os\nfrom celery import Celery\n"
            'celery = Celery("scope", broker=os.environ["CELERY_BROKER_URL"],'
            ' backend=os.environ["CELERY_RESULT_BACKEND"])\n'
            'queue = os.environ["QUEUE_NAMESPACE"]\n'
        ),
    }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for path, text in sources.items():
            content = text.encode()
            entry = tarfile.TarInfo("pinned-snapshot/" + path)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))

    class ApprovedSource:
        def __init__(self, settings):
            assert settings is config

        def resolve(self, owner, name, revision):
            assert f"{owner}/{name}" == pin.repository and revision == pin.revision
            return SimpleNamespace(
                owner=owner, name=name, url=f"https://github.com/{pin.repository}",
                revision=revision, commit_sha=pin.source_sha,
            )

        def download(self, resolved):
            assert resolved.commit_sha == pin.source_sha
            return iter([raw.getvalue()])

    first_receipt = {"is_simulated": True, "fallback": False, "provider_calls": [
        {"sequence": 1, "status": "error", "error_code": "model_timeout"},
    ]}
    second_receipt = {"is_simulated": True, "fallback": False, "provider_calls": [
        {"sequence": 1, "status": "completed"},
    ]}
    receipts = iter([first_receipt, second_receipt])

    async def simulated_agent(root, metadata, facts, skills, budget):
        receipt = next(receipts)
        if receipt is first_receipt:
            error = AgentError("model_timeout", retryable=True)
            error.provenance = receipt
            raise error
        assert metadata["commit_sha"] == pin.source_sha
        assert (root / "service/worker.py").read_text() == sources["service/worker.py"] and skills
        return HarnessOutcome(
            {**facts["document"], "mode": "agent"}, [], [], budget, 0,
            None, "completed", prompt_version=COMMAND_PROMPT_VERSION, provider_kind="scripted", provenance=receipt,
        )

    monkeypatch.setattr(jobs, "GitHubClient", ApprovedSource)
    monkeypatch.setattr(jobs, "_run_agent", simulated_agent)
    jobs.execute(str(run_id))
    with Session.begin() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.status == "queued" and run.attempts == 1
        db.get(PendingJob, run_id).next_dispatch_at = db.scalar(select(func.now()))
    jobs.execute(str(run_id))
    with Session() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.status == "completed" and run.commit_sha == pin.source_sha
        assert run.reproducibility_json["study_scope_sha256"] == pin.fingerprint()
        assert run.reproducibility_json["provider"] == "scripted"
        assert run.reproducibility_json["agent_provenance"] == second_receipt
        first, second = run.reproducibility_json["attempt_history"]
        assert (first["attempt"], first["status"], first["provenance"]) == (1, "retrying", first_receipt)
        assert (second["attempt"], second["status"], second["provenance"]) == (2, "completed", second_receipt)
        assert not run.reproducibility_json["provider_response_ids"]

    monkeypatch.setenv("DF_APPROVED_REDIS_BINDINGS", json.dumps([{
        "application_id": str(pin.application_id), "application_name": application.name,
        "repository": pin.repository, "source_sha": pin.source_sha, "worker_target": "service.worker:celery",
        "redis_connection_secret": "test-study-redis",
    }]))
    monkeypatch.setitem(app.dependency_overrides, get_identity, lambda: OWNER)
    endpoint = f"/api/v1/applications/{pin.application_id}"
    with TestClient(app) as client:
        observed = client.get(f"/api/v1/analyses/{run_id}")
        assert observed.status_code == 200
        provenance = observed.json()["reproducibility"]
        assert provenance["study_scope_sha256"] == pin.fingerprint()
        assert [entry["provenance"] for entry in provenance["attempt_history"]] == [first_receipt, second_receipt]
        suggestion = client.get(f"{endpoint}/kubernetes-recommendation?analysis_run_id={run_id}")
        assert suggestion.status_code == 200, suggestion.text
        rec = suggestion.json()
        assert rec["generation_blocker"] is None
        assert rec["approved_queue"]["worker"].startswith("celery -A service.worker:celery worker")
        intent = rec["intent"]
        saved = client.put(
            f"{endpoint}/deployment-intent",
            headers={"Idempotency-Key": "study-redis-intent"},
            json={
                **{key: intent[key] for key in ("schema_version", "environment_profile_id", "connectivity", "build")},
                "analysis_run_id": str(run_id), "expected_version": 0, "expected_assessment_version": 1,
            },
        )
        assert saved.status_code == 201, saved.text
        response = client.post(
            f"{endpoint}/infrastructure-plans",
            headers={"Idempotency-Key": "study-redis-bundle"},
            json={
                "analysis_run_id": str(run_id), "expected_assessment_version": 1,
                "intent_version": saved.json()["version"], "environment_profile_id": "local-k3s",
            },
        )
    assert response.status_code == 201, response.text
    plan = response.json()
    files = {record["path"]: record["content"] for record in plan["files"]}
    release = bind_verified_image(
        bundle_files=files, bundle_digest=plan["bundle_digest"], image_binding=plan["validation"]["image_binding"],
        image_repository=intent["build"]["image"], image_digest="sha256:" + "b" * 64,
        profile=local_k3s_profile(), application_id=str(pin.application_id),
    )
    deployment = yaml.safe_load(release.files["deploy/kubernetes/deployment.yaml"])
    pod = deployment["spec"]["template"]["spec"]
    assert not pod.get("initContainers")
    web, worker = pod["containers"]
    assert web["image"] == worker["image"] == release.image_reference
    assert web["env"] == worker["env"]
    assert {item["name"] for item in worker["env"]} == {"CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"}
    assert {item["valueFrom"]["secretKeyRef"]["name"] for item in worker["env"]} == {"test-study-redis"}


def test_study_worker_does_not_simulate_queued_agent_job_if_mode_changes(monkeypatch):
    application, pin = source()
    with Session.begin() as db:
        db.add(application)
    monkeypatch.setattr(service, "settings", lambda: enrolled(pin))
    with Session.begin() as db:
        payload, _ = service.enqueue(db, db.get(Application, pin.application_id), OWNER, "test-scope", None)
        run_id = UUID(payload["run_id"])
    monkeypatch.setattr(jobs, "settings", lambda: Settings(analysis_mode="fake"))
    jobs.execute(str(run_id))
    with Session() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.status == "failed" and run.error_code == "study_agent_source_outside_scope"
        assert run.assessment_version == 0 and run.commit_sha is None
