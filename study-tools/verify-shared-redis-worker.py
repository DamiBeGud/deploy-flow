"""Verify the pinned native M-PY05 worker against a disposable local Redis."""

import argparse
import hashlib
import json
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import celery
import redis as redis_client

CLIENT = """
import json
import os
import sys
import time
from fastapi.testclient import TestClient
from checksum_jobs.app import app

operation, argument, *more = sys.argv[1:]
with TestClient(app) as client:
    header = {"Authorization": "Bearer " + os.environ["API_TOKEN"]}
    if operation == "submit":
        body = {"reference": argument, "text": more[0]}
        assert client.post("/jobs", json=body).status_code == 401
        assert client.get("/ready").json() == {"status": "ready"}
        response = client.post("/jobs", json=body, headers=header)
        assert response.status_code == 202
        print(json.dumps(response.json()))
    else:
        for _ in range(70 if operation == "await" else 1):
            response = client.get("/jobs/" + argument, headers=header)
            assert response.status_code == 200
            value = response.json()
            if operation == "status" or value["state"] in {"SUCCESS", "FAILURE"}:
                print(json.dumps(value))
                sys.exit()
            time.sleep(0.20)
        raise RuntimeError("Expected worker result before deadline")
"""

EXPECTED_SOURCE = "9938bbf748987ef8362ee305d10f4ab35327b37e"


def check_worker(source, redis_server, binding_type, root, pool):
    with socket.socket() as channel:
        channel.bind(("127.0.0.1", 0))
        port = channel.getsockname()[1]
    password = secrets.token_hex(20)
    config = root / "redis.conf"
    config.touch(mode=0o600)
    config.write_text(f"""bind 127.0.0.1
    protected-mode yes
    port {port}
    save ""
    appendonly no
    requirepass {password}
    """)

    base_env = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.defpath}",
                "TMPDIR": str(root), "PYTHONDONTWRITEBYTECODE": "1",
                "LANG": "C", "LC_ALL": "C"}
    apps = [binding_type(
        application_id=UUID(f"00000000-0000-4000-8000-{i:012d}"),
        application_name=f"native-checksum-{i}", repository="damibegud/m-py05-checksum-jobs",
        source_sha=EXPECTED_SOURCE, worker_target="checksum_jobs.worker:celery",
        redis_connection_secret=f"local-redis-ref-{i}", api_token_secret=f"local-api-token-{i}",
    ) for i in (1, 2)]
    def environments(binding, index):
        # Supply synthetic runtime values directly where Kubernetes would resolve the approved private references.
        urls = {key: f"redis://:{quote(password)}@127.0.0.1:{port}/{database}"
                for database, key in enumerate(("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"))}
        worker = base_env | binding.environment() | urls
        assert {entry["name"]: entry["valueFrom"]["secretKeyRef"] for entry in binding.connection_env()} == {
            name: {"name": binding.redis_connection_secret, "key": name} for name in urls
        }
        assert "API_TOKEN" not in worker
        return worker, worker | {"API_TOKEN": f"native-test-token-{index}"}

    def observe(environment, operation, identifier, *args):
        result = subprocess.run([sys.executable, "-B", "-c", CLIENT, operation, identifier, *args],
                                cwd=source, env=environment, text=True, capture_output=True, timeout=21, check=False)
        assert result.returncode == 0, (operation, result.returncode, "native API assertion failed")
        return json.loads(result.stdout)

    def start_worker(binding, environment):
        command = binding.worker_argv()
        if pool == "solo":
            command.append("--pool=solo")
        executable = Path(sys.executable).parent / command[0]
        with (root / (binding.application_name + ".log")).open("w") as log:
            return subprocess.Popen([str(executable), *command[1:]], cwd=source, env=environment,
                                    start_new_session=True, stdout=log, stderr=subprocess.STDOUT)

    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=4)

    server = subprocess.Popen([str(redis_server), str(config)], cwd=root, env=base_env, start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    workers = []
    try:
        redis = redis_client.Redis(host="127.0.0.1", port=port, password=password, socket_timeout=1)
        for _ in range(30):
            assert server.poll() is None, "new Redis fixture exited"
            try:
                if redis.ping():
                    break
            except redis_client.exceptions.ConnectionError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Redis did not become ready")
        assert redis.config_get("bind") == {"bind": "127.0.0.1"}
        assert redis.config_get("protected-mode") == {"protected-mode": "yes"}
        assert redis.config_get("save") == {"save": ""} and redis.config_get("appendonly") == {"appendonly": "no"}
        version = redis.info("server")["redis_version"]
        assert version == "7.4.11"
        (worker_a, client_a), (worker_b, client_b) = [environments(binding, i) for i, binding in enumerate(apps)]
        assert worker_a["QUEUE_NAMESPACE"] != worker_b["QUEUE_NAMESPACE"]
        first = start_worker(apps[0], worker_a)
        workers.append(first)
        samples = ("study-test A", "study-test B!")
        job_a = observe(client_a, "submit", "fixture-a", samples[0])["job_id"]
        job_b = observe(client_b, "submit", "fixture-b", samples[1])["job_id"]
        assert job_a != job_b
        a = observe(client_a, "await", job_a)
        assert first.poll() is None
        assert observe(client_b, "status", job_b) == {"job_id": job_b, "state": "PENDING", "result": None}
        assert observe(client_b, "status", job_a) == {"job_id": job_a, "state": "PENDING", "result": None}
        stop(first)
        second = start_worker(apps[1], worker_b)
        workers.append(second)
        b = observe(client_b, "await", job_b)
        assert second.poll() is None and first.poll() is not None
        assert observe(client_a, "status", job_b) == {"job_id": job_b, "state": "PENDING", "result": None}
        for result, job, reference, value in ((a, job_a, "fixture-a", samples[0]), (b, job_b, "fixture-b", samples[1])):
            payload = value.encode()
            expected = {"job_id": job, "state": "SUCCESS",
                        "result": {"reference": reference, "sha256": hashlib.sha256(payload).hexdigest(),
                                   "byte_length": len(payload)}}
            assert result == expected, (reference, result["state"], result.get("result"))
        results = redis_client.Redis(host="127.0.0.1", port=port, db=1, password=password, socket_timeout=1)
        keys = set(results.scan_iter(match="*celery-task-meta-*"))
        expected = {f"{binding.environment()['QUEUE_NAMESPACE']}:celery-task-meta-{job}".encode()
                    for binding, job in zip(apps, (job_a, job_b))}
        assert keys == expected
        summary = {"recorded_at": datetime.now(timezone.utc).isoformat(), "source_sha": EXPECTED_SOURCE,
                   "case_id": "M-PY05", "redis_version": version, "celery": celery.__version__,
                   "redis_client": redis_client.__version__, "single_private_loopback_redis": True,
                   "api_requests": "native FastAPI TestClient", "real_worker_result_count": 2,
                   "system": platform.system(), "worker_pool": pool, "used_generated_worker_args": pool == "default",
                   "generated_worker_argv": apps[0].worker_argv(),
                   "missing_api_token_rejected_on_both": True, "one_active_worker_at_a_time": True,
                   "waiting_job_stayed_pending_in_other_scope": True, "cross_scope_result_hidden_both_directions": True,
                   "exact_prefixed_keys_observed": True, "scope_hard_security_isolation": False}
    finally:
        for child in workers:
            stop(child)
        stop(server)
        config.unlink()
    assert all(child.poll() is not None for child in workers) and server.poll() is not None
    summary["test_processes_stopped"] = True

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Clean pinned M-PY05 checkout")
    parser.add_argument("--backend", type=Path, required=True, help="DeployFlow backend with combined patch applied")
    parser.add_argument("--redis-server", type=Path, required=True, help="Local Redis 7.4.11 binary")
    parser.add_argument("--pool", choices=("default", "solo"), default="default", help="Use generated argv or explicitly override the worker pool")
    args = parser.parse_args()
    if not __debug__:
        parser.error("Run the verification without Python optimization")

    source = args.source.resolve()
    source_command = ["git", "-C", str(source)]
    revision = subprocess.check_output([*source_command, "rev-parse", "HEAD"], text=True, timeout=10).strip()
    status = subprocess.check_output([*source_command, "status", "--porcelain", "--untracked-files=all"], text=True, timeout=10)
    if revision != EXPECTED_SOURCE or status:
        parser.error(f"Use a clean source checkout at {EXPECTED_SOURCE}")
    backend = args.backend.resolve()
    if not (backend / "app/bootstrap/redis.py").is_file():
        parser.error("DeployFlow backend is missing app/bootstrap/redis.py")
    redis_server = args.redis_server.resolve()
    if not redis_server.is_file() or not os.access(redis_server, os.X_OK):
        parser.error("Pass the path to an executable Redis server")

    sys.path.insert(0, str(backend))
    from app.bootstrap.redis import RedisBinding

    with tempfile.TemporaryDirectory(prefix="ba-redis-") as directory:
        root = Path(directory)
        archive = root / "source.tar"
        snapshot = root / "source"
        subprocess.run([*source_command, "archive", "--output", str(archive), EXPECTED_SOURCE],
                       timeout=10, check=True)
        with tarfile.open(archive) as tracked_source:
            tracked_source.extractall(snapshot, filter="data")
        summary = check_worker(snapshot, redis_server, RedisBinding, root, args.pool)
    summary["binding_module_sha256"] = hashlib.sha256((backend / "app/bootstrap/redis.py").read_bytes()).hexdigest()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
