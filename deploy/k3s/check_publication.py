"""Validate GitHub's authenticated run response before consuming its public bundle."""

import json
import re
import sys


def check(run, source, attempt):
    if (run.get("repository", {}).get("full_name") != "DamiBeGud/deploy-flow"
            or run.get("path") != ".github/workflows/platform-images.yml"
            or run.get("event") not in {"workflow_dispatch", "push"} or run.get("head_branch") != "main"
            or run.get("head_sha") != source or run.get("run_attempt") != int(attempt)
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError("Publication must be a successful main push/manual run at this exact source and attempt")


def check_main(source, current):
    if not re.fullmatch(r"[a-f0-9]{40}", source) or current != source:
        raise ValueError("Main moved since this workflow source; publish and preview the current main again")


if __name__ == "__main__":
    if sys.argv[1] == "--main":
        check_main(sys.argv[2], sys.argv[3])
    else:
        with open(sys.argv[1]) as stream:
            check(json.load(stream), sys.argv[2], sys.argv[3])
