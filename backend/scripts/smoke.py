"""Exercise the real Compose API/worker and optionally interrupt local containers.

Run from backend/: python3 scripts/smoke.py [--recovery]
Uses only the standard library. Recovery targets this directory's Compose services.
"""

import argparse
import http.client
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    def request(path, *, body=None, key=None, headers=None):
        supplied = {"Content-Type": "application/json", **(headers or {})}
        if key:
            supplied["Idempotency-Key"] = key
        req = urllib.request.Request(
            args.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=supplied,
        )
        return urllib.request.urlopen(req, timeout=15)

    def json_request(path, **kwargs):
        with request(path, **kwargs) as response:
            return json.load(response)

    def compose(*parts):
        subprocess.run(["docker", "compose", *parts], cwd=ROOT, check=True, timeout=45)

    def wait_for(run_id, statuses, timeout=75):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = json_request(f"/api/v1/analyses/{run_id}")
            if result["status"] in statuses:
                return result
            if result["status"] in {"failed", "cancelled"}:
                raise AssertionError(result)
            time.sleep(0.25)
        raise AssertionError("Timed out waiting for analysis")

    # Compose starting a container does not mean Uvicorn is already listening.
    ready_deadline = time.monotonic() + 30
    while True:
        try:
            assert json_request("/health/ready")["status"] == "ready"
            break
        except (OSError, http.client.HTTPException):
            if time.monotonic() >= ready_deadline:
                raise
            time.sleep(0.5)

    suffix = uuid4().hex[:10]
    created = json_request(
        "/api/v1/applications",
        key=f"smoke-app-{suffix}",
        body={
            "name": f"smoke-{suffix}",
            "repository_url": "https://github.com/DamiBeGud/deployflow-node-express-fixture",
        },
    )
    path = f"/api/v1/applications/{created['id']}/analyses"
    queued = json_request(path, body={}, key=f"smoke-run-{suffix}")
    repeated = json_request(path, body={}, key=f"smoke-run-{suffix}")
    assert queued == repeated
    run_id = queued["run_id"]
    print(f"Duplicate requests returned one run: {run_id}", flush=True)

    # Consume one live event then disconnect. Later reconnect with this exact ID.
    with request(queued["events_url"]) as stream:
        cursor = None
        while cursor is None:
            line = stream.readline().decode().strip()
            if line.startswith("id: "):
                cursor = int(line[4:])
    wait_for(run_id, {"running"})
    if args.recovery:
        print(
            "Killing this Compose worker during analysis; waiting for lease recovery.", flush=True
        )
        compose("kill", "--signal", "SIGKILL", "worker")
        compose("up", "-d", "--no-deps", "worker")

    result = wait_for(run_id, {"completed"})
    assert result["assessment"]["mode"] == "fake"
    if args.recovery:
        assert result["attempts"] >= 2, result
        compose("restart", "redis")
    with request(queued["events_url"], headers={"Last-Event-ID": str(cursor)}) as response:
        replay = response.read().decode()
    ids = [int(line[4:]) for line in replay.splitlines() if line.startswith("id: ")]
    assert ids and min(ids) > cursor and ids == sorted(set(ids))
    assert "event: analysis.completed" in replay
    print(f"Completed in {result['attempts']} attempt(s); replayed event IDs {ids}.", flush=True)
    print("PASS: real API, dispatcher, Redis, worker, PostgreSQL, and SSE reconnect.", flush=True)


if __name__ == "__main__":
    main()
