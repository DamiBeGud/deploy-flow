"""Source releases use real DB/dispatcher/claims with bounded fake external APIs."""
import io
import json
import zipfile

import pytest
from sqlalchemy import func, select
from test_phase7_release import release_source

from app.bootstrap.profiles import local_k3s_profile
from app.deployments import jobs
from app.deployments.preparation import ReleasePreparationError, _check_github, _source
from app.dispatcher import dispatch_delivery_once
from app.integrations.github.api import GitHubPhase6Error
from app.integrations.github.source_updates import reconcile_sources
from app.persistence.database import Session
from app.persistence.models import (
    DeliveryJob,
    DeploymentRun,
    InfrastructurePlanFile,
    InfrastructurePullRequest,
    PlanApproval,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

BASE = 'c' * 40
SOURCE = 'd' * 40


def source_api(client, monkeypatch, pr_id=None):
    if pr_id is None:
        pr_id, _ = release_source(client, monkeypatch)
    with Session() as db:
        files = {row.path: row.content for row in db.scalars(select(InfrastructurePlanFile))}
    class API:
        head = SOURCE
        path = 'src/app.js'
        before = 'const RELEASE = "one";\nres.json({release: RELEASE});\napp.get("/healthz", (req, res) => {\n res.json({status: "ok"});\n});\n'
        after = before.replace('"one"', '"two"')
        modified = None
        event = 'push'
        branch = 'main'
        workflow = '.github/workflows/deployflow.yml'
        repo = 123
        conclusion = 'success'
        status = 'completed'
        artifact = True
        artifact_digest = 'sha256:' + 'e' * 64
        artifact_image = 'ghcr.io/example/fixture'
        artifact_sha = SOURCE
        run_attempt = 1
        run_id = 20
        def __init__(self, *_, **__): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def repository_info(self): return {'id': self.repo}
        def branch_sha(self, *_): return self.head
        def require_descendant(self, base, head):
            if head == 'a' * 40:
                raise GitHubPhase6Error('source_history_requires_review')
        def compare_paths(self, *_): return {self.path}
        def file_at(self, path, sha):
            if path in files:
                return (files[path] + ('modified' if path == self.modified else '')).encode()
            return (self.before if sha == BASE else self.after).encode()
        def workflow_runs(self, *_): return [{'id': self.run_id, 'run_attempt': self.run_attempt}]
        def workflow_run(self, *_): return {'id': self.run_id, 'status': self.status, 'conclusion': self.conclusion, 'event': self.event, 'head_sha': self.head, 'head_branch': self.branch, 'path': self.workflow, 'repository': {'id': self.repo}, 'run_attempt': self.run_attempt}
        def run_artifacts(self, *_): return [{'id': 80, 'name': 'deployflow-build', 'size_in_bytes': 1000, 'expired': False}] if self.artifact else []
        def artifact_zip(self, *_):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as z:
                z.writestr('deployflow-build.json', json.dumps({'repository_id': 123, 'commit_sha': self.artifact_sha, 'run_id': self.run_id, 'run_attempt': self.run_attempt, 'image_repository': self.artifact_image, 'image_digest': self.artifact_digest}))
            return output.getvalue()
        def pull_request(self, *_): return {'merged': True, 'merge_commit_sha': BASE, 'base': {'ref': 'main', 'repo': {'id': 123}}, 'head': {'ref': 'deployflow/test', 'sha': 'b' * 40}}
    monkeypatch.setattr('app.integrations.github.source_updates.GitHubAPI', API)
    monkeypatch.setattr('app.deployments.preparation.GitHubAPI', API)
    monkeypatch.setattr('app.integrations.github.source_updates.verify_registry_digest', lambda *_: None)
    monkeypatch.setattr('app.integrations.github.verification.verify_registry_digest', lambda *_: None)
    return pr_id, API, files


def test_source_build_prepares_distinct_immutable_release_without_enabled_policy(client, monkeypatch):
    pr, api, _ = source_api(client, monkeypatch)
    reconcile_sources(pr)
    reconcile_sources(pr)  # duplicate notification
    api.run_attempt = 2
    reconcile_sources(pr)  # rerun is not a second source admission
    api.run_attempt = 1
    with Session() as db:
        builds = list(db.scalars(select(VerifiedBuild).order_by(VerifiedBuild.verified_at)))
        assert len(builds) == 2
        build = builds[-1]
        assert build.merged_sha == BASE and build.source_sha == SOURCE
        assert build.artifact_id == 80
        assert build.compatibility_json['status'] == 'compatible'
        assert build.compatibility_json['approval_id']
        job = db.scalar(select(DeliveryJob))
        job_id = job.id
    async def passed(*_): return {'status': 'passed'}
    monkeypatch.setattr('app.deployments.preparation.call_kubernetes', passed)
    monkeypatch.setattr('app.deployments.jobs.call_kubernetes', passed)
    published = []
    assert dispatch_delivery_once(published.append) == 1
    jobs.execute(published[0])
    with Session() as db:
        assert db.get(DeliveryJob, job_id).status == 'completed'
        release = db.scalar(select(ReleaseSpecificationRecord))
        assert release.source_sha == SOURCE and release.merged_sha == BASE
        preflight = db.scalar(select(DeliveryJob).where(DeliveryJob.kind == 'preflight'))
        preflight_id = preflight.id
    jobs.execute(str(preflight_id))
    with Session() as db:
        assert db.scalar(select(DeploymentRun)).status == 'waiting_for_executor'
        assert len(list(db.scalars(select(ReleaseSpecificationRecord)))) == 1


@pytest.mark.parametrize(('field', 'value', 'code'), [
    ('repo', 999, 'github_repository_changed'),
    ('branch', 'other', 'workflow_run_untrusted'),
    ('event', 'pull_request', 'workflow_run_untrusted'),
    ('workflow', '.github/workflows/other.yml', 'workflow_run_untrusted'),
    ('conclusion', 'failure', 'workflow_run_untrusted'),
    ('artifact', False, 'build_artifact_missing'),
    ('artifact_sha', BASE, 'build_artifact_mismatch'),
    ('artifact_image', 'ghcr.io/other/image', 'build_artifact_mismatch'),
    ('artifact_digest', 'latest', 'build_artifact_invalid'),
    ('modified', 'Dockerfile', 'approved_infrastructure_changed'),
    ('modified', '.github/workflows/deployflow.yml', 'approved_infrastructure_changed'),
    ('path', 'package.json', 'deployment_requirements_need_review'),
    ('path', 'package-lock.json', 'deployment_requirements_need_review'),
    ('head', 'a' * 40, 'source_history_requires_review'),
])
def test_source_rejections_do_not_poison_bootstrap(client, monkeypatch, field, value, code):
    pr, api, _ = source_api(client, monkeypatch)
    setattr(api, field, value)
    reconcile_sources(pr)
    with Session() as db:
        row = db.get(InfrastructurePullRequest, pr)
        assert row.status == 'verified'
        assert row.source_error_code == code
        assert len(list(db.scalars(select(VerifiedBuild)))) == 1
        assert not list(db.scalars(select(DeliveryJob)))


@pytest.mark.parametrize('change', [
    'const PORT = 9999;', 'const port = 9999;', 'process.env.NEW_SETTING;',
    'server.listen(9999);', 'import pg from "pg";', 'eval("config");',
])
def test_deployment_requirement_edits_need_review(client, monkeypatch, change):
    pr, api, _ = source_api(client, monkeypatch)
    api.after += change
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'deployment_requirements_need_review'


def test_health_body_change_requires_review(client, monkeypatch):
    pr, api, _ = source_api(client, monkeypatch)
    api.after = api.before.replace('status: "ok"', 'status: "broken"')
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'deployment_requirements_need_review'


def test_incomplete_ci_waits_and_registry_failure_blocks(client, monkeypatch):
    pr, api, _ = source_api(client, monkeypatch)
    api.status = 'in_progress'
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_status == 'awaiting_ci'
        assert len(list(db.scalars(select(VerifiedBuild)))) == 1
    api.status = 'completed'
    def reject(*_): raise GitHubPhase6Error('registry_digest_unverified')
    monkeypatch.setattr('app.integrations.github.source_updates.verify_registry_digest', reject)
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'registry_digest_unverified'


def test_branch_movement_blocks_before_preflight_and_revoked_approval_blocks(client, monkeypatch):
    pr, api, _ = source_api(client, monkeypatch)
    reconcile_sources(pr)
    with Session() as db:
        build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE))
        source = _source(db, pr, build.id, local_k3s_profile())
    api.head = 'f' * 40
    with pytest.raises(ReleasePreparationError, match='verified_revision_not_target_head'):
        _check_github(source)
    with Session.begin() as db:
        db.scalar(select(PlanApproval)).invalidated_at = db.scalar(select(func.now()))
    reconcile_sources(pr)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr).source_error_code == 'plan_no_longer_approved'


def test_two_releases_automatic_queue_rollback_and_notification_replay(client, monkeypatch):
    from test_phase7_execution import opted_in_job, prepared_run

    from app.api.identity import Identity, get_identity
    from app.deployments import execution as executor
    from app.main import app
    from app.persistence.models import ExecutionJob

    first_id = prepared_run(client, monkeypatch)
    first_job = opted_in_job(first_id, monkeypatch)
    writes = []
    monkeypatch.setattr(executor, 'apply_owned', lambda item, *_: (writes.append(item['kind']) or {'uid': 'owned-' + item['kind'], 'generation': 1}))
    monkeypatch.setattr(executor, 'rollout_status', lambda *_: {'status': 'ready', 'desired_replicas': 1, 'ready_replicas': 1, 'available_replicas': 1})
    monkeypatch.setattr(executor, 'service_health', lambda *_: {'status': 'passed'})
    executor.execute(str(first_job))
    with Session() as db:
        first = db.get(DeploymentRun, first_id)
        assert first.status == 'succeeded'
        first_release = first.release_id
        pr_id = db.get(VerifiedBuild, first.verified_build_id).pull_request_id
    _, api, _ = source_api(client, monkeypatch, pr_id)
    reconcile_sources(pr_id)
    with Session() as db:
        second_build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE))
        prepare_id = db.scalar(select(DeliveryJob.id).where(DeliveryJob.verified_build_id == second_build.id))
        second_build_id = second_build.id
    jobs.execute(str(prepare_id))
    with Session() as db:
        preflight_id = db.scalar(select(DeliveryJob.id).where(DeliveryJob.kind == 'preflight', DeliveryJob.verified_build_id == second_build_id))
    jobs.execute(str(preflight_id))
    with Session() as db:
        second = db.scalar(select(DeploymentRun).where(DeploymentRun.verified_build_id == second_build_id))
        second_id = second.id
        assert second.status == 'checking'  # queued automatically by application policy
        assert second.previous_release_id == first_release
        second_job = db.scalar(select(ExecutionJob.id).where(ExecutionJob.deployment_run_id == second_id))
    executor.execute(str(second_job))
    with Session() as db:
        assert db.get(DeploymentRun, second_id).status == 'succeeded'
    app.dependency_overrides[get_identity] = lambda: Identity('local-developer', 'local-organization', frozenset({'deployer'}))
    rollback = client.post(f'/api/v1/deployments/{second_id}/rollback', headers={'Idempotency-Key': 'source-test-rollback'})
    assert rollback.status_code == 202, rollback.text
    executor.execute(rollback.json()['job_id'])
    executor.execute(rollback.json()['job_id'])
    api.run_attempt = 2
    reconcile_sources(pr_id)
    reconcile_sources(pr_id)
    with Session() as db:
        runs = list(db.scalars(select(DeploymentRun)))
        assert len(runs) == 3 and all(run.status == 'succeeded' for run in runs)
        rolled = db.get(ReleaseSpecificationRecord, rollback.json()['release_id'])
        assert rolled.rollback_target_release_id == first_release
        assert rolled.source_sha == BASE
        assert len(list(db.scalars(select(VerifiedBuild)))) == 2
        assert len(list(db.scalars(select(DeliveryJob).where(DeliveryJob.kind == 'prepare')))) == 2
    assert len(writes) == 9


def test_pending_new_release_cannot_cross_partial_resource_conflict(client, monkeypatch):
    from test_phase7_execution import prepared_run
    first = prepared_run(client, monkeypatch)
    with Session.begin() as db:
        run = db.get(DeploymentRun, first)
        run.status = 'reconciling'
        pr_id = db.get(VerifiedBuild, run.verified_build_id).pull_request_id
    source_api(client, monkeypatch, pr_id)
    reconcile_sources(pr_id)
    with Session() as db:
        build = db.scalar(select(VerifiedBuild).where(VerifiedBuild.source_sha == SOURCE))
        job_id = db.scalar(select(DeliveryJob.id).where(DeliveryJob.kind == 'prepare', DeliveryJob.verified_build_id == build.id))
    jobs.execute(str(job_id))
    with Session() as db:
        assert db.get(DeliveryJob, job_id).error_code == 'deployment_slot_busy'
        assert len(list(db.scalars(select(DeploymentRun)))) == 1


def test_concurrent_notifications_admit_one_build(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    pr_id, _, _ = source_api(client, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(reconcile_sources, pr_id) for _ in range(2)]
        for result in results:
            result.result(timeout=10)
    with Session() as db:
        assert len(list(db.scalars(select(VerifiedBuild)))) == 2
        assert len(list(db.scalars(select(DeliveryJob)))) == 1


def test_newer_commit_overtakes_older_preparation(client, monkeypatch):
    pr_id, api, _ = source_api(client, monkeypatch)
    reconcile_sources(pr_id)
    with Session() as db:
        older_job = db.scalar(select(DeliveryJob.id))
    api.head = api.artifact_sha = 'f' * 40
    api.run_id = 21
    reconcile_sources(pr_id)
    async def reject(*_): pytest.fail('superseded source must not reach admission')
    monkeypatch.setattr('app.deployments.preparation.call_kubernetes', reject)
    jobs.execute(str(older_job))
    with Session() as db:
        assert db.get(DeliveryJob, older_job).status == 'blocked'
        assert db.get(DeliveryJob, older_job).error_code == 'release_source_stale'
        assert not list(db.scalars(select(ReleaseSpecificationRecord)))
        assert len(list(db.scalars(select(VerifiedBuild)))) == 3


def test_policy_revocation_before_apply_prevents_mutation(client, monkeypatch):
    from test_phase7_execution import opted_in_job, prepared_run

    from app.deployments import execution as executor
    from app.persistence.models import DeploymentPolicy
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    with Session.begin() as db:
        db.scalar(select(DeploymentPolicy)).enabled = False
    monkeypatch.setattr(executor, 'apply_owned', lambda *_: pytest.fail('revoked policy must not mutate'))
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).status == 'failed'
        assert db.get(DeploymentRun, run_id).error_code == 'deployment_not_authorized'


def test_bounded_source_policy_gates_queue_and_rechecks_before_apply(client, monkeypatch):
    from test_phase7_execution import opted_in_job, prepared_run

    from app.deployments import execution as executor
    from app.persistence.models import DeploymentPolicy
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    with Session.begin() as db:
        db.scalar(select(DeploymentPolicy)).allowed_source_sha = 'f' * 40
    monkeypatch.setattr(executor, 'apply_owned', lambda *_: pytest.fail('unapproved source must not mutate'))
    executor.execute(str(job_id))
    with Session() as db:
        assert db.get(DeploymentRun, run_id).error_code == 'source_not_authorized_by_policy'


def test_reviewed_migration_preserves_rows_and_backfills_source(client, monkeypatch):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from test_phase7_execution import prepared_run

    from app.persistence.database import engine
    run_id = prepared_run(client, monkeypatch)
    spec = importlib.util.spec_from_file_location('source_migration', 'migrations/versions/c3a71d9e4208_phase7_source_provenance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        # Reconstruct the preceding schema only inside this disposable DB
        # transaction, then exercise the actual upgrade with existing rows.
        tables = {
            'deployment_policies': ['allowed_source_sha'],
            'infrastructure_pull_requests': ['source_head_sha', 'source_status', 'source_error_code'],
            'verified_builds': ['source_sha', 'artifact_id', 'compatibility_json'],
            'release_specifications': ['source_sha'],
        }
        counts = {table: connection.scalar(text(f'SELECT count(*) FROM {table}')) for table in tables}
        for table, columns in tables.items():
            for column in columns:
                connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        for table, count in counts.items():
            assert connection.scalar(text(f'SELECT count(*) FROM {table}')) == count
        assert connection.scalar(text('SELECT source_sha = merged_sha FROM verified_builds')) is True
        assert connection.scalar(text('SELECT source_sha = merged_sha FROM release_specifications')) is True
        assert connection.scalar(text('SELECT status FROM deployment_runs WHERE id=:id'), {'id': run_id}) == 'waiting_for_executor'


@pytest.mark.parametrize('expression', ['get marker() { state.changed = true; return "two"; }', 'marker: new HiddenDependency()', 'marker: hiddenRuntimeObject'])
def test_response_payload_cannot_hide_new_executable_requirements(client, monkeypatch, expression):
    pr_id, api, _ = source_api(client, monkeypatch)
    api.after = api.before.replace('release: RELEASE', expression)
    reconcile_sources(pr_id)
    with Session() as db:
        assert db.get(InfrastructurePullRequest, pr_id).source_error_code == 'deployment_requirements_need_review'
