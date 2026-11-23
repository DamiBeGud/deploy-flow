import base64
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from kubernetes.client.exceptions import ApiException
from sqlalchemy import select

from app.bootstrap import managed_rails
from app.bootstrap.generator import canonical_digest
from app.bootstrap.rails_jobs import eligible, process_one
from app.deployments.release import bind_verified_image
from app.kubernetes.policy import ManifestPolicyError, checked_manifests
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, Application, AssessmentVersion, RailsProvisionJob
from app.repository.scanner import Scanner
from app.skills.registry import SkillRegistry

SHA = "a" * 40
PROFILE = managed_rails.RailsProfile(
    owner_id="local-developer",
    organization_id="local-organization",
    repository="example/recipes",
    revision="pilot",
    source_sha=SHA,
)
SOURCE = {
    "Gemfile": "gem 'rails'\ngem 'puma'\ngem 'pg'\n",
    "Gemfile.lock": "GEM\n  specs:\n    rails (8.1.3)\n    puma (8.0.2)\n    pg (1.6.3)\n"
                    "\nRUBY VERSION\n   ruby 3.3.10p183\n\nBUNDLED WITH\n   2.5.22\n",
    "config/puma.rb": """bind "tcp://0.0.0.0:#{ENV.fetch('PORT','8401')}"\n""",
    "config/routes.rb": (
        "Rails.application.routes.draw do\n get '/health/live', to: 'health#live'\n"
        " get '/ready', to: 'health#ready'\nend\n"
    ),
    "config/database.yml": "default: &default\n  adapter: postgresql\n"
                           "  url: <%= ENV.fetch('DATABASE_URL') %>\nproduction:\n  <<: *default\n",
    "db/migrate/20261120000000_create_recipes.rb": "class CreateRecipes < ActiveRecord::Migration; end\n",
    "config/environments/production.rb": '  config.secret_key_base = ENV.fetch("SECRET_KEY_BASE")\n',
    "Rakefile": "Rake::TestTask.new(:unit) do |task|\nend\n  Rails.application.load_tasks\n",
    "test/unit_test.rb": "",
}


def fixture(tmp_path, changes=None):
    for name, contents in (SOURCE | (changes or {})).items():
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)
    repository = {
        "url": "https://github.com/example/recipes", "owner": "example", "name": "recipes",
        "revision": PROFILE.revision, "commit_sha": SHA,
    }
    application = Application(
        id=uuid4(), organization_id=PROFILE.organization_id, owner_id=PROFILE.owner_id,
        name="recipes", repository_url=repository["url"], revision=PROFILE.revision,
    )
    scanned = Scanner(tmp_path).scan(repository=repository, application_name=application.name)
    run = AnalysisRun(
        id=uuid4(), application_id=application.id, actor_id=application.owner_id,
        revision=PROFILE.revision, correlation_id="synthetic-rails",
        analysis_mode="deterministic", status="completed", commit_sha=SHA, assessment_version=1,
        scanner_facts_json=scanned.facts,
    )
    return application, run, scanned.document


class FakeCore:
    def __init__(self):
        self.entries = {}
        self.secrets = {}
        self.version = 1
        self.creates = 0
        self.drop_response = False

    def read_namespaced_config_map(self, name, namespace, **kwargs):
        assert (name, namespace) == (managed_rails.CATALOG_NAME, managed_rails.CATALOG_NAMESPACE)
        return SimpleNamespace(data=self.entries.copy(), metadata=SimpleNamespace(resource_version=str(self.version)))

    def patch_namespaced_config_map(self, name, namespace, body, **kwargs):
        self.read_namespaced_config_map(name, namespace)
        assert kwargs["_content_type"] == "application/merge-patch+json"
        if body["metadata"]["resourceVersion"] != str(self.version):
            raise ApiException(status=409)
        self.version += 1
        self.entries.update(body["data"])

    def read_namespaced_secret(self, name, namespace, **kwargs):
        assert namespace == "deployflow-test" and name in managed_rails.SECRET_NAMES
        if name not in self.secrets:
            raise ApiException(status=404)
        return self.secrets[name]

    def create_namespaced_secret(self, namespace, body, **kwargs):
        name = body["metadata"]["name"]
        assert namespace == "deployflow-test" and name in managed_rails.SECRET_NAMES
        if name in self.secrets:
            raise ApiException(status=409)
        self.creates += 1
        self.secrets[name] = SimpleNamespace(
            type=body["type"], immutable=body["immutable"],
            metadata=SimpleNamespace(deletion_timestamp=None, annotations=body["metadata"]["annotations"]),
            data=body["data"],
        )
        if self.drop_response:
            self.drop_response = False
            raise managed_rails.ManagedRailsError("rails_cluster_unavailable", retryable=True)


@pytest.fixture
def core(monkeypatch):
    fake = FakeCore()

    @contextmanager
    def fake_api():
        yield fake

    monkeypatch.setenv("DF_MANAGED_RAILS_PROFILE", PROFILE.model_dump_json())
    monkeypatch.setattr(managed_rails, "_catalog_api", fake_api)
    return fake


@pytest.mark.parametrize("adapter, override", [("sqlite3", ""), ("postgresql", "  database: another\n")])
def test_database_driver_by_itself_cannot_authorize_provisioning(tmp_path, adapter, override):
    database = SOURCE["config/database.yml"].replace("adapter: postgresql", f"adapter: {adapter}") + override
    application, run, assessment = fixture(tmp_path, {"config/database.yml": database})
    with pytest.raises(managed_rails.ManagedRailsError, match="rails_critical_evidence_missing"):
        eligible(PROFILE, application, run, assessment)


def test_profile_denies_unapproved_sha_fallback_and_conflicting_model_claim(tmp_path):
    application, run, assessment = fixture(tmp_path)
    with pytest.raises(managed_rails.ManagedRailsError, match="outside_approved_scope"):
        eligible(PROFILE.model_copy(update={"source_sha": "b" * 40}), application, run, assessment)
    run.analysis_mode = "agent"
    assessment = assessment | {"mode": "agent"}
    run.reproducibility_json = {"fallback_used": True}
    with pytest.raises(managed_rails.ManagedRailsError, match="assessment_not_eligible"):
        eligible(PROFILE, application, run, assessment)
    run.reproducibility_json = {"fallback_used": False, "provider_response_ids": ["synthetic-response"]}
    run.model_name = "synthetic-provider-model"
    run.model_provider = "deepseek"
    assessment["conflicts"] = ["Model claim conflicts with scanner fact: rails.database.adapter"]
    with pytest.raises(managed_rails.ManagedRailsError, match="critical_evidence_conflict"):
        eligible(PROFILE, application, run, assessment)
    assessment["conflicts"] = []
    with pytest.raises(managed_rails.ManagedRailsError, match="agent_source_review_incomplete"):
        eligible(PROFILE, application, run, assessment)
    assessment["command_inference"] = {
        "schema_version": "command-inference-v1", "source_sha": SHA,
        "commands": {"start": {"status": "conflict", "reason": "Competing launchers"}},
    }
    with pytest.raises(managed_rails.ManagedRailsError, match="critical_evidence_conflict"):
        eligible(PROFILE, application, run, assessment)


def test_catalog_rejects_foreign_secret_and_never_rotates_ready_credentials(tmp_path, core):
    application, run, assessment = fixture(tmp_path)
    binding = eligible(PROFILE, application, run, assessment)
    core.drop_response = True
    with pytest.raises(managed_rails.ManagedRailsError, match="rails_cluster_unavailable"):
        managed_rails.provision_rails(binding)
    secret = core.secrets[binding.secret_name]
    owner = secret.metadata.annotations.copy()
    secret.metadata.annotations["deployflow.io/application-id"] = str(uuid4())
    with pytest.raises(managed_rails.ManagedRailsError, match="ownership_conflict"):
        managed_rails.provision_rails(binding)
    assert managed_rails.managed_binding(str(application.id), SHA) is None
    secret.metadata.annotations = owner
    managed_rails.provision_rails(binding)
    assert core.creates == 1
    core.secrets.clear()
    with pytest.raises(managed_rails.ManagedRailsError, match="ready_secret_missing"):
        managed_rails.provision_rails(binding)
    assert core.creates == 1


def test_normal_api_provisions_once_then_renders_only_authorized_references(tmp_path, client, core):
    application, run, assessment = fixture(tmp_path)
    with Session.begin() as db:
        db.add(application)
        db.flush()
        db.add(run)
        db.flush()
        db.add(AssessmentVersion(run_id=run.id, version=1, document=assessment, actor_id="deterministic-scanner"))
    query = f"?analysis_run_id={run.id}"
    root = f"/api/v1/applications/{application.id}"
    recommend = f"{root}/kubernetes-recommendation{query}"
    start = f"{root}/rails-provisioning{query}"
    response = client.get(recommend)
    assert response.status_code == 200, response.text
    assert response.json()["rails_provisioning"]["status"] == "required"
    for _ in range(2):
        response = client.post(start)
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "queued"
    core.drop_response = True
    assert process_one()
    with Session.begin() as db:
        job = db.scalar(select(RailsProvisionJob))
        assert job.attempts == 1 and job.status == "queued" and job.error_code == "rails_cluster_unavailable"
        job.next_attempt_at = job.created_at
    assert process_one()
    assert not process_one()
    assert client.post(start).json() == {"status": "ready", "code": None}
    assert core.creates == 1
    result = client.get(recommend).json()
    assert result["rails_provisioning"]["status"] == "ready"
    assert result["approved_database"] == {"migration": "bundle exec rails db:migrate", "ephemeral": True}
    assert not result["generation_blocker"]
    intent = result["intent"]
    saved = client.put(f"{root}/deployment-intent", json={
        **{key: intent[key] for key in ("schema_version", "environment_profile_id", "connectivity", "build")},
        "analysis_run_id": str(run.id), "expected_version": 0, "expected_assessment_version": 1,
    }, headers={"Idempotency-Key": "save-rails-intent"})
    assert saved.status_code == 201, saved.text
    planned = client.post(f"{root}/infrastructure-plans", json={
        "analysis_run_id": str(run.id), "expected_assessment_version": 1,
        "intent_version": saved.json()["version"], "environment_profile_id": "local-k3s",
    }, headers={"Idempotency-Key": "generate-rails-plan"})
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    files = {row["path"]: row["content"] for row in plan["files"]}
    profile = {"namespace": "deployflow-test", "registry": "ghcr.io"}
    release = bind_verified_image(
        bundle_files=files, bundle_digest=plan["bundle_digest"], image_binding=plan["validation"]["image_binding"],
        image_repository="ghcr.io/example/recipes", image_digest="sha256:" + "a" * 64,
        profile=profile, application_id=str(application.id),
    )
    marshalled = json.dumps([result, plan, release.files, core.entries])
    for secret in core.secrets.values():
        for data in secret.data.values():
            assert data not in marshalled
            assert base64.b64decode(data).decode() not in marshalled
    configmap = "deploy/kubernetes/configmap.yaml"
    assert yaml.safe_load(release.files[configmap])["data"][managed_rails.SOURCE_MARKER] == SHA
    invalid = copy.copy(release.files)
    invalid[configmap] = invalid[configmap].replace(SHA, "b" * 40)
    with pytest.raises(ManifestPolicyError, match="rails_binding_denied"):
        checked_manifests(
            invalid, digest=canonical_digest(invalid), application_id=str(application.id), **profile,
        )


def test_skills_select_read_only_rails_bundle_for_pinned_layout(tmp_path):
    _, run, _ = fixture(tmp_path)
    rails = next(
        item for item in SkillRegistry(Path("skills"), max_context_chars=40_000).load().select(run.scanner_facts_json)
        if item.metadata.id == "rails"
    )
    assert rails.metadata.version == "1.0.0" and len(rails.content_hash) == 64
    assert set(rails.metadata.allowed_tools) == {"repo.read_file", "repo.search_text"}
