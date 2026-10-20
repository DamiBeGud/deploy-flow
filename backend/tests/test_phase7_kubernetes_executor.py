"""The narrow Kubernetes writer refuses foreign or changed resources."""

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from kubernetes.client.exceptions import ApiException
from urllib3.response import HTTPResponse

from app.kubernetes import executor
from app.kubernetes.client import ClusterError


@contextmanager
def fake_client():
    yield object()


def configmap() -> dict:
    return {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "sample-config", "namespace": "deployflow-test"},
        "data": {"MODE": "production"},
    }


def existing(*, owned=True, uid="owned-uid"):
    return SimpleNamespace(metadata=SimpleNamespace(
        labels={
            "app.kubernetes.io/managed-by": "deployflow" if owned else "another-system",
            "app.kubernetes.io/instance": "application-id",
        },
        uid=uid, resource_version="17", generation=2, deletion_timestamp=None,
    ))


def install_fake(monkeypatch, instance):
    monkeypatch.setattr(executor, "_api_client", fake_client)
    monkeypatch.setitem(executor._METHODS, "ConfigMap", (
        lambda _: instance, "read", "create", "patch",
    ))


def test_foreign_resource_is_never_patched(monkeypatch):
    calls = []
    instance = SimpleNamespace(
        read=lambda *_args, **_kwargs: existing(owned=False),
        create=lambda *_args, **_kwargs: calls.append("create"),
        patch=lambda *_args, **_kwargs: calls.append("patch"),
    )
    install_fake(monkeypatch, instance)
    with pytest.raises(ClusterError, match="resource_ownership_conflict"):
        executor.apply_owned(configmap(), "application-id")
    assert calls == []


def test_owned_resource_uses_nonforced_apply_with_uid_fence(monkeypatch):
    calls = []

    def patch(name, namespace, body, **kwargs):
        calls.append((name, namespace, body, kwargs))
        return existing()

    instance = SimpleNamespace(
        read=lambda *_args, **_kwargs: existing(),
        create=lambda *_args, **_kwargs: pytest.fail("must not create"),
        patch=patch,
    )
    install_fake(monkeypatch, instance)
    result = executor.apply_owned(configmap(), "application-id", "owned-uid")
    assert result["uid"] == "owned-uid"
    assert calls[0][2]["metadata"]["uid"] == "owned-uid"
    assert calls[0][2]["metadata"]["resourceVersion"] == "17"
    assert calls[0][3]["force"] is False
    assert calls[0][3]["field_manager"] == "deployflow-executor"
    assert calls[0][3]["_content_type"] == "application/apply-patch+yaml"


def test_disappeared_journaled_resource_is_not_recreated(monkeypatch):
    calls = []

    def missing(*_args, **_kwargs):
        raise ApiException(status=404)

    instance = SimpleNamespace(
        read=missing,
        create=lambda *_args, **_kwargs: calls.append("create"),
        patch=lambda *_args, **_kwargs: calls.append("patch"),
    )
    install_fake(monkeypatch, instance)
    with pytest.raises(ClusterError, match="resource_disappeared"):
        executor.apply_owned(configmap(), "application-id", "old-uid")
    assert calls == []


def test_cleanup_deletes_only_owned_uid_with_precondition(monkeypatch):
    calls = []
    instance = SimpleNamespace(
        read=lambda *_args, **_kwargs: existing(),
        delete=lambda name, namespace, **kwargs: calls.append((name, namespace, kwargs)),
    )
    monkeypatch.setattr(executor, "_api_client", fake_client)
    monkeypatch.setitem(executor._DELETE_METHODS, "ConfigMap", (
        lambda _: instance, "read", "delete",
    ))
    assert executor.delete_owned("ConfigMap", "sample-config", "application-id", "owned-uid") is False
    assert calls[0][2]["body"].preconditions.uid == "owned-uid"
    assert calls[0][0:2] == ("sample-config", "deployflow-test")


def test_cleanup_refuses_foreign_or_replaced_resource(monkeypatch):
    calls = []
    instance = SimpleNamespace(
        read=lambda *_args, **_kwargs: existing(owned=False),
        delete=lambda *_args, **_kwargs: calls.append("delete"),
    )
    monkeypatch.setattr(executor, "_api_client", fake_client)
    monkeypatch.setitem(executor._DELETE_METHODS, "ConfigMap", (
        lambda _: instance, "read", "delete",
    ))
    with pytest.raises(ClusterError, match="resource_ownership_conflict"):
        executor.delete_owned("ConfigMap", "sample-config", "application-id", None)
    instance.read = lambda *_args, **_kwargs: existing(uid="replacement")
    with pytest.raises(ClusterError, match="resource_identity_changed"):
        executor.delete_owned("ConfigMap", "sample-config", "application-id", "old-uid")
    assert calls == []


def test_internal_health_uses_dedicated_checker_and_fences_service(monkeypatch):
    calls = []
    service = existing()
    service.spec = SimpleNamespace(ports=[SimpleNamespace(port=4587)])

    def proxy(name, namespace, path, **kwargs):
        assert kwargs["_preload_content"] is False
        calls.append((name, namespace, path))
        return HTTPResponse(body=BytesIO(
            b'{"status":"passed","service":"sample-config","path":"/healthz"}'
        ), preload_content=False)

    instance = SimpleNamespace(
        read_namespaced_service=lambda *_args, **_kwargs: service,
        connect_get_namespaced_service_proxy_with_path=proxy,
    )
    monkeypatch.setattr(executor, "_api_client", fake_client)
    monkeypatch.setattr(executor.client, "CoreV1Api", lambda _: instance)
    result = executor.service_health("sample-config", "/healthz", "application-id", "owned-uid", 4587)
    assert result["status"] == "passed"
    assert calls == [("deployflow-health-checker:http", "deployflow-test", "probe/sample-config/4587/healthz")]
    with pytest.raises(ClusterError, match="resource_identity_changed"):
        executor.service_health("sample-config", "/healthz", "application-id", "other-uid", 4587)


@pytest.mark.parametrize("payload", [
    b"{'status': 'passed'}",
    b" " * 4097,
    b'{"status":"passed","service":"other-service","path":"/healthz"}',
])
def test_internal_health_rejects_invalid_raw_response_and_closes_connection(monkeypatch, payload):
    service = existing()
    service.spec = SimpleNamespace(ports=[SimpleNamespace(port=4587)])
    response = HTTPResponse(body=BytesIO(payload), preload_content=False)
    instance = SimpleNamespace(
        read_namespaced_service=lambda *_args, **_kwargs: service,
        connect_get_namespaced_service_proxy_with_path=lambda *_args, **_kwargs: response,
    )
    monkeypatch.setattr(executor, "_api_client", fake_client)
    monkeypatch.setattr(executor.client, "CoreV1Api", lambda _: instance)
    with pytest.raises(ClusterError, match="health_checker_invalid"):
        executor.service_health("sample-config", "/healthz", "application-id", "owned-uid", 4587)
    assert response.closed


@pytest.mark.parametrize("manifest", ["phase7-deployer-access.yaml", "phase7-stage2-proposal.yaml"])
def test_checker_proxy_rbac_matches_named_service_port(manifest):
    ops = Path(__file__).resolve().parents[1] / "ops" / "k3s"
    checker = next(
        item for item in yaml.safe_load_all((ops / "phase7-health-checker.yaml").read_text())
        if item["kind"] == "Service"
    )
    assert checker["metadata"]["namespace"] == "deployflow-test"
    assert checker["spec"]["ports"][0]["name"] == "http"
    role = next(
        item for item in yaml.safe_load_all((ops / manifest).read_text())
        if item["kind"] == "Role"
    )
    proxy_rules = [rule for rule in role["rules"] if "services/proxy" in rule["resources"]]
    assert proxy_rules == [{
        "apiGroups": [""], "resources": ["services/proxy"],
        "resourceNames": [f"{checker['metadata']['name']}:http"], "verbs": ["get"],
    }]
