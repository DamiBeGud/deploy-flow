"""Fail-closed verification of merged files and the CI-produced OCI digest."""

from __future__ import annotations

import io
import json
import re
import zipfile

import httpx

from app.integrations.github.api import GitHubAPI, GitHubPhase6Error

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"ghcr\.io/[a-z0-9._/-]+\Z")


def checked_artifact(archive: bytes, *, repository_id: int, commit_sha: str, run_id: int, run_attempt: int, image_repository: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if len(members) != 1 or members[0].filename != "deployflow-build.json" or members[0].file_size > 16 * 1024:
                raise GitHubPhase6Error("build_artifact_invalid")
            payload = json.loads(bundle.read(members[0]))
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise GitHubPhase6Error("build_artifact_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"repository_id", "commit_sha", "run_id", "run_attempt", "image_repository", "image_digest"}:
        raise GitHubPhase6Error("build_artifact_invalid")
    expected = (repository_id, commit_sha, run_id, run_attempt, image_repository)
    actual = tuple(payload[key] for key in ("repository_id", "commit_sha", "run_id", "run_attempt", "image_repository"))
    if any(type(value) is not type(reference) or value != reference for value, reference in zip(actual, expected)):
        raise GitHubPhase6Error("build_artifact_mismatch")
    digest = payload["image_digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise GitHubPhase6Error("build_artifact_invalid")
    return digest


def checked_workflow_run(run: dict, *, repository_id: int, merged_sha: str, base_branch: str) -> bool:
    if run.get("status") != "completed":
        return False
    return (
        run.get("event") == "push"
        and run.get("conclusion") == "success"
        and run.get("head_sha") == merged_sha
        and run.get("head_branch") == base_branch
        and run.get("path") in {".github/workflows/deployflow.yml", f".github/workflows/deployflow.yml@{base_branch}"}
        and (run.get("repository") or {}).get("id") == repository_id
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] >= 1
    )


def checked_merged_files(api: GitHubAPI, *, base_sha: str, merged_sha: str, files: dict[str, str]) -> None:
    if not isinstance(merged_sha, str) or not _SHA.fullmatch(merged_sha) or api.compare_paths(base_sha, merged_sha) != set(files):
        raise GitHubPhase6Error("merged_files_changed")
    for path, expected in files.items():
        if api.file_at(path, merged_sha) != expected.encode():
            raise GitHubPhase6Error("merged_files_changed")


def verify_registry_digest(image_repository: str, digest: str) -> None:
    """Public GHCR manifest must exist at the exact claimed digest."""
    if not _IMAGE.fullmatch(image_repository) or not _DIGEST.fullmatch(digest):
        raise GitHubPhase6Error("image_identity_invalid")
    path = image_repository.removeprefix("ghcr.io/")
    url = f"https://ghcr.io/v2/{path}/manifests/{digest}"
    accept = ", ".join((
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ))
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.head(url, headers={"Accept": accept})
            if response.status_code == 401:
                challenge = response.headers.get("WWW-Authenticate", "")
                if not challenge.startswith("Bearer ") or 'realm="https://ghcr.io/token"' not in challenge:
                    raise GitHubPhase6Error("registry_auth_unexpected")
                token_response = client.get("https://ghcr.io/token", params={"scope": f"repository:{path}:pull", "service": "ghcr.io"})
                token_response.raise_for_status()
                token = token_response.json().get("token")
                if not isinstance(token, str) or not token:
                    raise GitHubPhase6Error("registry_auth_unavailable")
                response = client.head(url, headers={"Accept": accept, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise GitHubPhase6Error("registry_unavailable", transient=True) from exc
    if response.status_code != 200 or response.headers.get("Docker-Content-Digest", "").lower() != digest:
        raise GitHubPhase6Error("registry_digest_unverified")
