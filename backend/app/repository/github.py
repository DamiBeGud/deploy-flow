import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt

from app.config import Settings
from app.repository.errors import (
    RepositoryAccessError,
    RepositoryConfigurationError,
    RepositoryRevisionError,
    TransientRepositoryError,
)


@dataclass(frozen=True)
class ResolvedRepository:
    owner: str
    name: str
    url: str
    revision: str
    commit_sha: str
    repository_id: int
    archive_url: str
    token: str


class GitHubClient:
    def __init__(self, config: Settings):
        self.config = config

    def _app_jwt(self) -> str:
        key_path = self.config.github_private_key_path
        if not self.config.github_app_id or not self.config.github_installation_id or not key_path:
            raise RepositoryConfigurationError()
        try:
            key = Path(key_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise RepositoryConfigurationError() from exc
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.config.github_app_id}, key, algorithm="RS256")

    def _headers(self, token: str) -> dict[str, str]:
        return {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": self.config.github_api_version}

    def resolve(self, owner: str, name: str, revision: str) -> ResolvedRepository:
        jwt_token = self._app_jwt()
        base = self.config.github_api_url.rstrip("/")
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                token_response = client.post(
                    f"{base}/app/installations/{self.config.github_installation_id}/access_tokens",
                    headers=self._headers(jwt_token), json={"repositories": [name], "permissions": {"contents": "read"}},
                )
                if token_response.status_code in {401, 403, 404}:
                    raise RepositoryAccessError()
                token_response.raise_for_status()
                token = token_response.json()["token"]
                repo = client.get(f"{base}/repos/{quote(owner, safe='')}/{quote(name, safe='')}", headers=self._headers(token))
                if repo.status_code in {401, 403, 404}:
                    raise RepositoryAccessError()
                repo.raise_for_status()
                commit = client.get(f"{base}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/commits/{quote(revision, safe='')}", headers=self._headers(token))
                if commit.status_code == 404:
                    raise RepositoryRevisionError()
                if commit.status_code in {401, 403}:
                    raise RepositoryAccessError()
                commit.raise_for_status()
                archive = client.get(f"{base}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/tarball/{commit.json()['sha']}", headers=self._headers(token))
                if archive.status_code not in {301, 302, 303, 307, 308}:
                    archive.raise_for_status()
                    raise RepositoryAccessError()
                archive_url = archive.headers.get("location")
                if not archive_url or not archive_url.startswith("https://codeload.github.com/"):
                    raise RepositoryAccessError()
                payload = repo.json()
                return ResolvedRepository(owner, name, payload["html_url"], revision, commit.json()["sha"], payload["id"], archive_url, token)
        except (RepositoryAccessError, RepositoryRevisionError):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise TransientRepositoryError() from exc
            raise RepositoryAccessError() from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise TransientRepositoryError() from exc

    def download(self, resolved: ResolvedRepository):
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                with client.stream("GET", resolved.archive_url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        yield chunk
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise TransientRepositoryError() from exc
            raise RepositoryAccessError() from exc
        except httpx.HTTPError as exc:
            raise TransientRepositoryError() from exc
