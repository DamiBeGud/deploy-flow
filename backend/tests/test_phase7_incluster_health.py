"""The dedicated checker is namespace-bound and returns no application body."""

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import incluster_health


def test_checker_rejects_unsafe_targets():
    with TestClient(incluster_health.app) as client:
        assert client.get("/ready").json() == {"status": "ready"}
        assert client.get("/probe/fixture/0/healthz").status_code == 400
    with pytest.raises(HTTPException) as error:
        incluster_health.probe("fixture", 4587, "../admin")
    assert error.value.status_code == 400


def test_checker_uses_cluster_local_service_name(monkeypatch):
    seen = []

    @contextmanager
    def probe_stream(method, url):
        seen.append((method, url))
        yield type("Response", (), {"status_code": 503, "text": "private content"})()

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

    with TestClient(incluster_health.app) as client:
        monkeypatch.setattr(incluster_health.httpx, "Client", FakeClient)
        result = client.get("/probe/fixture/4587/healthz")
        root_result = client.get("/probe/fixture/4587/")
    assert result.status_code == 200
    assert result.json() == {
        "status": "pending", "service": "fixture", "path": "/healthz",
        "code": "application_unhealthy",
    }
    assert root_result.json()["path"] == "/"
    assert seen == [
        ("GET", "http://fixture.deployflow-test.svc.cluster.local:4587/healthz"),
        ("GET", "http://fixture.deployflow-test.svc.cluster.local:4587/"),
    ]
