"""Validate GitHub's authenticated run response before consuming its public bundle."""

import json
import sys


def check(run, source, attempt):
    if (run.get("repository", {}).get("full_name") != "DamiBeGud/deploy-flow"
            or run.get("path") != ".github/workflows/platform-images.yml"
            or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
            or run.get("head_sha") != source or run.get("run_attempt") != int(attempt)
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError("Publication must be a successful manual run at this exact main source and attempt")


if __name__ == "__main__":
    with open(sys.argv[1]) as stream:
        check(json.load(stream), sys.argv[2], sys.argv[3])
