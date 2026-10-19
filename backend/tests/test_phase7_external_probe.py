"""The isolated external client checks only reviewed application hosts."""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from app import external_probe


def test_external_probe_rejects_unapproved_hosts(monkeypatch):
    with TestClient(external_probe.app) as client:
        for host in ("127.0.0.1", "metadata.google.internal", "fixture.deployflow.local.evil.test"):
            response = client.post("/probe", json={"hostname": host, "path": "/healthz"})
            assert response.status_code == 400
        assert client.post("/probe", json={
            "hostname": "fixture.deployflow.local", "path": "/../admin",
        }).status_code == 400


def test_external_probe_reports_http_result_without_returning_body(monkeypatch):
    seen = []

    @contextmanager
    def probe_stream(method, url):
        seen.append((method, url))
        yield type("Response", (), {"status_code": 200, "text": "private content"})()

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        @staticmethod
        def stream(method, url):
            return probe_stream(method, url)

    with TestClient(external_probe.app) as client:
        monkeypatch.setattr(external_probe.httpx, "Client", FakeClient)
        response = client.post("/probe", json={
            "hostname": "fixture.deployflow.local", "path": "/", "tls": True,
        })
    assert response.status_code == 200
    assert response.json() == {
        "status": "passed", "hostname": "fixture.deployflow.local", "path": "/",
        "scheme": "https",
    }
    assert seen == [("GET", "https://fixture.deployflow.local/")]
