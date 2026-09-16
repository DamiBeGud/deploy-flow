import httpx
import pytest

from app.config import Settings
from app.repository.errors import GitHubTokenRequestError, RepositoryAccessError
from app.repository.github import GitHubClient


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        return next(self.responses)


def response(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://github.test"))


def client():
    return GitHubClient(Settings(github_app_id="1", github_installation_id=2))


def test_token_request_retries_422_and_returns_token(monkeypatch):
    sleeps = []
    monkeypatch.setattr("app.repository.github.time.sleep", sleeps.append)
    fake = FakeClient(
        [
            response(422, {"message": "Installation is being updated"}),
            response(422, {"message": "Installation is being updated"}),
            response(201, {"token": "secret-token"}),
        ]
    )
    assert client()._installation_token(fake, "jwt", "DamiBeGud", "fixture") == "secret-token"
    assert fake.calls == 3
    assert sleeps == [0.5, 1.0]


def test_token_request_exhaustion_is_retryable_and_sanitized(monkeypatch):
    monkeypatch.setattr("app.repository.github.time.sleep", lambda _: None)
    fake = FakeClient(
        [response(422, {"message": "invalid repository selection", "token": "do-not-log"})] * 3
    )
    with pytest.raises(GitHubTokenRequestError, match="invalid repository selection") as error:
        client()._installation_token(fake, "jwt", "owner", "fixture")
    assert "do-not-log" not in str(error.value)
    assert error.value.code == "github_token_request_failed"
    assert error.value.retryable


def test_token_request_permanent_access_error_keeps_reason():
    fake = FakeClient([response(403, {"message": "Resource not accessible by integration"})])
    with pytest.raises(
        RepositoryAccessError, match="Resource not accessible by integration"
    ) as error:
        client()._installation_token(fake, "jwt", "owner", "fixture")
    assert error.value.code == "repository_access_denied"
    assert not error.value.retryable


def test_detail_is_bounded_and_control_characters_are_removed():
    long_message = "bad\n" + "x" * 500
    detail = GitHubClient._response_detail(response(422, {"message": long_message}), "fallback")
    assert "\n" not in detail
    assert len(detail) <= 240
