"""Repository-scoped GitHub App operations for reviewed infrastructure PRs."""

from __future__ import annotations

import base64
import re
from urllib.parse import quote, urlsplit

import httpx

from app.config import Settings
from app.repository.github import GitHubClient


class GitHubPhase6Error(RuntimeError):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


class GitHubAPI:
    """One short-lived, repository-scoped token per worker operation."""

    def __init__(self, config: Settings, owner: str, repository: str):
        self.config = config
        self.owner = owner
        self.repository = repository
        self.client: httpx.Client | None = None
        self.token: str | None = None

    def __enter__(self):
        self.client = httpx.Client(timeout=20, follow_redirects=False)
        auth = GitHubClient(self.config)
        try:
            self.token = auth._installation_token(
                self.client, auth._app_jwt(), self.owner, self.repository,
                {"contents": "write", "pull_requests": "write", "workflows": "write", "actions": "read"},
            )
        except Exception as exc:
            self.client.close()
            raise GitHubPhase6Error("github_write_auth_unavailable", transient=True) from exc
        return self

    def __exit__(self, *_):
        if self.client:
            self.client.close()

    def request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None, allow_404: bool = False) -> httpx.Response | None:
        assert self.client is not None and self.token is not None
        if not path.startswith("/") or "//" in path:
            raise GitHubPhase6Error("github_path_invalid")
        try:
            response = self.client.request(
                method, self.config.github_api_url.rstrip("/") + path,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": self.config.github_api_version},
                json=json, params=params,
            )
        except httpx.HTTPError as exc:
            raise GitHubPhase6Error("github_network_unavailable", transient=True) from exc
        if allow_404 and response.status_code == 404:
            return None
        if response.status_code in {429, 500, 502, 503, 504}:
            raise GitHubPhase6Error("github_temporarily_unavailable", transient=True)
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubPhase6Error("github_rate_limited", transient=True)
        if response.status_code >= 400:
            raise GitHubPhase6Error(f"github_http_{response.status_code}")
        return response

    @property
    def repo_path(self) -> str:
        return f"/repos/{quote(self.owner, safe='')}/{quote(self.repository, safe='')}"

    def repository_info(self) -> dict:
        return self.request("GET", self.repo_path).json()

    def branch_sha(self, branch: str) -> str:
        response = self.request("GET", f"{self.repo_path}/branches/{quote(branch, safe='')}")
        return response.json()["commit"]["sha"]

    def ref_sha(self, branch: str) -> str | None:
        response = self.request("GET", f"{self.repo_path}/git/ref/heads/{quote(branch, safe='/')}", allow_404=True)
        return response.json()["object"]["sha"] if response else None

    def file_at(self, path: str, ref: str) -> bytes | None:
        response = self.request("GET", f"{self.repo_path}/contents/{quote(path, safe='/')}", params={"ref": ref}, allow_404=True)
        if response is None:
            return None
        payload = response.json()
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise GitHubPhase6Error("github_file_unexpected")
        try:
            return base64.b64decode(payload["content"], validate=False)
        except (KeyError, ValueError) as exc:
            raise GitHubPhase6Error("github_file_unexpected") from exc

    def commit(self, sha: str) -> dict:
        return self.request("GET", f"{self.repo_path}/git/commits/{sha}").json()

    def compare_paths(self, base_sha: str, head_sha: str) -> set[str]:
        response = self.request("GET", f"{self.repo_path}/compare/{base_sha}...{head_sha}", params={"per_page": 100})
        body = response.json()
        if body.get("total_commits", 0) > 20 or len(body.get("files", [])) >= 100:
            raise GitHubPhase6Error("github_compare_too_large")
        return {row["filename"] for row in body.get("files", [])}

    def list_pull_requests(self, branch: str) -> list[dict]:
        response = self.request("GET", f"{self.repo_path}/pulls", params={"head": f"{self.owner}:{branch}", "state": "all", "per_page": 30})
        return response.json()

    def pull_request(self, number: int) -> dict:
        return self.request("GET", f"{self.repo_path}/pulls/{number}").json()

    def create_bundle_pull_request(self, *, branch: str, base_branch: str, base_sha: str, files: dict[str, str], title: str, body: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise GitHubPhase6Error("source_revision_invalid")
        matching = self.list_pull_requests(branch)
        if matching:
            if len(matching) != 1 or matching[0]["base"]["ref"] != base_branch or matching[0]["head"]["ref"] != branch:
                raise GitHubPhase6Error("github_pr_conflict")
            pull = matching[0]
            current = pull["head"]["sha"]
            branch_commit = self.commit(current)
            if [item["sha"] for item in branch_commit["parents"]] != [base_sha] or self.compare_paths(base_sha, current) != set(files):
                raise GitHubPhase6Error("github_pr_conflict")
            for path, content in files.items():
                if self.file_at(path, current) != content.encode():
                    raise GitHubPhase6Error("github_pr_conflict")
            return {"repository_id": self.repository_info()["id"], "head_sha": current, "number": pull["number"], "url": pull["html_url"]}
        for path in files:
            if self.file_at(path, base_sha) is not None:
                raise GitHubPhase6Error("managed_file_already_exists")
        current = self.ref_sha(branch)
        if current is None or current == base_sha:
            if self.branch_sha(base_branch) != base_sha:
                raise GitHubPhase6Error("source_revision_changed")
        if current is None:
            try:
                self.request("POST", f"{self.repo_path}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
            except GitHubPhase6Error as exc:
                if exc.code != "github_http_422":
                    raise
            current = self.ref_sha(branch)
        if current is None:
            raise GitHubPhase6Error("github_branch_unavailable", transient=True)
        if current == base_sha:
            base_commit = self.commit(base_sha)
            tree = self.request("POST", f"{self.repo_path}/git/trees", json={
                "base_tree": base_commit["tree"]["sha"],
                "tree": [{"path": path, "mode": "100644", "type": "blob", "content": content} for path, content in sorted(files.items())],
            }).json()
            commit = self.request("POST", f"{self.repo_path}/git/commits", json={
                "message": title, "tree": tree["sha"], "parents": [base_sha],
            }).json()
            try:
                self.request("PATCH", f"{self.repo_path}/git/refs/heads/{quote(branch, safe='/')}", json={"sha": commit["sha"], "force": False})
            except GitHubPhase6Error:
                # A lost response or another retry may have advanced the ref.
                pass
            current = self.ref_sha(branch)
        if current is None or current == base_sha:
            raise GitHubPhase6Error("github_branch_unavailable", transient=True)
        branch_commit = self.commit(current)
        parents = [item["sha"] for item in branch_commit["parents"]]
        if parents != [base_sha] or self.compare_paths(base_sha, current) != set(files):
            raise GitHubPhase6Error("github_branch_conflict")
        for path, content in files.items():
            if self.file_at(path, current) != content.encode():
                raise GitHubPhase6Error("github_branch_conflict")
        matching = self.list_pull_requests(branch)
        if matching:
            if len(matching) != 1 or matching[0]["base"]["ref"] != base_branch:
                raise GitHubPhase6Error("github_pr_conflict")
            pull = matching[0]
        else:
            if self.branch_sha(base_branch) != base_sha:
                raise GitHubPhase6Error("source_revision_changed")
            try:
                pull = self.request("POST", f"{self.repo_path}/pulls", json={"title": title, "head": branch, "base": base_branch, "body": body, "draft": False}).json()
            except GitHubPhase6Error:
                matching = self.list_pull_requests(branch)
                if len(matching) != 1:
                    raise
                pull = matching[0]
        return {"repository_id": self.repository_info()["id"], "head_sha": current, "number": pull["number"], "url": pull["html_url"]}

    def workflow_runs(self, branch: str, head_sha: str) -> list[dict]:
        response = self.request("GET", f"{self.repo_path}/actions/workflows/deployflow.yml/runs", params={"branch": branch, "event": "push", "head_sha": head_sha, "per_page": 30})
        return response.json().get("workflow_runs", [])

    def workflow_run(self, run_id: int) -> dict:
        return self.request("GET", f"{self.repo_path}/actions/runs/{run_id}").json()

    def run_artifacts(self, run_id: int) -> list[dict]:
        return self.request("GET", f"{self.repo_path}/actions/runs/{run_id}/artifacts", params={"per_page": 100}).json().get("artifacts", [])

    def artifact_zip(self, artifact_id: int) -> bytes:
        response = self.request("GET", f"{self.repo_path}/actions/artifacts/{artifact_id}/zip")
        if response.status_code not in {301, 302, 303, 307, 308}:
            raise GitHubPhase6Error("github_artifact_redirect_missing")
        location = response.headers.get("Location", "")
        parsed = urlsplit(location)
        host = parsed.hostname or ""
        trusted = (
            host.endswith(".actions.githubusercontent.com")
            or host == "objects.githubusercontent.com"
            or (host.endswith(".blob.core.windows.net") and parsed.path.startswith("/actions-results/"))
        )
        if parsed.scheme != "https" or parsed.username or parsed.password or not trusted:
            raise GitHubPhase6Error("github_artifact_redirect_invalid")
        assert self.client is not None
        try:
            with self.client.stream("GET", location) as archive:
                archive.raise_for_status()
                chunks = []
                size = 0
                for chunk in archive.iter_bytes():
                    size += len(chunk)
                    if size > 128 * 1024:
                        raise GitHubPhase6Error("github_artifact_too_large")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise GitHubPhase6Error("github_artifact_unavailable", transient=True) from exc
        return b"".join(chunks)
