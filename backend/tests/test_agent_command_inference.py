import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
import yaml
from test_native_delivery import PROFILE, PYTHON, scan

from app.agents.harness import AgentBudget, run_agent
from app.agents.provider import ModelAction, ModelUsage, ScriptedProvider
from app.agents.repository_mcp import RepositoryGateway
from app.analysis.command_inference import SourceExcerpt, infer_commands
from app.analysis.contracts import AssessmentDraftV3, InfrastructureAssessmentV2
from app.bootstrap.generator import PlanError, render
from app.bootstrap.recommendations import recommend
from app.repository.tools import RepositoryTools

GUNICORN = [
    "gunicorn",
    "service.main:app",
    "-k",
    "uvicorn_worker.UvicornWorker",
    "--bind",
    "0.0.0.0:9100",
]
UVICORN = ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8201"]
INSTALL = ["python", "-m", "pip", "install", "-r", "requirements.txt"]
TEST = ["python", "-m", "pytest"]
FILES = PYTHON | {
    "requirements.txt": PYTHON["requirements.txt"] + "gunicorn==23.0.0\nuvicorn-worker==0.4.0\n",
    "README.md": (
        "# Service\n\n## Install\n\nRun `"
        + " ".join(INSTALL)
        + "`.\n\n## Test\n\nRun `"
        + " ".join(TEST)
        + "`.\n\n## Development\n\nRun `"
        + " ".join(UVICORN)
        + "`.\n\n## Production\n\nRun `"
        + " ".join(GUNICORN)
        + "`.\n"
    ),
}


def candidate(argv, context="production", phase="start", ids=None):
    return {
        "phase": phase,
        "context": context,
        "runtime_family": "python",
        "working_directory": ".",
        "argv": argv,
        "evidence_ids": ids or ["ev-agent-1", "ev-agent-2", "ev-agent-3"],
    }


def draft(*commands):
    return AssessmentDraftV3.model_validate(
        {
            "summary": "Evidence-backed requirements",
            "findings": [
                {
                    "category": "health_check",
                    "status": "explicit",
                    "value": "/status",
                    "confidence": 1,
                    "evidence_ids": ["ev-agent-3"],
                }
            ],
            "command_candidates": list(commands),
        }
    )


def test_agent_command_roundtrips_provider_tools_recommendation_and_bundle(tmp_path):
    base = scan(tmp_path, FILES)
    assert base["runtime"]["command"] == " ".join(UVICORN)
    tools = RepositoryTools(tmp_path, base["repository"], read_limit=65536, result_limit=65536)

    class TestSession:
        async def call_tool(self, name, parameters):
            assert name == "repo.read_file"
            return SimpleNamespace(structuredContent=tools.read_file(**parameters))

    gateway = RepositoryGateway(
        TestSession(), allowed_tools={"repo.read_file"}, output_budget=16000, tool_timeout_seconds=2
    )
    calls = [
        {
            "name": "repo_read_file",
            "call_id": path,
            "arguments": json.dumps({"path": path, "start_line": 1, "end_line": 30}),
        }
        for path in ("README.md", "requirements.txt", "service/main.py")
    ]
    claims = draft(
        candidate(INSTALL, "unspecified", "install", ["ev-agent-1", "ev-agent-2"]),
        candidate(TEST, "unspecified", "test", ["ev-agent-1", "ev-agent-2"]),
        candidate(UVICORN, "development"),
        candidate(GUNICORN),
    )
    provider = ScriptedProvider(
        [
            ModelAction(calls, None, [], ModelUsage()),
            ModelAction([], claims.model_dump_json(), [], ModelUsage()),
        ]
    )
    outcome = asyncio.run(
        run_agent(
            provider=provider,
            gateway=gateway,
            facts={"document": base},
            skills=[],
            budget=AgentBudget(3, 5, 2000, 2000, 4),
            infer_delivery_commands=True,
        )
    )
    assert not outcome.response_ids and outcome.returned_model is None
    assert outcome.prompt_sha256
    assert "command_inference" not in base
    assessment = InfrastructureAssessmentV2.model_validate_json(
        json.dumps(outcome.document)
    ).model_dump()
    rec = recommend(assessment, PROFILE, "main", 1)
    intent = rec["intent"]
    assert not rec["generation_blocker"]
    assert intent["build"]["install_command"] == INSTALL
    assert intent["build"]["test_command"] == TEST
    assert intent["build"]["build_command"] is None
    assert intent["build"]["start_command"] == GUNICORN
    assert intent["connectivity"]["port"] == 9100
    assert intent["connectivity"]["health_path"] == "/status"
    fields = intent["field_recommendations"]
    assert fields["build.start_command"]["selector"] == "agent"
    assert fields["build.start_command"]["evidence"]["file_path"] == "README.md"
    assert fields["build.start_command"]["alternatives"][0]["context"] == "development"
    assert fields["connectivity.health_path"]["selector"] == "agent"
    bundle = render(assessment, intent, PROFILE)
    cmd = next(line for line in bundle.files["Dockerfile"].splitlines() if line.startswith("CMD "))
    assert json.loads(cmd[4:]) == GUNICORN
    service = yaml.safe_load(bundle.files["deploy/kubernetes/service.yaml"])
    assert service["spec"]["ports"][0]["port"] == 9100
    deployment = yaml.safe_load(bundle.files["deploy/kubernetes/deployment.yaml"])
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["fsGroup"] == 10001
    assert pod["volumes"] == [{"name": "runtime-tmp", "emptyDir": {"sizeLimit": "64Mi"}}]
    assert pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    assert pod["containers"][0]["volumeMounts"] == [{"name": "runtime-tmp", "mountPath": "/tmp"}]
    reordered = copy.deepcopy(intent)
    resources = reordered["connectivity"]["resources"]
    reordered["connectivity"]["resources"] = dict(reversed(resources.items()))
    assert render(assessment, reordered, PROFILE).digest == bundle.digest
    wrong_port = copy.deepcopy(intent)
    wrong_port["connectivity"]["port"] = 9101
    with pytest.raises(PlanError, match="gunicorn_command_port_must_match_service_port"):
        render(assessment, wrong_port, PROFILE)
    with pytest.raises(PlanError, match="gunicorn_requires_verified_source_and_dependencies"):
        render(base, intent, PROFILE)


@pytest.mark.parametrize(
    "case", ["conflict", "dependency", "fabricated", "foreign_evidence", "missing", "custom"]
)
def test_unverified_candidates_do_not_replace_production_declarations(tmp_path, case):
    files = dict(FILES)
    proposal = list(GUNICORN)
    ids = None
    if case == "conflict":
        files["README.md"] += "\nRun `" + " ".join(UVICORN) + "`.\n"
    elif case == "dependency":
        files["requirements.txt"] = PYTHON["requirements.txt"]
    elif case == "fabricated":
        proposal = [*GUNICORN[:-1], "0.0.0.0:9999"]
    elif case == "foreign_evidence":
        ids = ["ev-agent-1", "ev-agent-2", "ev-agent-3", "ev-foreign"]
    elif case == "custom":
        proposal = ["./start.sh", "--port", "9100"]
        files["README.md"] = files["README.md"].replace(" ".join(GUNICORN), " ".join(proposal))
    base = scan(tmp_path, files)
    claims = draft() if case == "missing" else draft(candidate(proposal, ids=ids))
    paths = ["README.md", "requirements.txt", "service/main.py"]
    excerpts = {
        f"ev-agent-{index}": SourceExcerpt(path, 1, files[path])
        for index, path in enumerate(paths, 1)
    }
    inference = infer_commands(base, claims, excerpts)
    assessment = base | {"mode": "agent", "command_inference": inference.model_dump()}
    assert inference.commands["start"].status == {
        "conflict": "conflict",
        "dependency": "unsupported",
        "custom": "unsupported",
    }.get(case, "missing")
    rec = recommend(assessment, PROFILE, "main", 1)
    assert rec["intent"]["build"]["start_command"] == []
    assert rec["intent"]["connectivity"]["port"] != 9100
    if case == "custom":
        start = rec["intent"]["field_recommendations"]["build.start_command"]
        assert start["alternatives"][0]["argv"] == proposal
    with pytest.raises(PlanError):
        render(assessment, rec["intent"], PROFILE)


def test_invalid_inline_syntax_is_rejected():
    with pytest.raises(ValueError):
        draft(candidate(["gunicorn", "service.main:app; harmless"]))


def test_legacy_draft_does_not_acquire_agent_recommendations(tmp_path):
    assessment = scan(tmp_path, FILES)
    assert "command_inference" not in assessment
    assert recommend(assessment, PROFILE, "main", 1)["intent"]["build"]["start_command"] == UVICORN
