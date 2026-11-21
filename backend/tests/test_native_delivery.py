from pathlib import Path

import pytest
import yaml

from app.api.schemas import BuildSpecification
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
    assert assessment["review"] == {"runtime": family, "port": port, "health_path": probe}
    recommendation = recommend(assessment, PROFILE, "main", 1)
    intent = recommendation["intent"]
    build = BuildSpecification.model_validate(intent["build"])
    assert build.image == (
        "ghcr.io/example/demo" if family != "nodejs" else PROFILE["default_image"]
    )
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
