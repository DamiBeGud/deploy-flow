"""Exercise the actual tunnel supervisor using a local failing SSH child."""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def start_supervisor(tmp_path):
    ssh = tmp_path / "ssh"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys,time\n"
        "from pathlib import Path\n"
        "log=Path(os.environ['DF_TEST_TUNNEL_LOG'])\n"
        "with log.open('a') as out:out.write(json.dumps({'pid':os.getpid(),'args':sys.argv[1:]})+'\\n')\n"
        "if len(log.read_text().splitlines())==1:sys.exit(255)\n"
        "time.sleep(60)\n"
    )
    ssh.chmod(0o755)
    log = tmp_path / "attempts.jsonl"
    process = subprocess.Popen(
        ["sh", str(ROOT / "scripts/k3s_tunnel_session.sh")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "DF_K3S_SSH_TARGET": "fixture@example.invalid", "DF_TEST_TUNNEL_LOG": str(log)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return process, log


def wait_attempts(process, log, count):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert process.poll() is None, "supervisor exited instead of reconnecting"
        rows = log.read_text().splitlines() if log.exists() else []
        if len(rows) >= count:
            return [json.loads(row) for row in rows]
        time.sleep(0.05)
    raise AssertionError("SSH retry did not occur")


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        assert process.wait(timeout=2) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_disconnect_retries_without_exiting_supervisor_and_stops_child(tmp_path):
    process, log = start_supervisor(tmp_path)
    try:
        rows = wait_attempts(process, log, 2)
        assert process.poll() is None
        args = rows[-1]["args"]
        assert args[-1] == "fixture@example.invalid"
        assert args[args.index("-L") + 1] == "127.0.0.1:16443:127.0.0.1:6443"
        assert "StrictHostKeyChecking=yes" in args
        assert "ConnectTimeout=10" in args
        assert "ServerAliveCountMax=3" in args
    finally:
        stop(process)
    try:
        os.kill(rows[-1]["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("SSH child survived supervisor shutdown")


def test_shutdown_during_reconnect_delay_is_prompt(tmp_path):
    process, log = start_supervisor(tmp_path)
    try:
        wait_attempts(process, log, 1)
        # The first SSH child exits; stopping must also interrupt the retry sleep.
        time.sleep(0.15)
    finally:
        stop(process)
