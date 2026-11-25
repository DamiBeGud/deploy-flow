import json
import re
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError

from app.api.schemas import BuildSpecification, CreateApplication
from app.bootstrap.generator import PlanError, canonical_digest, render
from app.bootstrap.recommendations import recommend
from app.kubernetes.policy import ManifestPolicyError, checked_manifests
from app.repository.scanner import Scanner

PROFILE = {
    "id": "local-k3s",
    "application_id": "test-application",
    "namespace": "deployflow-test",
    "registry": "ghcr.io",
    "default_image": "ghcr.io/example/node-fixture",
    "hostname_suffix": ".deployflow.local",
    "max_replicas": 3,
    "checkout_action": "actions/checkout@v4",
    "setup_node_action": "actions/setup-node@v4",
    "node_base_image": "node:20-bookworm-slim",
}
IDENTITY = {
    "owner": "Example",
    "name": "Demo",
    "url": "https://github.com/Example/Demo",
    "commit_sha": "a" * 40,
}

PYTHON = {
    "requirements.txt": "fastapi==0.115.12\nuvicorn==0.34.2\npytest==8.3.5\n",
    "pyproject.toml": '[project]\nrequires-python = ">=3.11,<3.14"\n',
    "service/main.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.get("/status")\ndef status(): return {"ok": True}\n'
    ),
    "tests/test_service.py": "def test_status(): pass\n",
    "README.md": "Start `uvicorn service.main:app --host 0.0.0.0 --port 8201`.\n",
}
RAILS = {
    "Gemfile": "gem 'rails'\ngem 'puma'\n",
    "Gemfile.lock": "GEM\n  specs:\n    rails (8.1.3)\n    puma (8.0.2)\n",
    "config/puma.rb": """bind "tcp://0.0.0.0:#{ENV.fetch('PORT','8401')}"\n""",
    "config/routes.rb": (
        "Rails.application.routes.draw do\n get '/health/live', to: 'health#live'\n"
        " get '/ready', to: 'health#ready'\nend\n"
    ),
    "config/database.yml": "url: <%= ENV.fetch('DATABASE_URL') %>\n",
    "db/migrate/20261120000000_create_records.rb": "class CreateRecords < ActiveRecord::Migration; end",
    "config/environments/production.rb": 'config.secret_key_base = ENV.fetch("SECRET_KEY_BASE")',
    "Rakefile": "Rake::TestTask.new(:unit) do |task|\nend\n",
    "test/health_test.rb": "",
}
JAVA = {
    "pom.xml": """
      <project xmlns="http://maven.apache.org/POM/4.0.0">
        <parent><groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId></parent>
        <artifactId>example</artifactId><version>0.1.0</version>
        <properties><java.version>17</java.version></properties>
        <build><plugins><plugin><artifactId>spring-boot-maven-plugin</artifactId>
        </plugin></plugins></build>
      </project>
    """,
    "src/main/resources/application.properties": "server.port=${PORT:8301}\n",
    "src/main/java/example/Controller.java": 'class C {\n @GetMapping("/health/live")\n}',
    "src/test/java/example/ControllerTest.java": "",
}
NODE = {
    "package.json": (
        '{"engines":{"node":">=22 <23"},'
        '"scripts":{"test":"node --test","build":"tsc","start":"node dist/index.js"}}'
    ),
    "package-lock.json": "{}",
    "src/index.ts": 'const port = Number(process.env.PORT ?? "3201");\napp.get("/live", handler);',
}


def scan(tmp_path: Path, files: dict[str, str]):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return Scanner(tmp_path).scan(repository=IDENTITY, application_name="demo").document


@pytest.mark.parametrize(
    ("files", "family", "port", "probe", "commands", "image_tag"),
    [
        (
            PYTHON,
            "python",
            8201,
            "/status",
            (
                "python -m pip install -r requirements.txt",
                "python -m pytest",
                None,
                "uvicorn service.main:app --host 0.0.0.0 --port 8201",
            ),
            "python:3.12-slim-bookworm",
        ),
        (
            RAILS,
            "ruby",
            8401,
            "/ready",
            (
                "bundle install",
                "bundle exec rake unit",
                None,
                "bundle exec puma -C config/puma.rb",
            ),
            None,
        ),
        (
            JAVA,
            "java",
            8301,
            "/health/live",
            (
                "mvn -B dependency:go-offline",
                "mvn -B test",
                "mvn -B package",
                "java -jar target/example-0.1.0.jar",
            ),
            "maven:3-eclipse-temurin-17-noble",
        ),
        (
            NODE,
            "nodejs",
            3201,
            "/live",
            (
                "npm ci",
                "npm test",
                "npm run build",
                "npm start",
            ),
            "node:22-bookworm-slim",
        ),
    ],
)
def test_detect_and_render_native_delivery(
    tmp_path, files, family, port, probe, commands, image_tag
):
    assessment = scan(tmp_path, files)
    expected_review = {"runtime": family, "port": port, "health_path": probe}
    if family == "ruby":
        expected_review["liveness_path"] = "/health/live"
    assert assessment["review"] == expected_review
    recommendation = recommend(assessment, PROFILE, "main", 1)
    intent = recommendation["intent"]
    build = BuildSpecification.model_validate(intent["build"])
    assert build.image == "ghcr.io/example/demo"
    for field, expected in zip(
        ("install_command", "test_command", "build_command", "start_command"), commands, strict=True
    ):
        value = getattr(build, field)
        assert (" ".join(value) if value else None) == expected
        hint = intent["field_recommendations"][f"build.{field}"]
        assert not hint["overridden"]
        if expected is not None:
            assert hint["evidence"]["file_path"] in files

    if family == "ruby":
        assert assessment["workflow"]["migration_command"] == "bundle exec rails db:migrate"
        assert assessment["configuration"]["required_variables"] == [
            "DATABASE_URL",
            "SECRET_KEY_BASE",
        ]
        assert "migration" in recommendation["generation_blocker"]
        with pytest.raises(PlanError, match="rails_requires_approved_database"):
            render(assessment, intent, PROFILE)
        return

    assert recommendation["generation_blocker"] is None
    bundle = render(assessment, intent, PROFILE)
    assert bundle.files["Dockerfile"].startswith(f"FROM {image_tag}")
    workflow = yaml.safe_load(bundle.files[".github/workflows/deployflow.yml"])
    assert workflow["jobs"]["publish"]["needs"] == ["test"]
    assert workflow["jobs"]["publish"]["if"] == "github.event_name == 'push'"
    if family != "nodejs":
        steps = workflow["jobs"]["test"]["steps"]
        assert steps[1]["run"] == "docker build --target test --file Dockerfile ."
        assert bundle.validation["runtime_template"] == f"{family}-web-v1"
    manifests = checked_manifests(
        bundle.files,
        digest=bundle.digest,
        namespace=PROFILE["namespace"],
        application_id=PROFILE["application_id"],
        registry=PROFILE["registry"],
    )
    deployment = next(item for item in manifests if item["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["containers"][0]["readinessProbe"]["httpGet"]["path"] == probe
    if family == "java":
        assert pod["volumes"] == [{"name": "runtime-tmp", "emptyDir": {"sizeLimit": "64Mi"}}]


@pytest.mark.parametrize(
    "files", [{"README.md": "Install npm packages."}, PYTHON | JAVA, PYTHON | NODE]
)
def test_unsupported_layout_does_not_receive_commands(tmp_path, files):
    assessment = scan(tmp_path, files)
    assert assessment["review"]["runtime"] == "unknown"
    assert assessment["completion"] == "partial"
    result = recommend(assessment, PROFILE, "main", 1)
    build = BuildSpecification.model_validate(result["intent"]["build"])
    assert (
        build.install_command,
        build.test_command,
        build.build_command,
        build.start_command,
    ) == ([], None, None, [])
    assert result["generation_blocker"]
    with pytest.raises(PlanError, match="unsupported_runtime"):
        render(assessment, result["intent"], PROFILE)


def test_ambiguous_fastapi_entry_is_left_blank(tmp_path):
    files = PYTHON | {"service/other.py": PYTHON["service/main.py"]}
    assessment = scan(tmp_path, files)
    assert assessment["review"]["runtime"] == "python"
    assert assessment["runtime"]["command"] is None
    assert assessment["completion"] == "partial"


def test_native_arguments_and_java_scratch_are_bounded(tmp_path):
    python = scan(tmp_path, PYTHON)
    intent = recommend(python, PROFILE, "main", 1)["intent"]
    intent["build"]["start_command"] = [
        "uvicorn",
        "service.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "9999",
    ]
    with pytest.raises(PlanError, match="uvicorn_command_port_must_match_service_port"):
        render(python, intent, PROFILE)
    intent["build"]["start_command"] = ["uvicorn", "service.main:app;echo bad"]
    with pytest.raises(PlanError, match="unsafe_start_command"):
        render(python, intent, PROFILE)

    java = scan(tmp_path / "java", JAVA)
    bundle = render(java, recommend(java, PROFILE, "main", 1)["intent"], PROFILE)
    path = "deploy/kubernetes/deployment.yaml"
    deployment = yaml.safe_load(bundle.files[path])
    pod = deployment["spec"]["template"]["spec"]
    pod["volumes"] = [{"name": "runtime-tmp", "hostPath": {"path": "/tmp"}}]
    files = bundle.files | {path: yaml.safe_dump(deployment)}
    with pytest.raises(ManifestPolicyError, match="deployment_policy_denied"):
        checked_manifests(
            files,
            digest=canonical_digest(files),
            namespace=PROFILE["namespace"],
            application_id=PROFILE["application_id"],
            registry=PROFILE["registry"],
        )


def test_detected_dependency_requires_binding(tmp_path):
    assessment = scan(
        tmp_path, PYTHON | {"requirements.txt": PYTHON["requirements.txt"] + "redis==6.0.0\n"}
    )
    result = recommend(assessment, PROFILE, "main", 1)
    assert "approved configuration bindings" in result["generation_blocker"]
    with pytest.raises(PlanError, match="native_service_requires_configuration_bindings"):
        render(assessment, result["intent"], PROFILE)


def test_approved_postgres_binding_is_source_and_application_scoped(tmp_path, monkeypatch):
    from app.deployments.release import bind_verified_image

    assessment = scan(
        tmp_path,
        PYTHON | {"requirements.txt": PYTHON["requirements.txt"] + "psycopg[binary]==3.2.8\n"},
    )
    bound_profile = PROFILE | {"application_id": "36d3695a-e390-4487-a15a-f6af6f40881f"}
    key = "DF_APPROVED_POSTGRES_BINDING"
    monkeypatch.delenv(key, raising=False)
    assert recommend(assessment, bound_profile, "main", 1)["generation_blocker"]
    import json

    binding = {
        "application_id": bound_profile["application_id"],
        "application_name": "demo",
        "repository": "example/demo",
        "source_sha": "a" * 40,
        "secret_name": "demo-db",
        "migration_module": "service.migrate",
        "liveness_path": "/live",
    }
    monkeypatch.setenv(key, json.dumps(binding))

    assert recommend(assessment, bound_profile, "main", 1)["generation_blocker"]
    result = recommend(
        assessment, bound_profile, "main", 1, application_id=bound_profile["application_id"]
    )
    assert result["approved_database"] == {"migration": "python -m service.migrate"}
    assert not result["generation_blocker"]
    intent = result["intent"]
    with pytest.raises(PlanError, match="native_service_requires_configuration_bindings"):
        render(assessment, intent, PROFILE)
    with pytest.raises(PlanError, match="native_service_requires_configuration_bindings"):
        render(assessment | {"repository": IDENTITY | {"commit_sha": "b" * 40}}, intent, bound_profile)
    bundle = render(assessment, intent, bound_profile)
    path = "deploy/kubernetes/deployment.yaml"
    manifest = yaml.safe_load(bundle.files[path])
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [{
        "name": "DATABASE_URL",
        "valueFrom": {"secretKeyRef": {"name": "demo-db", "key": "DATABASE_URL"}},
    }]
    assert container["livenessProbe"]["httpGet"]["path"] == "/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/status"
    launch = json.loads(bundle.files["Dockerfile"].rsplit("CMD ", 1)[1])
    assert launch[:2] == ["python", "-c"]
    assert "check=True, timeout=60" in launch[2]
    assert launch[3:5] == ["service.migrate", "uvicorn"]
    release = bind_verified_image(
        bundle_files=bundle.files,
        bundle_digest=bundle.digest,
        image_binding=bundle.validation["image_binding"],
        image_repository="ghcr.io/example/demo",
        image_digest="sha256:" + "c" * 64,
        profile=bound_profile,
        application_id=bound_profile["application_id"],
    )
    assert release.image_reference in release.files[path]

    intent["connectivity"]["environment"]["DATABASE_URL"] = "untrusted"
    with pytest.raises(PlanError, match="approved_postgres_binding_mismatch"):
        render(assessment, intent, bound_profile)
    container["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "another-secret"
    files = bundle.files | {path: yaml.safe_dump(manifest)}
    with pytest.raises(ManifestPolicyError, match="secret_reference_denied"):
        checked_manifests(
            files, digest=canonical_digest(files), namespace="deployflow-test",
            application_id=bound_profile["application_id"], registry="ghcr.io",
        )


def test_generated_ephemeral_postgres_sidecar_is_exact_and_private(tmp_path, monkeypatch):
    import json

    assessment = scan(
        tmp_path,
        PYTHON | {"requirements.txt": PYTHON["requirements.txt"] + "psycopg[binary]==3.2.8\n"},
    )
    profile = PROFILE | {"application_id": "36d3695a-e390-4487-a15a-f6af6f40881f"}
    monkeypatch.setenv("DF_APPROVED_POSTGRES_BINDING", json.dumps({
        "application_id": profile["application_id"],
        "application_name": "demo",
        "repository": "example/demo",
        "source_sha": "a" * 40,
        "secret_name": "demo-db",
        "migration_module": "service.migrate",
        "liveness_path": "/live",
        "ephemeral": True,
    }))
    recommendation = recommend(assessment, profile, "main", 1, application_id=profile["application_id"])
    assert recommendation["approved_database"]["ephemeral"] is True
    intent = recommendation["intent"]
    bundle = render(assessment, intent, profile)
    kwargs = {"namespace": "deployflow-test", "application_id": profile["application_id"], "registry": "ghcr.io"}
    checked_manifests(bundle.files, digest=bundle.digest, **kwargs)
    path = "deploy/kubernetes/deployment.yaml"
    deployment = yaml.safe_load(bundle.files[path])
    app_pod = deployment["spec"]["template"]["spec"]
    database = app_pod["initContainers"][0]
    assert database["restartPolicy"] == "Always"
    assert "listen_addresses=127.0.0.1" in database["args"]
    assert any(item == {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "demo-db", "key": "password"}}} for item in database["env"])
    assert len(app_pod["containers"]) == 1
    assert yaml.safe_load(bundle.files["deploy/kubernetes/service.yaml"])["spec"]["ports"] == [
        {"name": "http", "port": 8201, "targetPort": "http"}
    ]
    assert "postgresql://application:" in bundle.files["Dockerfile"]
    assert "service.migrate" in bundle.files["Dockerfile"]
    database["args"][-2:] = ["-p", "9999"]
    altered = bundle.files | {path: yaml.safe_dump(deployment)}
    with pytest.raises(ManifestPolicyError, match="deployment_policy_denied"):
        checked_manifests(altered, digest=canonical_digest(altered), **kwargs)
    intent["connectivity"]["replicas"] = 2
    with pytest.raises(PlanError, match="approved_postgres_binding_mismatch"):
        render(assessment, intent, profile)


def test_reviewed_pr_policy_only_permits_the_original_merge():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from app.deployments.queue import source_scope_permits
    from app.integrations.github.compatibility import REVIEWED_PR, policy_permits

    db = Mock()
    db.get.return_value = SimpleNamespace(head_sha="a" * 40, merged_sha="b" * 40, plan_id="plan")
    policy = SimpleNamespace(source_compatibility_policy=REVIEWED_PR, allowed_source_sha="a" * 40)
    release = SimpleNamespace(source_sha="b" * 40, merged_sha="b" * 40, plan_id="plan")
    build = SimpleNamespace(source_sha="b" * 40, merged_sha="b" * 40, pull_request_id="pr")
    assert policy_permits(REVIEWED_PR, None)
    assert not policy_permits(REVIEWED_PR, {"policy": "node-source-compatibility-v1"})
    assert source_scope_permits(db, policy, release, build)
    release.source_sha = "c" * 40
    build.source_sha = "c" * 40
    assert not source_scope_permits(db, policy, release, build)
    release.source_sha = build.source_sha = "b" * 40
    db.get.return_value.head_sha = "d" * 40
    assert not source_scope_permits(db, policy, release, build)


@pytest.mark.parametrize(("managed", "token_secret", "connection_secret"), [
    (False, None, None),
    (False, "queue-api-secret", None),
    (True, None, None),
    (False, None, "redis-run-01"),
    (False, "queue-api-secret", "redis-run-01"),
])
def test_redis_worker_and_verified_image_binding(tmp_path, monkeypatch, managed, token_secret, connection_secret):

    from app.bootstrap import redis
    from app.deployments.release import bind_verified_image

    files = {
        **PYTHON,
        "requirements.txt": PYTHON["requirements.txt"] + "redis==5.2.1\ncelery==5.5.3\n",
        "README.md": PYTHON["README.md"] + "Run `celery -A service.worker.celery worker --loglevel=warning --concurrency=1`.\n",
        "service/main.py": PYTHON["service/main.py"] + '@app.get("/health/live")\ndef live(): return {}\n',
        "service/worker.py": (
            "import os\nfrom celery import Celery\n"
            'celery = Celery("demo", broker=os.environ["CELERY_BROKER_URL"], backend=os.environ["CELERY_RESULT_BACKEND"])\n'
            'queue = os.environ["QUEUE_NAMESPACE"]\n'
        ),
    }
    if token_secret:
        files["service/main.py"] += 'import os\nexpected = os.environ.get("API_TOKEN")\n'
    assessment = scan(tmp_path, files)
    profile = PROFILE | {"application_id": "4374b5cc-a306-4fda-ab0b-560d8d6ed1a5"}
    assert assessment["workflow"]["worker_target"] == "service.worker:celery"
    assert "QUEUE_NAMESPACE" in assessment["configuration"]["required_variables"]
    assert recommend(assessment, profile, "main", 1)["generation_blocker"]
    value = {
        "application_id": profile["application_id"], "application_name": "demo", "repository": "example/demo",
        "source_sha": "a" * 40, "worker_target": "service.worker:celery", "api_token_secret": token_secret,
        "redis_connection_secret": connection_secret,
    }
    if managed:
        monkeypatch.setenv("DF_REDIS_PILOT_REPOSITORIES", json.dumps({"example/demo": "service.worker:celery"}))
        with monkeypatch.context() as obsolete_binding:
            obsolete_binding.setenv("DF_APPROVED_REDIS_BINDINGS", json.dumps([value | {"source_sha": "c" * 40}]))
            assert redis.redis_for_source(assessment, profile["application_id"]) is None
        another_id = "7be41672-22c3-4ac8-81e9-314292916555"
        another_run = assessment | {
            "application": assessment["application"] | {"name": "another-run"},
            "repository": assessment["repository"] | {"commit_sha": "b" * 40},
        }
        assert redis.redis_for_source(another_run, another_id).environment() == {
            "CELERY_BROKER_URL": "redis://127.0.0.1:6379/0",
            "CELERY_RESULT_BACKEND": "redis://127.0.0.1:6379/1",
            "QUEUE_NAMESPACE": "q-" + another_id.replace("-", ""),
            redis.REDIS_SOURCE_KEY: "b" * 40,
        }
        for update in (
            {"repository": assessment["repository"] | {"name": "another-repo"}},
            {"workflow": assessment["workflow"] | {"worker_target": "other.worker:celery"}},
            {"configuration": {"required_variables": list(redis.REDIS_REQUIREMENTS | {"API_TOKEN"})}},
        ):
            assert redis.redis_for_source(assessment | update, profile["application_id"]) is None
    else:
        monkeypatch.setenv("DF_APPROVED_REDIS_BINDINGS", json.dumps([value]))
    result = recommend(assessment, profile, "main", 1, application_id=profile["application_id"])
    assert not result["generation_blocker"]
    intent = result["intent"]
    if connection_secret:
        # Each approved app gets a distinct queue scope even when the broker host is shared.
        other_id = "7be41672-22c3-4ac8-81e9-314292916555"
        other_binding = value | {"application_id": other_id, "application_name": "another-run"}
        monkeypatch.setenv("DF_APPROVED_REDIS_BINDINGS", json.dumps([value, other_binding]))
        other_assessment = assessment | {"application": assessment["application"] | {"name": "another-run"}}
        first = redis.redis_for_source(assessment, profile["application_id"])
        second = redis.redis_for_source(other_assessment, other_id)
        assert first.environment()["QUEUE_NAMESPACE"] != second.environment()["QUEUE_NAMESPACE"]
        moved = assessment | {"repository": assessment["repository"] | {"commit_sha": "b" * 40}}
        assert redis.redis_for_source(moved, profile["application_id"]) is None

        intent["connectivity"]["environment"]["CELERY_BROKER_URL"] = "unapproved"
        with pytest.raises(PlanError, match="approved_redis_binding_mismatch"):
            render(assessment, intent, profile)
        del intent["connectivity"]["environment"]["CELERY_BROKER_URL"]
    bundle = render(assessment, intent, profile)
    release = bind_verified_image(
        bundle_files=bundle.files, bundle_digest=bundle.digest, image_binding=bundle.validation["image_binding"],
        image_repository=intent["build"]["image"], image_digest="sha256:" + "e" * 64,
        profile=profile, application_id=profile["application_id"],
    )
    path = "deploy/kubernetes/deployment.yaml"
    manifest = yaml.safe_load(release.files[path])
    pod = manifest["spec"]["template"]["spec"]
    web, worker = pod["containers"]
    assert [web["image"], worker["image"]] == [release.image_reference] * 2
    assert worker["command"][:4] == ["celery", "-A", "service.worker:celery", "worker"]
    if connection_secret:
        assert not pod.get("initContainers")
        refs = [
            {"name": key, "valueFrom": {"secretKeyRef": {"name": connection_secret, "key": key}}}
            for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
        ]
        assert worker["env"] == refs
        assert web["env"][:2] == refs
        if token_secret:
            assert web["env"][2] == {
                "name": "API_TOKEN", "valueFrom": {"secretKeyRef": {"name": token_secret, "key": "API_TOKEN"}},
            }
        else:
            assert web["env"] == refs

        config_path = "deploy/kubernetes/configmap.yaml"
        config = yaml.safe_load(release.files[config_path])
        assert all(entry["name"] not in config["data"] for entry in refs)
        assert config["data"]["QUEUE_NAMESPACE"] == "q-" + profile["application_id"].replace("-", "")
        config["data"]["CELERY_RESULT_BACKEND"] = "unapproved"
        tampered = release.files | {config_path: yaml.safe_dump(config)}
        with pytest.raises(ManifestPolicyError, match="redis_binding_denied"):
            checked_manifests(
                tampered, digest=canonical_digest(tampered), namespace=profile["namespace"],
                application_id=profile["application_id"], registry=profile["registry"],
            )

        worker["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "different-secret"
        tampered = release.files | {path: yaml.safe_dump(manifest)}
        with pytest.raises(ManifestPolicyError, match="redis_binding_denied"):
            checked_manifests(
                tampered, digest=canonical_digest(tampered), namespace=profile["namespace"],
                application_id=profile["application_id"], registry=profile["registry"],
            )
        pod["containers"] = [{"name": web["name"], **{k: v for k, v in web.items() if k != "env"}}]
        tampered = release.files | {path: yaml.safe_dump(manifest)}
        with pytest.raises(ManifestPolicyError, match="deployment_policy_denied"):
            checked_manifests(
                tampered, digest=canonical_digest(tampered), namespace=profile["namespace"],
                application_id=profile["application_id"], registry=profile["registry"],
            )
        return

    assert pod["initContainers"][0]["args"][2] == "127.0.0.1"
    assert token_secret is None or web["env"][0]["valueFrom"]["secretKeyRef"]["name"] == token_secret
    assert "env" not in worker
    if managed:
        config_path = "deploy/kubernetes/configmap.yaml"
        config = yaml.safe_load(bundle.files[config_path])
        assert config["data"][redis.REDIS_SOURCE_KEY] == "a" * 40
        config["data"][redis.REDIS_SOURCE_KEY] = "c" * 40
        tampered = bundle.files | {config_path: yaml.safe_dump(config)}
        with pytest.raises(ManifestPolicyError, match="redis_binding_denied"):
            checked_manifests(
                tampered, digest=canonical_digest(tampered), namespace=profile["namespace"],
                application_id=profile["application_id"], registry=profile["registry"],
            )
    redis_container = pod["initContainers"][0]
    valid_image = redis_container["image"]
    redis_container["image"] = valid_image[:-2]
    with monkeypatch.context() as invalid_pin:
        invalid_pin.setattr(redis, "REDIS_IMAGE", redis_container["image"])
        files = release.files | {path: yaml.safe_dump(manifest)}
        with pytest.raises(ManifestPolicyError, match="container_image_reference_invalid"):
            checked_manifests(
                files, digest=canonical_digest(files), namespace=profile["namespace"],
                application_id=profile["application_id"], registry=profile["registry"],
            )
    redis_container["image"] = valid_image
    pod["containers"][1]["command"] = ["sh", "-c", "true"]
    files = release.files | {path: yaml.safe_dump(manifest)}
    with pytest.raises(ManifestPolicyError, match="redis_binding_denied"):
        checked_manifests(
            files, digest=canonical_digest(files), namespace=profile["namespace"],
            application_id=profile["application_id"], registry=profile["registry"],
        )


def test_application_names_can_be_generated_from_repository_and_unique_identity():
    application_id = UUID("4374b5cc-a306-4fda-ab0b-560d8d6ed1a5")
    another_id = UUID("7be41672-22c3-4ac8-81e9-314292916555")
    repository = "https://github.com/Example/01_app.Name"
    application = CreateApplication(repository_url=repository + ".git/", revision="release/next")
    assert application.repository_url == repository
    assert application.revision == "release/next"
    assert application.name_for(application_id) == "df-01-app-name-4374b5cca3064fda"
    assert application.name_for(application_id) != application.name_for(another_id)
    assert CreateApplication(repository_url=repository, name="custom").name_for(application_id) == "custom"
    long_name = CreateApplication(repository_url=repository + "a" * 100).name_for(application_id)
    assert len(long_name) == 63
    assert re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", long_name)
    with pytest.raises(ValidationError):
        CreateApplication(repository_url="https://github.com/Example/.git")


def test_node_runtime_uses_its_repository_and_blocks_missing_database(tmp_path):
    plain = NODE["package.json"]
    files = NODE | {
        "package.json": plain.replace('"scripts"', '"dependencies":{"pg":"^8"},"scripts"'),
        "src/index.ts": NODE["src/index.ts"] + '\nconst url = process.env.DATABASE_URL;\n',
    }
    assessment = scan(tmp_path, files)
    assert assessment["configuration"]["required_variables"] == ["DATABASE_URL"]
    result = recommend(assessment, PROFILE, "main", 1)
    assert result["intent"]["build"]["image"] == "ghcr.io/example/demo"
    assert result["generation_blocker"]
    with pytest.raises(PlanError, match="native_service_requires_configuration_bindings"):
        render(assessment, result["intent"], PROFILE)


@pytest.mark.parametrize("family,slug,source,migration", [
    ("nodejs", "demo", NODE | {
        "package.json": NODE["package.json"].replace('"start"', '"migrate":"node scripts/migrate.mjs","start"').replace('"scripts"', '"dependencies":{"pg":"8.16.3"},"scripts"'),
        "src/index.ts": NODE["src/index.ts"] + '\nconst url = process.env.DATABASE_URL;\n',
        "scripts/migrate.mjs": 'export const migrate = true;',
    }, "npm run migrate"),
    ("java", "demo", JAVA | {
        "pom.xml": JAVA["pom.xml"].replace(
            "<build>",
            "<dependencies>"
            + "".join(f"<dependency><artifactId>{item}</artifactId></dependency>" for item in (
                "spring-jdbc", "postgresql", "flyway-core", "flyway-database-postgresql",
            ))
            + "</dependencies><build>",
        ),
        "README.md": "Run `java -jar target/example-0.1.0.jar migrate`.",
        "src/main/java/example/Database.java": 'String url=System.getenv("DATABASE_URL");',
        "src/main/java/example/Application.java": 'if (args[0].equals("migrate")) Database.migrate();',
        "src/main/resources/db/migration/V1__data.sql": "SELECT 1;",
    }, "java -jar target/example-0.1.0.jar migrate"),
    ("ruby", "demo", RAILS | {
        "Gemfile.lock": RAILS["Gemfile.lock"] + "\nRUBY VERSION\n   ruby 3.3.10p183\n\nBUNDLED WITH\n   2.5.22\n",
    }, "bundle exec rails db:migrate"),
])
def test_postgres_source_migration_is_packaged_for_each_runtime(tmp_path, monkeypatch, family, slug, source, migration):
    import json

    profile = PROFILE | {"application_id": "43cfca1e-0d9a-4c38-bf9d-8d3a2132ea9c"}
    assignment = {
        "application_id": profile["application_id"], "application_name": slug,
        "repository": "example/demo", "source_sha": "a" * 40, "secret_name": "native-db",
        "runtime": family, "liveness_path": "/health/live", "ephemeral": True,
    }
    monkeypatch.setenv("DF_APPROVED_POSTGRES_BINDINGS", json.dumps([assignment]))
    assessment = scan(tmp_path, source)
    assert assessment["workflow"]["migration_command"] == migration
    result = recommend(assessment, profile, "main", 1, application_id=profile["application_id"])
    assert not result["generation_blocker"]
    bundle = render(assessment, result["intent"], profile)
    checked_manifests(
        bundle.files, digest=bundle.digest, namespace=profile["namespace"],
        application_id=profile["application_id"], registry=profile["registry"],
    )
    launch = json.loads(bundle.files["Dockerfile"].rsplit("CMD ", 1)[1])
    assert launch[:2] == ["sh", "-ec"]
    assert f"timeout 60 {migration}" in launch[2]
    assert "127.0.0.1:5432" in launch[2]
    pod = yaml.safe_load(bundle.files["deploy/kubernetes/deployment.yaml"])["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["env"][-1]["name"] == "POSTGRES_PASSWORD"
    assert ("SECRET_KEY_BASE" in {entry["name"] for entry in pod["containers"][0]["env"]}) == (family == "ruby")
