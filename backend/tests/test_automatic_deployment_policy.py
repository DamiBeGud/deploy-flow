"""Explicit opt-in and signed event-to-worker transitions in the isolated DB."""
import hashlib
import hmac
import importlib.util
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from test_phase7_source_updates import SOURCE, source_api

from app.api.identity import Identity, get_identity
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments import execution, jobs
from app.deployments.preparation import ReleasePreparationError, _source
from app.dispatcher import dispatch_delivery_once, dispatch_execution_once, dispatch_github_once
from app.integrations.github.compatibility import HANDLER_VERSION, VERSION
from app.integrations.github.jobs import execute as execute_github
from app.integrations.github.source_updates import reconcile_sources
from app.main import app
from app.persistence.database import Session, engine
from app.persistence.models import (
    DeploymentPolicy,
    DeploymentRun,
    ExecutionJob,
    InfrastructurePlan,
    InfrastructurePullRequest,
    VerifiedBuild,
)
from app.webhook_main import app as webhook_app

BEFORE = 'import express from "express";\nconst app = express();\napp.get("/api/info", (req, res) => {\n res.json({message: "one"});\n});\napp.get("/healthz", (req, res) => {\n res.json({status: "ok"});\n});\napp.listen(3000);\n'
AFTER = BEFORE.replace(' res.json({message: "one"});', ' const value = [1, 2, 3].map(n => n * 2);\n res.status(200).json({values: value, message: "two"});')


def handler_source(client, monkeypatch, *, opt_in):
    pr_id, api, _ = source_api(client, monkeypatch)
    api.before, api.after = BEFORE, AFTER
    with Session.begin() as db:
        pr = db.get(InfrastructurePullRequest, pr_id)
        plan = db.get(InfrastructurePlan, pr.plan_id)
        app_id = plan.application_id
        if opt_in:
            db.add(DeploymentPolicy(application_id=app_id, environment_profile_id=plan.environment_profile_id,
                                    enabled=False, actor_id='test-operator', source_compatibility_policy=HANDLER_VERSION))
    return pr_id, api, app_id


def authorize(client, monkeypatch, app_id, body):
    monkeypatch.setattr(settings(), 'auth_mode', 'oidc')
    monkeypatch.setattr(settings(), 'deployment_execution_enabled', True)
    app.dependency_overrides[get_identity] = lambda: Identity('local-developer', 'local-organization', frozenset({'deployer'}))
    return client.put(f'/api/v1/applications/{app_id}/deployment-policy', json=body,
                      headers={'Idempotency-Key': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()})


def test_handler_changes_require_explicit_compatibility_opt_in(client, monkeypatch):
    pr, _, app_id = handler_source(client, monkeypatch, opt_in=False)
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'deployment_requirements_need_review'
        assert db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE)) is None
    response = authorize(client, monkeypatch, app_id, {'enabled': False, 'source_compatibility_policy': HANDLER_VERSION})
    assert response.status_code == 200, response.text
    reconcile_sources(pr)
    with Session() as db:
        build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE))
        assert build.compatibility_json['policy'] == HANDLER_VERSION
        assert len(list(db.scalars(select(ExecutionJob)))) == 0
    policy = client.get(f'/api/v1/applications/{app_id}/deployment-policy').json()
    assert policy['source_compatibility_policy'] == HANDLER_VERSION
    assert policy['enabled'] is False


@pytest.mark.parametrize('change', [
    lambda s: s.replace('app.listen(3000)', 'app.listen(8080)'),
    lambda s: s.replace('status: "ok"', 'status: "broken"'),
    lambda s: s.replace('from "express"', 'from "other-runtime"'),
    lambda s: s.replace('"/api/info"', '"/other"'),
    lambda s: s.replace('const value =', 'const value = process.env.NEW_CONFIG; const another ='),
    lambda s: s.replace('const value =', 'const value = globalThis["hidden"]; const another ='),
    lambda s: s + 'setInterval(() => {}, 50);',
])
def test_broader_handler_policy_preserves_deployment_contract(client, monkeypatch, change):
    pr, api, _ = handler_source(client, monkeypatch, opt_in=True)
    api.after = change(AFTER)
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'deployment_requirements_need_review'
        assert db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE)) is None


def test_compatibility_policy_revoked_between_build_and_preparation(client, monkeypatch):
    pr, _, _ = handler_source(client, monkeypatch, opt_in=True)
    reconcile_sources(pr)
    with Session.begin() as db:
        db.scalar(select(DeploymentPolicy)).source_compatibility_policy = VERSION
    with Session() as db:
        build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE))
        with pytest.raises(ReleasePreparationError, match='source_compatibility_policy_revoked'):
            _source(db, pr, build.id, local_k3s_profile())


@pytest.mark.parametrize('expression', ['load("new-runtime-file")', 'Object["get" + "PrototypeOf"](res)', 'res.app.get("NEW_REQUIREMENT")'])
def test_handler_policy_does_not_inherit_imported_alias_or_dynamic_runtime_authority(client, monkeypatch, expression):
    pr, api, _ = handler_source(client, monkeypatch, opt_in=True)
    prefix = 'import { readFileSync as load } from "node:fs";\n'
    api.before = prefix + BEFORE
    api.after = prefix + AFTER.replace('[1, 2, 3].map(n => n * 2)', expression)
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'deployment_requirements_need_review'


def test_source_bound_and_mode_are_preserved_when_omitted_and_null_is_explicit(client, monkeypatch):
    _, _, app_id = handler_source(client, monkeypatch, opt_in=True)
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'allowed_source_sha': SOURCE}).status_code == 200
    assert authorize(client, monkeypatch, app_id, {'enabled': False}).status_code == 200
    value = client.get(f'/api/v1/applications/{app_id}/deployment-policy').json()
    assert value['allowed_source_sha'] == SOURCE and value['source_compatibility_policy'] == HANDLER_VERSION
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'allowed_source_sha': None}).status_code == 200
    assert client.get(f'/api/v1/applications/{app_id}/deployment-policy').json()['authorization_state'] == 'future_compatible_sources'
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'source_compatibility_policy': 'unknown'}).status_code == 422
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'source_compatibility_policy': None}).status_code == 422
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'allowed_sources_sha': SOURCE}).status_code == 422


def test_idempotency_does_not_conflate_omitted_authority_with_explicit_null(client, monkeypatch):
    _, _, app_id = handler_source(client, monkeypatch, opt_in=True)
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'allowed_source_sha': SOURCE}).status_code == 200
    path = f'/api/v1/applications/{app_id}/deployment-policy'
    headers = {'Idempotency-Key': 'one-policy-change-only'}
    assert client.put(path, json={'enabled': False}, headers=headers).status_code == 200
    assert client.put(path, json={'enabled': False, 'allowed_source_sha': None}, headers=headers).status_code == 409
    assert client.get(path).json()['allowed_source_sha'] == SOURCE


@pytest.mark.parametrize('origin', ['direct_push', 'merged_pr'])
def test_signed_ci_completion_dispatches_real_claims_through_preflight_and_execution(client, monkeypatch, origin):
    pr, _, app_id = handler_source(client, monkeypatch, opt_in=True)
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'allowed_source_sha': None}).status_code == 200
    monkeypatch.setattr(settings(), 'github_webhook_secret', 'synthetic-webhook-only')
    monkeypatch.setattr(settings(), 'github_installation_id', 456)
    body = json.dumps({'action': 'completed', 'repository': {'id': 123}, 'installation': {'id': 456},
                       'workflow_run': {'event': 'push', 'head_sha': SOURCE}, 'test_origin': origin}).encode()
    headers = {'X-GitHub-Delivery': 'test-completion-' + origin.replace('_', '-'), 'X-GitHub-Event': 'workflow_run',
               'X-Hub-Signature-256': 'sha256=' + hmac.new(b'synthetic-webhook-only', body, hashlib.sha256).hexdigest()}
    with TestClient(webhook_app) as receiver:
        assert receiver.post('/webhooks/github', content=body, headers=headers).status_code == 202
        assert receiver.post('/webhooks/github', content=body, headers=headers).json()['duplicate'] is True
    for _ in range(2):
        published = []
        dispatch_github_once(published.append)
        for identifier in published:
            execute_github(identifier)
    with Session() as db:
        assert db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE)) is not None
    async def passed(*_): return {'status': 'passed'}
    monkeypatch.setattr('app.deployments.preparation.call_kubernetes', passed)
    monkeypatch.setattr('app.deployments.jobs.call_kubernetes', passed)
    monkeypatch.setattr(execution, 'call_kubernetes', passed)
    for _ in range(2):
        published = []
        assert dispatch_delivery_once(published.append) >= 1
        for identifier in published:
            jobs.execute(identifier)
    published = []
    assert dispatch_execution_once(published.append) == 1
    writes = []
    monkeypatch.setattr(execution, 'apply_owned', lambda item, *_: (writes.append(item['kind']) or {'uid': 'synthetic-' + item['kind'], 'generation': 1}))
    monkeypatch.setattr(execution, 'rollout_status', lambda *_: {'status': 'ready', 'desired_replicas': 1, 'ready_replicas': 1, 'available_replicas': 1})
    monkeypatch.setattr(execution, 'service_health', lambda *_: {'status': 'passed'})
    execution.execute(published[0])
    execution.execute(published[0])
    with Session() as db:
        run = db.scalar(select(DeploymentRun))
        assert run.status == 'succeeded'
        assert run.health_json['status'] == 'passed'
        assert len(list(db.scalars(select(ExecutionJob)))) == 1
    assert len(writes) == 3
    reconcile_sources(pr)
    with Session() as db:
        assert len(list(db.scalars(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE)))) == 1


def test_migration_preserves_policy_rows_and_existing_authority(client, monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    _, _, app_id = handler_source(client, monkeypatch, opt_in=True)
    with engine.begin() as connection:
        connection.execute(text('UPDATE deployment_policies SET allowed_source_sha=:sha WHERE application_id=:id'), {'sha': SOURCE, 'id': app_id})
        connection.execute(text('ALTER TABLE deployment_policies DROP COLUMN source_compatibility_policy'))
        spec = importlib.util.spec_from_file_location('automatic_policy_migration', 'migrations/versions/e4b92f7a6103_automatic_source_policy.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        row = connection.execute(text('SELECT enabled, allowed_source_sha, source_compatibility_policy FROM deployment_policies')).one()
        assert tuple(row) == (False, SOURCE, VERSION)


def test_dashboard_exposes_policy_gate_and_source_review_without_fabricating_a_deployment(client, monkeypatch):
    pr, _, app_id = handler_source(client, monkeypatch, opt_in=False)
    reconcile_sources(pr)
    value = next(a for a in client.get('/api/v1/applications').json() if a['id'] == str(app_id))
    assert value['deployment_execution_available'] is False
    assert value['deployment_policy_enabled'] is False
    assert value['latest_deployment'] is None and value['last_successful_deployment'] is None
    assert value['source_verification']['status'] == 'needs_review'
    assert value['source_verification']['error_code'] == 'deployment_requirements_need_review'


def test_handler_policy_revocation_is_rechecked_immediately_before_apply(client, monkeypatch):
    pr, _, app_id = handler_source(client, monkeypatch, opt_in=True)
    assert authorize(client, monkeypatch, app_id, {'enabled': True}).status_code == 200
    reconcile_sources(pr)
    async def passed(*_): return {'status': 'passed'}
    monkeypatch.setattr('app.deployments.preparation.call_kubernetes', passed)
    monkeypatch.setattr('app.deployments.jobs.call_kubernetes', passed)
    for _ in range(2):
        published = []
        dispatch_delivery_once(published.append)
        for identifier in published:
            jobs.execute(identifier)
    with Session() as db:
        job_id = db.scalar(select(ExecutionJob.id))
        run_id = db.scalar(select(DeploymentRun.id))
        assert job_id is not None
    assert authorize(client, monkeypatch, app_id, {'enabled': True, 'source_compatibility_policy': VERSION}).status_code == 200
    monkeypatch.setattr(execution, 'apply_owned', lambda *_: pytest.fail('revoked handler policy must not apply'))
    execution.execute(str(job_id))
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        assert run.status == 'failed'
        assert run.error_code == 'source_compatibility_policy_revoked'
