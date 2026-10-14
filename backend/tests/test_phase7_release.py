import asyncio
import copy
from uuid import UUID

import pytest
import yaml
from sqlalchemy import select
from test_phase4 import assessment, intent, profile
from test_phase6 import prepared as prepared_plan

from app.bootstrap.generator import canonical_digest, render
from app.deployments.preparation import ReleasePreparationError, prepare_release_candidate
from app.deployments.release import DEPLOYMENT_PATH, ReleaseError, bind_verified_image
from app.kubernetes.policy import ManifestPolicyError
from app.persistence.database import Session
from app.persistence.models import (
    InfrastructurePlan,
    InfrastructurePullRequest,
    PlanApproval,
    ReleaseFile,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

APPLICATION_ID = "8f047bbc-8337-4df7-98f4-f28be0f047f4"
IMAGE_DIGEST = "sha256:" + "b" * 64


def prepared():
    target = {**profile(), "application_id": APPLICATION_ID}
    bundle = render(assessment(), intent(), target)
    arguments = {
        "bundle_files": bundle.files,
        "bundle_digest": bundle.digest,
        "image_binding": bundle.validation["image_binding"],
        "image_repository": "ghcr.io/example/fixture",
        "image_digest": IMAGE_DIGEST,
        "profile": target,
        "application_id": APPLICATION_ID,
    }
    return bundle, arguments


def test_release_binds_only_the_approved_deployment_image():
    bundle, arguments = prepared()
    release = bind_verified_image(**arguments)
    assert release.source_bundle_digest == bundle.digest
    assert release.digest == canonical_digest(release.files)
    assert set(release.files) == {path for path in bundle.files if path.startswith("deploy/kubernetes/")}
    assert release.image_reference == f"ghcr.io/example/fixture@{IMAGE_DIGEST}"
    assert release.files[DEPLOYMENT_PATH] == bundle.files[DEPLOYMENT_PATH].replace(
        bundle.validation["image_binding"]["preview_reference"], release.image_reference, 1
    )
    assert bundle.files[DEPLOYMENT_PATH] != release.files[DEPLOYMENT_PATH]
    assert "@sha256:" not in bundle.files[DEPLOYMENT_PATH]
    for path in release.files.keys() - {DEPLOYMENT_PATH}:
        assert release.files[path] == bundle.files[path]
    deployment = yaml.safe_load(release.files[DEPLOYMENT_PATH])
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == release.image_reference


def test_release_keeps_external_ingress_unchanged():
    target = {**profile(), "application_id": APPLICATION_ID}
    external = copy.deepcopy(intent())
    external["connectivity"]["exposure"] = {
        "kind": "external", "hostname": "fixture.deployflow.local", "path": "/",
    }
    bundle = render(assessment(), external, target)
    release = bind_verified_image(
        bundle_files=bundle.files,
        bundle_digest=bundle.digest,
        image_binding=bundle.validation["image_binding"],
        image_repository="ghcr.io/example/fixture",
        image_digest=IMAGE_DIGEST,
        profile=target,
        application_id=APPLICATION_ID,
    )
    assert release.files["deploy/kubernetes/ingress.yaml"] == bundle.files["deploy/kubernetes/ingress.yaml"]


@pytest.mark.parametrize("change", [
    {"bundle_digest": "0" * 64},
    {"image_repository": "ghcr.io/other/fixture"},
    {"image_digest": "sha256:short"},
    {"application_id": "another-application"},
])
def test_release_rejects_unapproved_or_mismatched_inputs(change):
    _, arguments = prepared()
    with pytest.raises((ReleaseError, ManifestPolicyError)):
        bind_verified_image(**{**arguments, **change})


def test_release_rejects_duplicate_or_changed_image_binding():
    _, arguments = prepared()
    preview = arguments["image_binding"]["preview_reference"]
    repeated = dict(arguments["bundle_files"])
    repeated[DEPLOYMENT_PATH] += f"# {preview}\n"
    with pytest.raises(ReleaseError, match="image_binding_mismatch"):
        bind_verified_image(**{**arguments, "bundle_files": repeated, "bundle_digest": canonical_digest(repeated)})

    changed = dict(arguments["bundle_files"])
    changed[DEPLOYMENT_PATH] = changed[DEPLOYMENT_PATH].replace(preview, "ghcr.io/example/fixture:other")
    with pytest.raises(ReleaseError, match="image_binding_mismatch"):
        bind_verified_image(**{**arguments, "bundle_files": changed, "bundle_digest": canonical_digest(changed)})


def test_release_rejects_unmanaged_manifest_path():
    _, arguments = prepared()
    files = dict(arguments["bundle_files"])
    files["deploy/kubernetes/secret.yaml"] = "kind: Secret\n"
    with pytest.raises(ReleaseError, match="reviewed_bundle_mismatch"):
        bind_verified_image(**{**arguments, "bundle_files": files, "bundle_digest": canonical_digest(files)})


def release_source(client, monkeypatch):
    plan_data = prepared_plan(client, monkeypatch)
    plan_id = UUID(plan_data["id"])
    with Session.begin() as db:
        plan = db.get(InfrastructurePlan, plan_id)
        plan.status = "approved"
        approval = PlanApproval(plan_id=plan_id, bundle_digest=plan.bundle_digest, actor_id="test-reviewer")
        db.add(approval)
        pr = InfrastructurePullRequest(
            plan_id=plan_id, repository_id=123, owner="example", repository="fixture",
            base_branch="main", base_sha="a" * 40, head_branch="deployflow/test",
            head_sha="b" * 40, number=7, merged_sha="c" * 40,
            status="verified",
        )
        db.add(pr)
        db.flush()
        build = VerifiedBuild(
            pull_request_id=pr.id, run_id=12, run_attempt=1, merged_sha="c" * 40,
            image_repository=plan.validation_json["image_binding"]["repository"],
            image_digest=IMAGE_DIGEST,
        )
        db.add(build)
        db.flush()
        return pr.id, build.id


def fake_github(monkeypatch, *, branch_sha="c" * 40):
    class FakeGitHub:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def pull_request(self, _):
            return {
                "merged": True, "merge_commit_sha": None,
                "base": {"ref": "main", "repo": {"id": 123}},
                "head": {"ref": "deployflow/test", "sha": "b" * 40},
            }

        def merged_event_sha(self, _):
            return "c" * 40

        def repository_info(self):
            return {"id": 123}

        def branch_sha(self, _):
            return branch_sha

        def workflow_run(self, _):
            return {
                "status": "completed", "event": "push", "conclusion": "success",
                "head_sha": "c" * 40, "head_branch": "main",
                "path": ".github/workflows/deployflow.yml",
                "repository": {"id": 123}, "run_attempt": 1,
            }

    monkeypatch.setattr("app.deployments.preparation.GitHubAPI", FakeGitHub)


def test_preparation_stores_exact_release_once(client, monkeypatch):
    pr_id, build_id = release_source(client, monkeypatch)
    fake_github(monkeypatch)
    calls = []

    async def passed(role, tool, arguments):
        calls.append((role, tool, arguments))
        return {"status": "passed", "checks": [{"code": "admission_passed"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", passed)
    first = asyncio.run(prepare_release_candidate(pr_id, build_id))
    second = asyncio.run(prepare_release_candidate(pr_id, build_id))
    assert first == second
    assert len(calls) == 2
    assert calls[0][0:2] == ("validator", "kube.validate_manifests")
    with Session() as db:
        assert len(db.scalars(select(ReleaseSpecificationRecord)).all()) == 1
        record = db.get(ReleaseSpecificationRecord, first)
        files = {row.path: row.content for row in db.scalars(select(ReleaseFile)).all()}
        assert record.manifest_digest == canonical_digest(files)
        assert record.image_reference == f"ghcr.io/example/fixture@{IMAGE_DIGEST}"
        assert files == calls[0][2]["files"]
        assert len(files) == 3


def test_preparation_rejects_reverted_branch_before_validation(client, monkeypatch):
    pr_id, build_id = release_source(client, monkeypatch)
    fake_github(monkeypatch, branch_sha="d" * 40)

    async def unexpected(*_):
        pytest.fail("validator must not be called for a reverted merge")

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", unexpected)
    with pytest.raises(ReleasePreparationError, match="verified_revision_not_target_head"):
        asyncio.run(prepare_release_candidate(pr_id, build_id))
    with Session() as db:
        assert db.scalars(select(ReleaseSpecificationRecord)).all() == []


def test_preparation_requires_final_admission(client, monkeypatch):
    pr_id, build_id = release_source(client, monkeypatch)
    fake_github(monkeypatch)

    async def denied(*_):
        return {"status": "failed", "checks": [{"code": "cluster_admission_rejected"}]}

    monkeypatch.setattr("app.deployments.preparation.call_kubernetes", denied)
    with pytest.raises(ReleasePreparationError, match="release_admission_failed"):
        asyncio.run(prepare_release_candidate(pr_id, build_id))
    with Session() as db:
        assert db.scalars(select(ReleaseSpecificationRecord)).all() == []
