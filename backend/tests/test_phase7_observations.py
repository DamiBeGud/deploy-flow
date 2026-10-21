from types import SimpleNamespace

import pytest
from test_phase7_execution import opted_in_job, prepared_run

from app.deployments import execution as executor
from app.deployments.observations import application_observation
from app.kubernetes.client import ClusterError
from app.persistence.database import Session
from app.persistence.models import DeploymentRun


def snapshot(**changes):
    row = dict(kind='Deployment', name='fixture', uid='uid', application_id='app', managed=True,
               generation=1, observed_generation=1, image='image', desired_replicas=1,
               updated_replicas=1, ready_replicas=1, available_replicas=1, conditions=[])
    row.update(changes)
    return dict(workloads=[row], observed_at='2026-10-20T21:00:00Z', attempted_at='2026-10-20T21:00:00Z')


def observation(data):
    return application_observation(data, application_id='app', resource=SimpleNamespace(name='fixture', uid='uid', generation=1), release=SimpleNamespace(image_reference='image'))


def test_current_observation_preserves_zero_and_detects_missing_and_drift():
    assert observation(snapshot(ready_replicas=0, available_replicas=0))['status'] == 'pending'
    assert observation(snapshot())['status'] == 'ready'
    zero = observation(snapshot(desired_replicas=0, ready_replicas=0, available_replicas=0, updated_replicas=0))
    assert zero['desired_replicas'] == zero['ready_replicas'] == 0
    assert observation({**snapshot(), 'workloads': []})['status'] == 'missing'
    for change in ({'uid': 'other'}, {'application_id': 'other'}, {'image': 'other'}, {'generation': 2}):
        assert observation(snapshot(**change))['status'] == 'drifted'
    assert observation(snapshot(available_replicas=None))['status'] == 'unavailable'
    assert observation({'status': 'unavailable', 'code': 'cluster_access_expired', 'observed_at': None}) == {'status': 'unavailable', 'code': 'cluster_access_expired', 'observed_at': None}


def test_observation_failure_is_persisted_without_false_zero_or_health(client, monkeypatch):
    run_id = prepared_run(client, monkeypatch)
    job_id = opted_in_job(run_id, monkeypatch)
    monkeypatch.setattr(executor, 'apply_owned', lambda item, *_: {'uid': f"uid-{item['kind']}", 'generation': 1})
    def unavailable(*_): raise ClusterError('cluster_access_expired')
    monkeypatch.setattr(executor, 'rollout_status', unavailable)
    monkeypatch.setattr(executor, 'service_health', lambda *_: pytest.fail('health must not run'))
    executor.execute(str(job_id))
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        assert run.rollout_json['status'] == 'unavailable'
        assert run.rollout_json['observed_at']
        assert 'ready_replicas' not in run.rollout_json
        assert run.health_json is None


def test_service_absence_and_identity_are_visible():
    resource = SimpleNamespace(name='fixture', uid='uid', generation=1)
    release = SimpleNamespace(image_reference='image')
    service = SimpleNamespace(name='fixture', uid='service-uid')
    absent = application_observation(snapshot(), application_id='app', resource=resource, release=release, service=service)
    assert absent['status'] == 'missing' and absent['code'] == 'service_absent'
    foreign = application_observation({**snapshot(), 'services': [{'name': 'fixture', 'uid': 'foreign', 'application_id': 'app'}]}, application_id='app', resource=resource, release=release, service=service)
    assert foreign['status'] == 'drifted'
