"""Copy root-readable bind mounts to a private tmpfs, then drop privileges."""

from __future__ import annotations

import os
import runpy
from pathlib import Path


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Kubernetes credential bootstrap must start as root")
    runtime = Path("/run/deployflow-runtime")
    runtime.mkdir(mode=0o700, exist_ok=True)
    os.chown(runtime, 10001, 10001)
    runtime.chmod(0o700)
    for source_name, target_name, environment in (
        ("/run/deployflow/token", "token", "DF_KUBE_TOKEN_PATH"),
        ("/run/deployflow/ca.crt", "ca.crt", "DF_KUBE_CA_PATH"),
    ):
        content = Path(source_name).read_bytes()
        if not content:
            raise RuntimeError("Kubernetes credential file is empty")
        target = runtime / target_name
        target.write_bytes(content)
        os.chown(target, 10001, 10001)
        target.chmod(0o600)
        os.environ[environment] = str(target)
    if os.environ.get("DF_KUBE_MCP_ROLE") == "deployer":
        github_key = os.environ.get("DF_GITHUB_PRIVATE_KEY_PATH")
        if github_key:
            target = runtime / "github-app.pem"
            target.write_bytes(Path(github_key).read_bytes())
            os.chown(target, 10001, 10001)
            target.chmod(0o600)
            os.environ["DF_GITHUB_PRIVATE_KEY_PATH"] = str(target)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    if os.environ.get("DF_KUBE_MCP_ROLE") == "deployer":
        os.execvp("dramatiq", [
            "dramatiq", "app.worker", "--processes", "1", "--threads", "1",
            "--queues", "execution",
        ])
    runpy.run_module("mcp_servers.kubernetes_server", run_name="__main__")


if __name__ == "__main__":
    main()
