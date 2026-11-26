"""Reviewed build templates for the statically detected standalone services."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.bootstrap.postgres import POSTGRES_DATABASE, POSTGRES_PORT, migrated_start

NATIVE_UID = 10001
RUNTIME_SCRATCH_VOLUME = {"name": "runtime-tmp", "emptyDir": {"sizeLimit": "64Mi"}}
RUNTIME_SCRATCH_MOUNT = {"name": "runtime-tmp", "mountPath": "/tmp"}

PYTHON_IMAGES = ("3.12", "3.11", "3.13")
MAVEN_JAVA_VERSIONS = ("17", "21", "25")
PYTHON_INSTALL = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
PYTHON_TEST = ["python", "-m", "pytest"]
GUNICORN_WORKER = "uvicorn_worker.UvicornWorker"

NATIVE_DOCKERIGNORE = """.git
**/.git
**/.env
**/.env.*
**/.netrc
**/.npmrc
**/id_rsa
**/id_ed25519
**/*.pem
**/*.key
**/*.p12
**/*.pfx
**/*.jks
**/__pycache__
**/.venv
**/node_modules
**/target
"""


@dataclass(frozen=True)
class NativeBuild:
    dockerfile: str
    ci_steps: list[dict]
    environment: dict[str, str]
    template: str


def python_base_image(constraint: str | None) -> str:
    # Select a minor only if its full patch series satisfies the declared range.
    # Reject unsupported constraint forms instead of guessing at compatibility.
    bounds = []
    if constraint:
        for clause in constraint.split(","):
            match = re.fullmatch(r"\s*(<=|>=|<|>|==)\s*(\d+)\.(\d+)(\.0|\.\*)?\s*", clause)
            if not match or ((match[1] == "==") != (match[4] == ".*")):
                raise ValueError("unsupported_python_version_constraint")
            bounds.append((match[1], (int(match[2]), int(match[3]))))
    for version in PYTHON_IMAGES:
        candidate = tuple(map(int, version.split(".")))
        if all(
            {
                ">=": candidate >= limit,
                "<": candidate < limit,
                "==": candidate == limit,
                ">": candidate > limit,
                "<=": candidate < limit,
            }[operator]
            for operator, limit in bounds
        ):
            return f"python:{version}-slim-bookworm"
    raise ValueError("unsupported_python_version")


def python_listener(argv: list[str]) -> tuple[str, int] | None:
    if len(argv) != 6 or not re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", argv[1], re.ASCII
    ):
        return None
    if argv[0] == "uvicorn" and argv[2:5] == ["--host", "0.0.0.0", "--port"]:
        port = argv[5]
    elif (
        argv[0] == "gunicorn"
        and argv[2] in {"-k", "--worker-class"}
        and argv[3] == GUNICORN_WORKER
        and argv[4] in {"-b", "--bind"}
        and argv[5].startswith("0.0.0.0:")
    ):
        port = argv[5].partition(":")[2]
    else:
        return None
    if re.fullmatch(r"[1-9][0-9]{0,4}", port) and int(port) <= 65535:
        return argv[1], int(port)
    return None


def _native_commands(family, install, test, build, start, port, verified_gunicorn):
    if family == "python":
        listener = python_listener(start)
        if (
            install != PYTHON_INSTALL
            or test not in (None, PYTHON_TEST)
            or build is not None
            or listener is None
        ):
            raise ValueError("unsupported_python_commands")
        if start[0] == "gunicorn" and not verified_gunicorn:
            raise ValueError("gunicorn_requires_verified_source_and_dependencies")
        if listener[1] != port:
            raise ValueError(f"{start[0]}_command_port_must_match_service_port")
    elif family == "ruby":
        if (
            install != ["bundle", "install"]
            or test != ["bundle", "exec", "rake", "unit"]
            or build is not None
            or start != ["bundle", "exec", "puma", "-C", "config/puma.rb"]
        ):
            raise ValueError("unsupported_rails_commands")
    elif (
        install != ["mvn", "-B", "dependency:go-offline"]
        or test not in (None, ["mvn", "-B", "test"])
        or build != ["mvn", "-B", "package"]
        or len(start) != 3
        or start[:2] != ["java", "-jar"]
        or not re.fullmatch(r"target/[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.jar", start[2])
    ):
        raise ValueError("unsupported_maven_commands")


def native_build(
    family, version, port, install, test, build, start, *,
    verified_gunicorn=False, migration_module=None, ephemeral_postgres=False,
    migration_command=None, bundler=None,
):
    _native_commands(family, install, test, build, start, port, verified_gunicorn)
    if family == "python":
        base_image = python_base_image(version)
        environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        if start[0] == "gunicorn":
            environment["TMPDIR"] = "/tmp"
    elif family == "ruby":
        if not re.fullmatch(r"3\.3\.\d{1,3}", version or "") or not re.fullmatch(r"2\.\d{1,3}\.\d{1,3}", bundler or ""):
            raise ValueError("unsupported_ruby_bundler_versions")
        base_image = f"ruby:{version}-slim-bookworm"
        environment = {"RAILS_ENV": "production", "RAILS_LOG_TO_STDOUT": "1", "TMPDIR": "/tmp"}
    else:
        if version not in MAVEN_JAVA_VERSIONS:
            raise ValueError("unsupported_java_version")
        base_image = f"maven:3-eclipse-temurin-{version}-noble"
        environment = {"SERVER_PORT": str(port)}

    docker = [
        f"FROM {base_image} AS prepared",
        "WORKDIR /app",
        "COPY . .",
        "RUN " + json.dumps(install),
    ]
    if family == "ruby":
        docker[2:2] = [
            "RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev libyaml-dev pkg-config && rm -rf /var/lib/apt/lists/*",
            "RUN " + json.dumps(["gem", "install", "bundler", "--version", bundler, "--no-document"]),
            "ENV BUNDLE_FROZEN=true",
        ]
    if build:
        docker.append("RUN " + json.dumps(build))
    if test:
        docker += ["FROM prepared AS test", "RUN " + json.dumps(test)]
        test_steps = [{"run": "docker build --target test --file Dockerfile ."}]
    else:
        test_steps = [{"run": "echo 'No test command was confirmed for this repository'; exit 1"}]
    launch = start
    if migration_module:
        # The registered source migration is replay-safe. A failed migration
        # prevents startup, and only the runtime container receives the DB URL.
        launcher = (
            'import os, subprocess, sys; '
            'subprocess.run([sys.executable, "-m", sys.argv[1]], check=True, timeout=60); '
            'os.execvp(sys.argv[2], sys.argv[2:])'
        )
        if ephemeral_postgres:
            launcher = (
                'import os; from urllib.parse import quote; '
                f'os.environ["DATABASE_URL"] = "postgresql://{POSTGRES_DATABASE}:" + '
                'quote(os.environ.pop("POSTGRES_PASSWORD"), safe="") + '
                f'"@127.0.0.1:{POSTGRES_PORT}/{POSTGRES_DATABASE}"; ' + launcher
            )
        launch = ["python", "-c", launcher, migration_module, *start]
    elif migration_command:
        launch = migrated_start(start, migration_command, ephemeral_postgres)
    docker += [
        "FROM prepared AS runtime",
        f"USER {NATIVE_UID}:{NATIVE_UID}",
        f"EXPOSE {port}",
        "ENTRYPOINT []",
        "CMD " + json.dumps(launch),
        "",
    ]
    return NativeBuild(
        dockerfile="\n".join(docker),
        ci_steps=test_steps,
        environment=environment,
        template="python-gunicorn-web-v1" if start[0] == "gunicorn" else f"{family}-web-v1",
    )
