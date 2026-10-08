import asyncio
import copy
import hashlib
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase4 import application, completed_v2_run, save_intent

from app.bootstrap.generator import render
from app.kubernetes.client import ClusterError, list_observations, validate_dry_run
from app.kubernetes.gateway import GatewayError, call_kubernetes
from app.kubernetes.policy import ManifestPolicyError, checked_manifests
from app.persistence.database import Session
from app.persistence.models import InfrastructurePlan


def generated():
    assessment = {
        "schema_version": "infrastructure-assessment-v2",
        "completion": "complete",
        "repository": {"commit_sha": "a" * 40},
        "review": {"runtime": "nodejs"},
        "application": {"name": "fixture"},
    }
    intent = {
        "connectivity": {
            "port": 8080, "health_path": "/healthz", "replicas": 1,
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "512Mi"}},
            "environment": {}, "exposure": {"kind": "internal", "path": "/"},
        },
        "build": {"branch": "main", "image": "ghcr.io/example/fixture", "install_command": ["npm", "ci"], "test_command": ["npm", "test"], "build_command": None, "start_command": ["npm", "start"]},
    }
    profile = {
        "namespace": "deployflow-test", "registry": "ghcr.io", "application_id": "11111111-1111-1111-1111-111111111111",
        "ingress_class": "nginx", "hostname_suffix": ".deployflow.local", "max_replicas": 3,
        "allowed_secret_names": [], "node_base_image": "node:20-bookworm-slim",
        "checkout_action": "actions/checkout@v4", "setup_node_action": "actions/setup-node@v4",
    }
    return render(assessment, intent, profile), profile


def test_generated_manifests_pass_policy_and_disable_service_account_mount():
    bundle, profile = generated()
    result = checked_manifests(bundle.files, digest=bundle.digest, namespace=profile["namespace"], application_id=profile["application_id"], registry=profile["registry"])
    deployment = next(item for item in result if item["kind"] == "Deployment")
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


@pytest.mark.parametrize("change,code", [
    ("namespace", "manifest_identity_denied"),
    ("secret", "manifest_paths_denied"),
    ("digest", "bundle_digest_mismatch"),
    ("token", "deployment_policy_denied"),
])
def test_policy_rejects_unsafe_bundle(change, code):
    bundle, profile = generated()
    files = copy.deepcopy(bundle.files)
    digest = bundle.digest
    if change == "namespace":
        files["deploy/kubernetes/deployment.yaml"] = files["deploy/kubernetes/deployment.yaml"].replace("namespace: deployflow-test", "namespace: default")
    elif change == "secret":
        files["deploy/kubernetes/secret.yaml"] = "apiVersion: v1\nkind: Secret\n"
    elif change == "digest":
        digest = "0" * 64
    else:
        files["deploy/kubernetes/deployment.yaml"] = files["deploy/kubernetes/deployment.yaml"].replace("automountServiceAccountToken: false", "automountServiceAccountToken: true")
    if change != "digest":
        from app.bootstrap.generator import canonical_digest
        digest = canonical_digest(files)
    with pytest.raises(ManifestPolicyError, match=code):
        checked_manifests(files, digest=digest, namespace=profile["namespace"], application_id=profile["application_id"], registry=profile["registry"])


@pytest.mark.parametrize("enforce,version,expected", [
    ("", "v1.36", "pod_security_enforce"),
    ("baseline", "v1.36", "pod_security_enforce"),
    ("restricted", "", "pod_security_version"),
    ("restricted", "v1.35", "pod_security_version"),
])
def test_readiness_script_rejects_pod_security_label_drift(tmp_path, enforce, version, expected):
    fake = tmp_path / "ssh"
    fake.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *hostname) echo homeserver1;;\n"
        "  *version*) echo '\"gitVersion\": \"v1.36.3+k3s1\"';;\n"
        "  *'jsonpath='*'enforce-version'*) echo '" + version + "';;\n"
        "  *'jsonpath='*'enforce'*) echo '" + enforce + "';;\n"
        "  *'get namespace deployflow-test -o name'*) echo namespace/deployflow-test;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", str(root / "scripts/check_k3s_readiness.sh")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "DF_K3S_SSH_TARGET": "user@example.test"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert expected in result.stderr


def test_cluster_validation_is_persisted_without_applying_resources(client, monkeypatch):
    app_id = application(client)
    run_id = completed_v2_run(client, app_id)
    saved = save_intent(client, app_id, run_id)
    created = client.post(
        f"/api/v1/applications/{app_id}/infrastructure-plans",
        headers={"Idempotency-Key": "phase5-plan"},
        json={"analysis_run_id": run_id, "expected_assessment_version": 1, "intent_version": saved["version"], "environment_profile_id": "local-k3s"},
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    calls = []

    async def fake_gateway(role, tool, arguments):
        calls.append((role, tool, arguments))
        return {"status": "passed", "checks": [{"kind": "Deployment", "name": "fixture", "code": "admission_passed"}], "observed_at": "2026-10-07T10:00:00+00:00"}

    monkeypatch.setattr("app.api.routes.call_kubernetes", fake_gateway)
    response = client.post(
        f"/api/v1/infrastructure-plans/{plan['id']}/validations",
        headers={"Idempotency-Key": "phase5-validation"},
        json={"expected_digest": plan["bundle_digest"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "passed"
    assert calls[0][0:2] == ("validator", "kube.validate_manifests")
    assert client.get(f"/api/v1/infrastructure-plans/{plan['id']}").json()["cluster_validation"]["status"] == "passed"
    duplicate = client.post(
        f"/api/v1/infrastructure-plans/{plan['id']}/validations",
        headers={"Idempotency-Key": "phase5-validation"},
        json={"expected_digest": plan["bundle_digest"]},
    )
    assert duplicate.json() == response.json()

    with Session.begin() as db:
        stored = db.get(InfrastructurePlan, plan["id"])
        stored.environment_profile_hash = hashlib.sha256(b"old policy").hexdigest()
    stale = client.get(f"/api/v1/infrastructure-plans/{plan['id']}").json()
    assert stale["status"] == "stale"
    assert stale["cluster_validation"] is None
    denied = client.post(
        f"/api/v1/infrastructure-plans/{plan['id']}/approve",
        headers={"Idempotency-Key": "phase5-approve-stale"},
        json={"expected_digest": plan["bundle_digest"]},
    )
    assert denied.status_code == 409


def test_validator_forces_dry_run_and_rejects_foreign_ownership(monkeypatch):
    from kubernetes.client.exceptions import ApiException

    calls = []

    class FakeApi:
        pass

    class FakeClient:
        def __init__(self, api):
            pass

        def read_namespaced_config_map(self, name, namespace, **kwargs):
            raise ApiException(status=404)

        def patch_namespaced_config_map(self, name, namespace, body, **kwargs):
            calls.append((name, namespace, kwargs))

    @contextmanager
    def fake_api():
        yield FakeApi()

    monkeypatch.setattr("app.kubernetes.client._api_client", fake_api)
    monkeypatch.setattr("app.kubernetes.client.client.CoreV1Api", FakeClient)
    monkeypatch.setattr("app.kubernetes.client.client.AppsV1Api", FakeClient)
    monkeypatch.setattr("app.kubernetes.client.client.NetworkingV1Api", FakeClient)
    item = {"kind": "ConfigMap", "metadata": {"name": "fixture-config"}}
    result = validate_dry_run([item], "11111111-1111-1111-1111-111111111111")
    assert result["status"] == "passed"
    assert calls[0][1] == "deployflow-test"
    assert calls[0][2]["dry_run"] == "All"
    assert calls[0][2]["field_validation"] == "Strict"
    assert calls[0][2]["_content_type"] == "application/apply-patch+yaml"

    def foreign(self, name, namespace, **kwargs):
        return SimpleNamespace(metadata=SimpleNamespace(labels={"app.kubernetes.io/instance": "someone-else"}))

    monkeypatch.setattr(FakeClient, "read_namespaced_config_map", foreign)
    with pytest.raises(ClusterError, match="resource_ownership_conflict"):
        validate_dry_run([item], "11111111-1111-1111-1111-111111111111")
    assert len(calls) == 1


def test_services_api_is_scoped_and_reports_unavailable(client, monkeypatch):
    from app.kubernetes.gateway import GatewayError

    async def unavailable(role, tool, arguments):
        raise GatewayError("cluster_unavailable")

    monkeypatch.setattr("app.api.routes.call_kubernetes", unavailable)
    assert client.get("/api/v1/services?namespace=default").status_code == 404
    response = client.get("/api/v1/services?namespace=deployflow-test")
    assert response.status_code == 503
    assert response.json()["detail"] == "cluster_unavailable"


def test_observations_report_replicas_and_associate_services(monkeypatch):
    @contextmanager
    def fake_api():
        yield SimpleNamespace(sanitize_for_serialization=lambda item: item)

    def reply(items):
        return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None))

    class Apps:
        def __init__(self, api):
            pass

        def list_namespaced_deployment(self, namespace, **kwargs):
            assert namespace == "deployflow-test"
            return reply([{
                "metadata": {"name": "fixture", "labels": {"app.kubernetes.io/managed-by": "deployflow"}},
                "spec": {"replicas": 2, "template": {"metadata": {"labels": {"app": "fixture"}}, "spec": {"containers": [{"image": "ghcr.io/example/fixture:tag"}]}}},
                "status": {"readyReplicas": 1, "conditions": [{"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"}]},
            }])

        def list_namespaced_stateful_set(self, namespace, **kwargs):
            return reply([])

    class Core:
        def __init__(self, api):
            pass

        def list_namespaced_service(self, namespace, **kwargs):
            return reply([{"metadata": {"name": "fixture"}, "spec": {"type": "ClusterIP", "selector": {"app": "fixture"}, "clusterIP": "10.0.0.1", "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]}}])

    class Batch:
        def __init__(self, api):
            pass

        def list_namespaced_job(self, namespace, **kwargs):
            return reply([])

    monkeypatch.setattr("app.kubernetes.client._api_client", fake_api)
    monkeypatch.setattr("app.kubernetes.client.client.AppsV1Api", Apps)
    monkeypatch.setattr("app.kubernetes.client.client.CoreV1Api", Core)
    monkeypatch.setattr("app.kubernetes.client.client.BatchV1Api", Batch)
    observed = list_observations()
    workload = observed["workloads"][0]
    assert (workload["desired_replicas"], workload["ready_replicas"]) == (2, 1)
    assert workload["services"] == ["fixture"]
    assert workload["managed"] is True
    assert observed["services"][0]["address"] == "10.0.0.1"


def test_reader_mcp_transport_exposes_only_reader_tools(monkeypatch):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "DF_KUBE_MCP_ROLE": "reader", "DF_KUBE_MCP_PORT": str(port)}
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.kubernetes_server"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(50):
            if process.poll() is not None:
                pytest.fail(f"reader MCP exited: {process.stderr.read()}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("reader MCP did not start")

        url = f"http://127.0.0.1:{port}/mcp"
        monkeypatch.setattr(
            "app.kubernetes.gateway.settings",
            lambda: SimpleNamespace(kube_reader_mcp_url=url, kube_validator_mcp_url=""),
        )

        async def inspect_transport():
            capabilities = await call_kubernetes("reader", "kube.get_capabilities", {})
            assert capabilities["namespaces"] == ["deployflow-test"]
            async with streamable_http_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
            assert "kube.validate_manifests" not in names
            assert "kube.list_workloads" in names
            with pytest.raises(GatewayError, match="kubernetes_tool_denied"):
                await call_kubernetes("reader", "kube.validate_manifests", {})

        asyncio.run(inspect_transport())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
