"""Read-only GitHub proofs shared by the fixed server operator and finalizer.

The short-lived Actions token is input in memory only. Never include it in a
receipt, command argument, artifact, exception, or response.
"""

import base64
import datetime
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile

REPO = "DamiBeGud/deploy-flow"


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            target.remove_header("Authorization")
        return target


class GitHubProof:
    def __init__(self, token):
        if not token:
            raise ValueError("github_proof_token_missing")
        self.token = token

    def read(self, path, *, raw=False):
        request = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Accept": "application/vnd.github+json", "User-Agent": "DeployFlow-fixed-operator"})
        try:
            with urllib.request.build_opener(Redirect).open(request, timeout=20) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("github_proof_too_large")
            return data if raw else json.loads(data)
        except (OSError, ValueError):
            raise ValueError("github_proof_unavailable") from None

    def run(self, context, *, completed=False):
        if (set(context) != {"run_id", "attempt", "source_sha", "workflow"}
                or type(context["run_id"]) is not int or context["run_id"] < 1
                or type(context["attempt"]) is not int or context["attempt"] < 1
                or not re.fullmatch(r"[a-f0-9]{40}", context["source_sha"])
                or context["workflow"] not in {".github/workflows/platform-upgrade.yml", ".github/workflows/platform-auto.yml"}):
            raise ValueError("github_controller_context_invalid")
        run = self.read(f"/actions/runs/{context['run_id']}/attempts/{context['attempt']}")
        if (run.get("repository", {}).get("full_name") != REPO or run.get("head_branch") != "main"
                or run.get("head_sha") != context["source_sha"] or run.get("path") != context["workflow"]
                or run.get("run_attempt") != context["attempt"]
                or run.get("event") not in {"workflow_dispatch", "workflow_run", "schedule"}
                or (completed and run.get("conclusion") != "success")):
            raise ValueError("github_controller_untrusted")
        return run

    def public_job(self, context, *, since=None):
        self.run(context)
        jobs = self.read(f"/actions/runs/{context['run_id']}/attempts/{context['attempt']}/jobs?per_page=100")
        found = [j for j in jobs.get("jobs", []) if j.get("name") == "public-verification"]
        if len(found) != 1 or found[0].get("status") != "completed" or found[0].get("conclusion") != "success":
            raise ValueError("independent_public_verification_not_proven")
        if since:
            try:
                stamp = found[0]["completed_at"]
                if not isinstance(stamp, str):
                    raise TypeError("missing timestamp")
                completed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                verified = datetime.datetime.fromisoformat(since)
                now = datetime.datetime.now(datetime.timezone.utc)
                if completed < verified or completed > now or now - completed > datetime.timedelta(minutes=30):
                    raise ValueError("stale")
            except (KeyError, ValueError, TypeError):
                raise ValueError("independent_public_verification_stale") from None
        return {"run_id": context["run_id"], "attempt": context["attempt"], "job_id": found[0]["id"],
                "source_sha": context["source_sha"], "vantage": "independent-github-runner", "completed_at": found[0].get("completed_at")}

    def file(self, path, source):
        value = self.read("/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + source)
        if value.get("type") != "file" or value.get("encoding") != "base64":
            raise ValueError("approved_controller_file_missing")
        return base64.b64decode(value["content"])

    def publication(self, identifier, attempt, source):
        from check_publication import check
        run = self.read(f"/actions/runs/{identifier}/attempts/{attempt}")
        check(run, source, attempt)
        artifacts = self.read(f"/actions/runs/{identifier}/artifacts?per_page=100").get("artifacts", [])
        found = [a for a in artifacts if a.get("name") == f"platform-release-{attempt}" and not a.get("expired")]
        if len(found) != 1:
            raise ValueError("publication_artifact_missing")
        artifact = found[0]
        raw = self.read(f"/actions/artifacts/{artifact['id']}/zip", raw=True)
        if artifact.get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise ValueError("publication_artifact_digest_invalid")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = [i for i in archive.infolist() if i.filename == "release.json"]
            if len(entries) != 1 or entries[0].file_size > 4 * 1024 * 1024:
                raise ValueError("publication_bundle_invalid")
            return json.loads(archive.read(entries[0]))
