import hashlib
import hmac
import io
import json
import zipfile
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_phase4 import application, completed_v2_run, save_intent

from app.config import settings
from app.integrations.github.api import GitHubAPI, GitHubPhase6Error
from app.integrations.github.jobs import execute as execute_github
from app.integrations.github.verification import (
    checked_artifact,
    checked_merged_files,
    checked_workflow_run,
)
from app.persistence.database import Session
from app.persistence.models import (
    AnalysisRun,
    DeliveryJob,
    GitHubJob,
    GitHubWebhookDelivery,
    InfrastructurePlanFile,
    InfrastructurePullRequest,
    VerifiedBuild,
)
from app.webhook_main import app as webhook_app


def prepared(client, monkeypatch):
    config = settings()
    monkeypatch.setattr(config, "ghcr_registry", "ghcr.io")
    monkeypatch.setattr(config, "ghcr_image", "ghcr.io/example/fixture")
    monkeypatch.setattr(config, "github_app_id", "123")
    monkeypatch.setattr(config, "github_installation_id", 456)
    monkeypatch.setattr(config, "github_private_key_path", "/outside-repo/app.pem")
    app_id = application(client)
    run_id = completed_v2_run(client, app_id)
    with Session.begin() as db:
        db.get(AnalysisRun, run_id).commit_sha = "a" * 40
    saved = save_intent(client, app_id, run_id)
    response = client.post(
        f"/api/v1/applications/{app_id}/infrastructure-plans",
        headers={"Idempotency-Key": "phase6-plan"},
        json={"analysis_run_id": run_id, "expected_assessment_version": 1, "intent_version": saved["version"], "environment_profile_id": "local-k3s"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_pr_requires_admission_and_queues_only_one_write(client, monkeypatch):
    plan = prepared(client, monkeypatch)
    path = f"/api/v1/infrastructure-plans/{plan['id']}/pull-requests"
    body = {"expected_digest": plan["bundle_digest"]}
    rejected = client.post(path, headers={"Idempotency-Key": "before-admission"}, json=body)
    assert rejected.status_code == 409

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.api.routes.call_kubernetes", passed)
    validation = client.post(
        f"/api/v1/infrastructure-plans/{plan['id']}/validations",
        headers={"Idempotency-Key": "phase6-admission"}, json=body,
    )
    assert validation.status_code == 201
    first = client.post(path, headers={"Idempotency-Key": "create-pr"}, json=body)
    second = client.post(path, headers={"Idempotency-Key": "create-pr"}, json=body)
    third = client.post(path, headers={"Idempotency-Key": "create-pr-again"}, json=body)
    assert first.status_code == second.status_code == third.status_code == 202
    assert first.json()["id"] == second.json()["id"] == third.json()["id"]
    assert first.json()["status"] == "queued"
    assert client.get(f"/api/v1/infrastructure-plans/{plan['id']}").json()["status"] == "approved"
    with Session() as db:
        assert len(db.scalars(select(InfrastructurePullRequest)).all()) == 1
        assert len(db.scalars(select(GitHubJob)).all()) == 1


def test_webhook_requires_raw_body_signature_and_deduplicates(client, monkeypatch):
    assert client.post("/webhooks/github").status_code == 404
    monkeypatch.setattr(settings(), "github_webhook_secret", "a-test-secret")
    monkeypatch.setattr(settings(), "github_installation_id", 456)
    body = json.dumps({"action": "completed", "repository": {"id": 123}, "installation": {"id": 456}}).encode()
    signature = "sha256=" + hmac.new(b"a-test-secret", body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Delivery": "phase6-delivery", "X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": signature}
    with TestClient(webhook_app) as client:
        assert client.post("/webhooks/github", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=bad"}).status_code == 401
        first = client.post("/webhooks/github", content=body, headers=headers)
        second = client.post("/webhooks/github", content=body, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    with Session() as db:
        assert len(db.scalars(select(GitHubWebhookDelivery)).all()) == 1
        assert {job.kind for job in db.scalars(select(GitHubJob))} == {"reconcile", "source_reconcile"}


def test_github_worker_records_one_pr_after_external_success(client, monkeypatch):
    plan = prepared(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.api.routes.call_kubernetes", passed)
    body = {"expected_digest": plan["bundle_digest"]}
    client.post(f"/api/v1/infrastructure-plans/{plan['id']}/validations", headers={"Idempotency-Key": "worker-admission"}, json=body)
    created = client.post(f"/api/v1/infrastructure-plans/{plan['id']}/pull-requests", headers={"Idempotency-Key": "worker-pr"}, json=body)
    assert created.status_code == 202
    calls = []

    class FakeGitHub:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def create_bundle_pull_request(self, **arguments):
            calls.append(arguments)
            return {"repository_id": 123, "head_sha": "b" * 40, "number": 8, "url": "https://github.com/DamiBeGud/deployflow-node-express-fixture/pull/8"}

    monkeypatch.setattr("app.integrations.github.jobs.GitHubAPI", FakeGitHub)
    with Session() as db:
        job = db.scalar(select(GitHubJob).where(GitHubJob.kind == "create"))
        job_id = str(job.id)
    execute_github(job_id)
    execute_github(job_id)
    assert len(calls) == 1
    pr = client.get(f"/api/v1/pull-requests/{created.json()['id']}")
    assert pr.json()["status"] == "open"
    assert pr.json()["number"] == 8
    with Session() as db:
        assert len(db.scalars(select(GitHubJob).where(GitHubJob.kind == "reconcile")).all()) == 1


@pytest.mark.parametrize("missing_pr_merge_sha", [False, True])
def test_merged_pr_and_exact_ci_artifact_record_verified_image_without_deployment(client, monkeypatch, missing_pr_merge_sha):
    plan = prepared(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.api.routes.call_kubernetes", passed)
    body = {"expected_digest": plan["bundle_digest"]}
    client.post(f"/api/v1/infrastructure-plans/{plan['id']}/validations", headers={"Idempotency-Key": "verified-admission"}, json=body)
    created = client.post(f"/api/v1/infrastructure-plans/{plan['id']}/pull-requests", headers={"Idempotency-Key": "verified-pr"}, json=body)
    assert created.status_code == 202
    with Session() as db:
        files = {row.path: row.content for row in db.scalars(select(InfrastructurePlanFile)).all()}
        create_job_id = str(db.scalar(select(GitHubJob).where(GitHubJob.kind == "create")).id)

    class FakeGitHub:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def create_bundle_pull_request(self, **_):
            return {"repository_id": 123, "head_sha": "b" * 40, "number": 8, "url": "https://github.com/DamiBeGud/deployflow-node-express-fixture/pull/8"}

        def pull_request(self, *_):
            return {"base": {"ref": "main", "repo": {"id": 123}}, "head": {"ref": f"deployflow/plan-{plan['id']}", "sha": "b" * 40}, "merged": True, "merge_commit_sha": None if missing_pr_merge_sha else "c" * 40}

        def merged_event_sha(self, *_):
            assert missing_pr_merge_sha
            return "c" * 40

        def repository_info(self):
            return {"id": 123}

        def compare_paths(self, *_):
            return set(files)

        def file_at(self, path, *_):
            return files[path].encode()

        def workflow_runs(self, *_):
            return [{"id": 77, "head_sha": "c" * 40, "run_attempt": 1}]

        def workflow_run(self, *_):
            return {"id": 77, "status": "completed", "conclusion": "success", "event": "push", "head_sha": "c" * 40, "head_branch": "main", "path": ".github/workflows/deployflow.yml@main", "repository": {"id": 123}, "run_attempt": 1}

        def run_artifacts(self, *_):
            return [{"id": 55, "name": "deployflow-build", "size_in_bytes": 512, "expired": False}]

        def artifact_zip(self, *_):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("deployflow-build.json", json.dumps({
                    "repository_id": 123, "commit_sha": "c" * 40, "run_id": 77,
                    "run_attempt": 1, "image_repository": "ghcr.io/example/fixture",
                    "image_digest": "sha256:" + "d" * 64,
                }))
            return archive.getvalue()

    monkeypatch.setattr("app.integrations.github.jobs.GitHubAPI", FakeGitHub)
    monkeypatch.setattr("app.integrations.github.jobs.verify_registry_digest", lambda *_: None)
    execute_github(create_job_id)
    with Session() as db:
        reconcile_job_id = str(db.scalar(select(GitHubJob).where(GitHubJob.kind == "reconcile")).id)
    execute_github(reconcile_job_id)
    result = client.get(f"/api/v1/pull-requests/{created.json()['id']}").json()
    assert result["status"] == "verified"
    assert result["verified_build"]["image_digest"] == "sha256:" + "d" * 64
    with Session() as db:
        assert len(db.scalars(select(VerifiedBuild)).all()) == 1
        jobs = db.scalars(select(DeliveryJob)).all()
        assert len(jobs) == 1
        assert jobs[0].kind == "prepare"
        assert jobs[0].verified_build_id == db.scalar(select(VerifiedBuild)).id
    with Session.begin() as db:
        repeated = GitHubJob(
            kind="reconcile", pull_request_id=UUID(created.json()["id"]),
            status="queued", attempts=0,
        )
        db.add(repeated)
        db.flush()
        repeated_id = str(repeated.id)
    execute_github(repeated_id)
    with Session() as db:
        assert len(db.scalars(select(VerifiedBuild)).all()) == 1
        assert len(db.scalars(select(DeliveryJob)).all()) == 1
        assert db.get(InfrastructurePullRequest, UUID(created.json()["id"])).status == "verified"


def test_missing_merge_event_does_not_falsely_block_merged_pr(client, monkeypatch):
    plan = prepared(client, monkeypatch)

    async def passed(*_):
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.api.routes.call_kubernetes", passed)
    body = {"expected_digest": plan["bundle_digest"]}
    client.post(f"/api/v1/infrastructure-plans/{plan['id']}/validations", headers={"Idempotency-Key": "missing-sha-admission"}, json=body)
    created = client.post(f"/api/v1/infrastructure-plans/{plan['id']}/pull-requests", headers={"Idempotency-Key": "missing-sha-pr"}, json=body)
    assert created.status_code == 202

    class FakeGitHub:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def create_bundle_pull_request(self, **_):
            return {"repository_id": 123, "head_sha": "b" * 40, "number": 8, "url": "https://github.com/example/fixture/pull/8"}

        def pull_request(self, *_):
            return {"base": {"ref": "main", "repo": {"id": 123}}, "head": {"ref": f"deployflow/plan-{plan['id']}", "sha": "b" * 40}, "merged": True, "merge_commit_sha": None}

        def repository_info(self):
            return {"id": 123}

        def merged_event_sha(self, *_):
            raise GitHubPhase6Error("merge_commit_sha_unavailable", transient=True)

    monkeypatch.setattr("app.integrations.github.jobs.GitHubAPI", FakeGitHub)
    with Session() as db:
        create_job_id = str(db.scalar(select(GitHubJob).where(GitHubJob.kind == "create")).id)
    execute_github(create_job_id)
    with Session() as db:
        reconcile_job_id = str(db.scalar(select(GitHubJob).where(GitHubJob.kind == "reconcile")).id)
    for _ in range(5):
        execute_github(reconcile_job_id)
    result = client.get(f"/api/v1/pull-requests/{created.json()['id']}").json()
    assert result["status"] == "merged"
    assert result["error_code"] == "merge_commit_sha_unavailable"
    assert result["verified_build"] is None


def test_build_artifact_is_bound_to_exact_run_and_image():
    payload = {
        "repository_id": 123, "commit_sha": "a" * 40, "run_id": 77, "run_attempt": 2,
        "image_repository": "ghcr.io/example/fixture", "image_digest": "sha256:" + "b" * 64,
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("deployflow-build.json", json.dumps(payload))
    expected = dict(repository_id=123, commit_sha="a" * 40, run_id=77, run_attempt=2, image_repository="ghcr.io/example/fixture")
    assert checked_artifact(archive.getvalue(), **expected) == payload["image_digest"]
    with pytest.raises(GitHubPhase6Error, match="build_artifact_mismatch"):
        checked_artifact(archive.getvalue(), **{**expected, "run_attempt": 1})


def test_only_expected_successful_push_workflow_is_eligible():
    run = {
        "id": 77, "status": "completed", "conclusion": "success", "event": "push",
        "head_sha": "a" * 40, "head_branch": "main", "path": ".github/workflows/deployflow.yml",
        "repository": {"id": 123}, "run_attempt": 1,
    }
    expected = dict(repository_id=123, merged_sha="a" * 40, base_branch="main")
    assert checked_workflow_run(run, **expected)
    assert checked_workflow_run({**run, "path": ".github/workflows/deployflow.yml@main"}, **expected)
    assert not checked_workflow_run({**run, "event": "pull_request"}, **expected)
    assert not checked_workflow_run({**run, "head_sha": "b" * 40}, **expected)
    assert not checked_workflow_run({**run, "conclusion": "failure"}, **expected)


def test_merged_files_must_match_approval_and_contain_no_source_changes():
    files = {"Dockerfile": "FROM scratch\n", ".dockerignore": "node_modules\n"}

    class FakeAPI:
        def compare_paths(self, *_):
            return set(files)

        def file_at(self, path, *_):
            return files[path].encode()

    checked_merged_files(FakeAPI(), base_sha="a" * 40, merged_sha="b" * 40, files=files)
    with pytest.raises(GitHubPhase6Error, match="merge_commit_sha_unavailable") as missing:
        checked_merged_files(FakeAPI(), base_sha="a" * 40, merged_sha=None, files=files)
    assert missing.value.transient

    class ChangedSource(FakeAPI):
        def compare_paths(self, *_):
            return set(files) | {"src/server.js"}

    with pytest.raises(GitHubPhase6Error, match="merged_files_changed"):
        checked_merged_files(ChangedSource(), base_sha="a" * 40, merged_sha="b" * 40, files=files)


def test_merge_event_recovers_sha_from_later_page(monkeypatch):
    api = GitHubAPI(settings(), "example", "fixture")
    calls = []

    def request(_, path, *, params):
        calls.append((path, params))
        events = [{"event": "commented"}] * 100 if params["page"] == 1 else [{"event": "merged", "commit_id": "c" * 40}]
        return SimpleNamespace(json=lambda: events)

    monkeypatch.setattr(api, "request", request)
    assert api.merged_event_sha(8) == "c" * 40
    assert [params["page"] for _, params in calls] == [1, 2]
    assert all(path == "/repos/example/fixture/issues/8/events" for path, _ in calls)


@pytest.mark.parametrize(
    ("events", "code", "transient"),
    [
        ([], "merge_commit_sha_unavailable", True),
        ([{"event": "merged", "commit_id": None}], "merge_commit_sha_unavailable", True),
        ([{"event": "merged", "commit_id": "bad"}], "github_merge_event_invalid", False),
        ([{"event": "merged", "commit_id": "a" * 40}, {"event": "merged", "commit_id": "b" * 40}], "github_merge_event_ambiguous", False),
    ],
)
def test_merge_event_rejects_missing_or_untrusted_sha(monkeypatch, events, code, transient):
    api = GitHubAPI(settings(), "example", "fixture")
    monkeypatch.setattr(api, "request", lambda *_, **__: SimpleNamespace(json=lambda: events))
    with pytest.raises(GitHubPhase6Error, match=code) as error:
        api.merged_event_sha(8)
    assert error.value.transient is transient


def test_github_pr_retry_reuses_existing_reviewed_branch(monkeypatch):
    api = GitHubAPI(settings(), "example", "fixture")
    monkeypatch.setattr(api, "list_pull_requests", lambda *_: [{
        "base": {"ref": "main"}, "head": {"ref": "deployflow/plan-1", "sha": "b" * 40},
        "number": 7, "html_url": "https://github.com/example/fixture/pull/7",
    }])
    monkeypatch.setattr(api, "commit", lambda *_: {"parents": [{"sha": "a" * 40}]})
    monkeypatch.setattr(api, "compare_paths", lambda *_: {"Dockerfile"})
    monkeypatch.setattr(api, "file_at", lambda *_: b"FROM scratch\n")
    monkeypatch.setattr(api, "repository_info", lambda: {"id": 123})
    result = api.create_bundle_pull_request(
        branch="deployflow/plan-1", base_branch="main", base_sha="a" * 40,
        files={"Dockerfile": "FROM scratch\n"}, title="Review", body="Approved",
    )
    assert result == {"repository_id": 123, "head_sha": "b" * 40, "number": 7, "url": "https://github.com/example/fixture/pull/7"}
