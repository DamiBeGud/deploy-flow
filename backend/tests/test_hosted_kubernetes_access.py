"""Projected identities rotate without SSH, copied credentials, or kubeconfig fallback."""

import pytest
from kubernetes.config.config_exception import ConfigException
from kubernetes.config.incluster_config import InClusterConfigLoader

from app.kubernetes import client as cluster


@pytest.fixture
def access_environment(monkeypatch):
    for name in ("DF_KUBE_API_SERVER", "DF_KUBE_CA_PATH", "DF_KUBE_TOKEN_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DF_KUBE_ACCESS_MODE", "incluster")


def test_projected_token_rotation_is_read_on_next_operation(tmp_path, monkeypatch, access_environment):
    token, ca = tmp_path / "token", tmp_path / "ca.crt"
    token.write_text("synthetic-first-token")
    ca.write_text("synthetic-ca")
    configurations, closed = [], []

    def load(*, client_configuration, try_refresh_token):
        assert try_refresh_token is True
        InClusterConfigLoader(
            token_filename=str(token), cert_filename=str(ca),
            environ={"KUBERNETES_SERVICE_HOST": "10.43.0.1", "KUBERNETES_SERVICE_PORT": "443"},
        ).load_and_set(client_configuration)

    class Api:
        def __init__(self, configuration):
            configurations.append(configuration)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cluster.kube_config, "load_incluster_config", load)
    monkeypatch.setattr(cluster.client, "ApiClient", Api)
    with cluster._api_client():
        assert configurations[-1].api_key["BearerToken"] == "bearer synthetic-first-token"
    replacement = tmp_path / "replacement"
    replacement.write_text("synthetic-rotated-token")
    replacement.replace(token)
    with cluster._api_client():
        assert configurations[-1].api_key["BearerToken"] == "bearer synthetic-rotated-token"
    assert all(config.host == "https://10.43.0.1:443" for config in configurations)
    assert all(config.verify_ssl and config.ssl_ca_cert == str(ca) and config.retries == 0 for config in configurations)
    assert closed == [True, True]
    assert cluster.NAMESPACE == "deployflow-test"


def test_missing_pod_identity_fails_closed(monkeypatch, access_environment):
    def missing(**_kwargs):
        raise ConfigException("No projected identity")

    monkeypatch.setattr(cluster.kube_config, "load_incluster_config", missing)
    monkeypatch.setattr(cluster.kube_config, "load_kube_config", lambda **_kwargs: pytest.fail("No admin fallback"))
    monkeypatch.setattr(cluster.client, "ApiClient", lambda *_: pytest.fail("No API request"))
    with pytest.raises(cluster.ClusterError, match="cluster_access_unconfigured"):
        with cluster._api_client():
            pytest.fail("Missing identity accepted")


@pytest.mark.parametrize("override", ["DF_KUBE_API_SERVER", "DF_KUBE_CA_PATH", "DF_KUBE_TOKEN_PATH"])
def test_incluster_mode_rejects_mixed_host_credentials(monkeypatch, access_environment, override):
    monkeypatch.setenv(override, "unexpected-host-setting")
    monkeypatch.setattr(cluster.kube_config, "load_incluster_config", lambda **_: pytest.fail("Ambiguous configuration"))
    with pytest.raises(cluster.ClusterError, match="cluster_access_unconfigured"):
        with cluster._api_client():
            pytest.fail("Mixed identity accepted")


@pytest.mark.parametrize("mode,endpoint", [("unknown", "https://127.0.0.1:16443"), ("ssh", "https://outside.example:6443")])
def test_unknown_mode_and_remote_ssh_endpoint_are_denied(monkeypatch, tmp_path, mode, endpoint):
    token, ca = tmp_path / "token", tmp_path / "ca"
    token.write_text("synthetic-token")
    ca.write_text("synthetic-ca")
    monkeypatch.setenv("DF_KUBE_ACCESS_MODE", mode)
    monkeypatch.setenv("DF_KUBE_API_SERVER", endpoint)
    monkeypatch.setenv("DF_KUBE_CA_PATH", str(ca))
    monkeypatch.setenv("DF_KUBE_TOKEN_PATH", str(token))
    with pytest.raises(cluster.ClusterError, match="cluster_access_unconfigured"):
        with cluster._api_client():
            pytest.fail("Unbounded endpoint accepted")


def test_default_ssh_mode_preserves_private_file_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("DF_KUBE_ACCESS_MODE", raising=False)
    monkeypatch.delenv("DF_KUBE_API_SERVER", raising=False)
    token, ca = tmp_path / "token", tmp_path / "ca"
    token.write_text("synthetic-ssh-token\n")
    ca.write_text("synthetic-ca")
    monkeypatch.setenv("DF_KUBE_CA_PATH", str(ca))
    monkeypatch.setenv("DF_KUBE_TOKEN_PATH", str(token))
    with cluster._api_client() as api:
        assert api.configuration.host == "https://127.0.0.1:16443"
        assert api.configuration.api_key["BearerToken"] == "Bearer synthetic-ssh-token"
