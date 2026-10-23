"""Read-only selection of current main's latest successful push publication."""

import json
import os

from github_proof import GitHubProof


def select(proof):
    source = proof.read("/git/ref/heads/main")["object"]["sha"]
    runs = proof.read("/actions/workflows/platform-images.yml/runs?branch=main&event=push&head_sha=" + source + "&per_page=30").get("workflow_runs", [])
    runs.sort(key=lambda r: (r.get("id", 0), r.get("run_attempt", 0)), reverse=True)
    if not runs or runs[0].get("status") != "completed" or runs[0].get("conclusion") != "success":
        return {"eligible": "false", "reason": "current_main_awaiting_successful_publication"}
    run = runs[0]
    try:
        bundle = proof.publication(run["id"], run["run_attempt"], source)
    except ValueError as exc:
        if str(exc) == "publication_artifact_missing":
            return {"eligible": "false", "reason": "current_main_not_published"}
        raise
    if bundle["source_sha"] != source:
        raise ValueError("Publication source differs")
    return {"eligible": "true", "source_sha": source, "publication_run_id": str(run["id"]), "publication_attempt": str(run["run_attempt"])}


if __name__ == "__main__":
    result = select(GitHubProof(os.environ.get("GH_TOKEN")))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.writelines(key + "=" + value + "\n" for key, value in result.items())
    print(json.dumps(result))
