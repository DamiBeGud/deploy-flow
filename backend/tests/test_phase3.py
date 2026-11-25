import asyncio
import json

import httpx
import pytest

from app.agents.harness import AgentBudget, AgentError, run_agent
from app.agents.provider import (
    DeepSeekResponsesProvider,
    ModelAction,
    ModelProviderError,
    ModelUsage,
    ScriptedProvider,
    _provider_diagnostic,
)
from app.agents.repository_mcp import GatewayResult
from app.config import settings
from app.repository.scanner import Scanner
from app.repository.tools import RepositoryTools
from app.skills.registry import SkillRegistry


def fixture_scan(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "engines": {"node": ">=20"},
                "scripts": {"start": "node src/server.js", "test": "node --test"},
                "dependencies": {"express": "^5"},
            }
        )
    )
    source = tmp_path / "src"
    source.mkdir()
    (source / "server.js").write_text(
        "const PORT = 4587; app.get('/healthz', () => {}); server.listen(PORT); process.env.APP_GREETING;"
    )
    metadata = {
        "url": "https://github.com/example/fixture",
        "owner": "example",
        "name": "fixture",
        "revision": "main",
        "commit_sha": "a" * 40,
    }
    return Scanner(tmp_path).scan(repository=metadata, application_name="fixture"), metadata


def test_skill_registry_selects_reviewed_node_skills(tmp_path):
    scan, _ = fixture_scan(tmp_path)
    registry = SkillRegistry(
        settings().skill_directory, max_context_chars=settings().skill_context_chars
    ).load()
    selected = registry.select(scan.facts)
    assert [bundle.metadata.id for bundle in selected] == [
        "configuration-and-secrets",
        "containerization",
        "dependency-detection",
        "nodejs",
        "repository-discovery",
    ]
    assert all(len(bundle.content_hash) == 64 for bundle in selected)


def test_typed_scanner_keeps_node_evidence(tmp_path):
    scan, _ = fixture_scan(tmp_path)
    assert scan.document["schema_version"] == "infrastructure-assessment-v2"
    assert scan.document["review"] == {
        "runtime": "nodejs",
        "port": 4587,
        "health_path": "/healthz",
    }
    assert scan.document["evidence"]
    assert "package.json" in scan.facts["paths"]


def test_repository_tools_reject_traversal_and_sensitive_files(tmp_path):
    (tmp_path / "visible.txt").write_text("visible")
    (tmp_path / ".env").write_text("SECRET=value")
    tools = RepositoryTools(tmp_path, {}, read_limit=1024, result_limit=4096)
    assert tools.read_file("visible.txt")["text"] == "visible"
    with pytest.raises(ValueError, match="invalid_path"):
        tools.read_file("../outside")
    with pytest.raises(ValueError, match="path_denied"):
        tools.read_file(".env")


def test_deepseek_provider_refuses_unconfigured_credentials():
    provider = DeepSeekResponsesProvider(
        api_key=None,
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        reasoning_effort="high",
        timeout_seconds=5,
        max_output_tokens=1000,
    )
    with pytest.raises(ModelProviderError, match="model_auth_unconfigured"):
        asyncio.run(
            provider.respond(
                instructions="test",
                history=[],
                tools=[],
                tool_choice="auto",
                output_schema={},
            )
        )


def test_provider_diagnostic_is_short_and_redacts_credentials():
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": "invalid_parameter",
                "message": "text.format is invalid; token=not-for-logs",
            }
        },
    )
    diagnostic = _provider_diagnostic(response)
    assert "http_status=400" in diagnostic
    assert "invalid_parameter" in diagnostic
    assert "[REDACTED]" in diagnostic
    assert "not-for-logs" not in diagnostic


class FakeGateway:
    used_bytes = 64

    async def execute(self, name, arguments):
        assert name == "repo_read_manifest"
        assert arguments == {"path": "package.json"}
        return GatewayResult(
            {"path": "package.json", "manifest": {"name": "fixture"}},
            [
                {
                    "id": "ev-agent-1",
                    "file_path": "package.json",
                    "line_start": 1,
                    "line_end": 1,
                    "excerpt_hash": "b" * 64,
                    "detector": "test",
                }
            ],
            {"path": "package.json"},
            1,
        )


def test_harness_requires_repository_evidence_and_returns_needs_input(tmp_path):
    scan, _ = fixture_scan(tmp_path)
    registry = SkillRegistry(
        settings().skill_directory, max_context_chars=settings().skill_context_chars
    ).load()
    first = ModelAction(
        [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "repo_read_manifest",
                "arguments": '{"path":"package.json"}',
            }
        ],
        None,
        [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "repo_read_manifest",
                "arguments": '{"path":"package.json"}',
            }
        ],
        ModelUsage(10, 5, 1, "fake"),
    )
    draft = {
        "summary": "Verified package metadata.",
        "findings": [
            {
                "category": "workflow.install",
                "value": "npm ci",
                "status": "explicit",
                "confidence": 0.9,
                "evidence_ids": ["ev-agent-1"],
            }
        ],
        "questions": [
            {
                "field_path": "network.exposure",
                "prompt": "Should this service be externally exposed?",
                "reason": "The repository cannot determine exposure.",
                "required": True,
                "answer_type": "single_choice",
                "choices": ["internal", "external"],
            }
        ],
        "risks": [],
        "unknowns": [],
    }
    second = ModelAction(
        [],
        json.dumps(draft),
        [{"role": "assistant", "content": [{"type": "output_text", "text": json.dumps(draft)}]}],
        ModelUsage(10, 5, 1, "fake"),
    )
    outcome = asyncio.run(
        run_agent(
            provider=ScriptedProvider([first, second]),
            gateway=FakeGateway(),
            facts=scan.facts,
            skills=registry.select(scan.facts),
            budget=AgentBudget(8, 12, 120_000, 32_000, 180),
        )
    )
    assert outcome.status == "needs_input"
    assert outcome.document["mode"] == "agent"
    assert outcome.document["questions"][0]["id"] == "question-agent-1"
    assert outcome.document["findings"][-1]["evidence_ids"] == ["ev-agent-1"]

    provenance = outcome.provenance
    assert provenance["is_simulated"] is True and provenance["sent_settings"] == {}
    assert [record["status"] for record in provenance["provider_calls"]] == ["completed", "completed"]


def test_harness_retains_allowlisted_provider_receipts_on_failed_attempt(tmp_path, monkeypatch):
    scan, _ = fixture_scan(tmp_path)
    calls = []

    def response(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://api.deepseek.com/responses"
        if len(calls) == 1:
            return httpx.Response(200, json={
                "id": "test-turn-1", "model": "test-served", "usage": {"input_tokens": 0},
                "output": [{"type": "function_call", "call_id": "test-tool", "name": "repo_read_manifest",
                            "arguments": '{"path":"package.json"}'}],
            })
        return httpx.Response(200, json={
            "id": "test-turn-2", "status": "incomplete", "model": "test-served",
            "usage": {"input_tokens": -1, "output_tokens": 10}, "unretained": "not-part-of-receipt",
        })

    create_client = httpx.AsyncClient
    transport = httpx.MockTransport(response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: create_client(transport=transport, **kwargs))
    provider = DeepSeekResponsesProvider(
        api_key="fixture-auth-only", base_url="https://api.deepseek.com", model="test-requested",
        reasoning_effort="high", timeout_seconds=5, max_output_tokens=2000,
    )
    with pytest.raises(AgentError, match="model_incomplete") as raised:
        asyncio.run(run_agent(
            provider=provider, gateway=FakeGateway(), facts=scan.facts, skills=[],
            budget=AgentBudget(8, 12, 120_000, 32_000, 180),
        ))
    provenance = raised.value.provenance
    first, failed = provenance["provider_calls"]
    assert provenance["requested_model"] == "test-requested"
    assert provenance["sent_settings"] == {"reasoning_effort": "high", "max_output_tokens": 2000}
    assert all(body["reasoning"] == {"effort": "high"} and body["max_output_tokens"] == 2000 for body in calls)
    assert all(key not in body for key in provenance["not_sent_settings"] for body in calls)
    assert (first["response_id"], first["status"], first["input_tokens"], first["output_tokens"]) == (
        "test-turn-1", "completed", 0, None,
    )
    assert (failed["response_id"], failed["error_code"], failed["input_tokens"], failed["output_tokens"]) == (
        "test-turn-2", "model_incomplete", None, 10,
    )
    assert all(row["finished_at"] >= row["started_at"] for row in (first, failed))
    summary = json.dumps(provenance)
    assert "not-part-of-receipt" not in summary and "fixture-auth-only" not in summary and "package.json" not in summary
