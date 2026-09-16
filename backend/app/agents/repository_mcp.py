"""Host-controlled stdio client for the project-owned repository MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.repository.archive import excerpt_hash

TOOL_MAP = {
    "repo_list_tree": "repo.list_tree",
    "repo_read_file": "repo.read_file",
    "repo_search_text": "repo.search_text",
    "repo_read_manifest": "repo.read_manifest",
    "repo_get_git_metadata": "repo.get_git_metadata",
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "name": "repo_list_tree",
        "description": "List a bounded repository tree.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}},
            "required": ["path", "depth"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "repo_read_file",
        "description": "Read a bounded text-file line range.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path", "start_line", "end_line"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "repo_search_text",
        "description": "Find a bounded literal text search.",
        "parameters": {
            "type": "object",
            "properties": {
                "literal": {"type": "string"},
                "include_patterns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["literal", "include_patterns"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "repo_read_manifest",
        "description": "Read a supported dependency manifest.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "repo_get_git_metadata",
        "description": "Return trusted pinned repository metadata.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]


class ToolGatewayError(Exception):
    pass


def _redact(value):
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        # Do not present obvious inline credentials to the model or persist them.
        import re

        return re.sub(
            r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s'\"]+)",
            r"\1[REDACTED]",
            value,
        )
    return value


def _arguments(name: str, arguments: object) -> dict:
    if name not in TOOL_MAP or not isinstance(arguments, dict):
        raise ToolGatewayError("tool_denied")
    expected = {
        "repo_list_tree": {"path", "depth"},
        "repo_read_file": {"path", "start_line", "end_line"},
        "repo_search_text": {"literal", "include_patterns"},
        "repo_read_manifest": {"path"},
        "repo_get_git_metadata": set(),
    }[name]
    if set(arguments) != expected:
        raise ToolGatewayError("tool_arguments_invalid")
    if any(isinstance(value, (dict, tuple)) for value in arguments.values()):
        raise ToolGatewayError("tool_arguments_invalid")
    return arguments


@dataclass
class GatewayResult:
    output: dict
    evidence: list[dict]
    safe_arguments: dict
    latency_ms: int


class RepositoryGateway:
    def __init__(
        self,
        session: ClientSession,
        *,
        allowed_tools: set[str],
        output_budget: int,
        tool_timeout_seconds: int,
    ):
        self.session = session
        self.allowed_tools = allowed_tools
        self.output_budget = output_budget
        self.tool_timeout_seconds = tool_timeout_seconds
        self.used_bytes = 0
        self.evidence_counter = 0

    async def execute(self, name: str, arguments: object) -> GatewayResult:
        parsed = _arguments(name, arguments)
        mcp_name = TOOL_MAP[name]
        if mcp_name not in self.allowed_tools:
            raise ToolGatewayError("tool_denied")
        started = monotonic()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(mcp_name, parsed), timeout=self.tool_timeout_seconds
            )
        except Exception as exc:
            raise ToolGatewayError("tool_failed") from exc
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            texts = [getattr(item, "text", "") for item in getattr(result, "content", [])]
            try:
                structured = json.loads("\n".join(texts))
            except json.JSONDecodeError:
                raise ToolGatewayError("tool_invalid_result") from None
        safe = _redact(structured)
        encoded = len(json.dumps(safe, sort_keys=True).encode())
        if self.used_bytes + encoded > self.output_budget:
            raise ToolGatewayError("tool_output_budget_exceeded")
        self.used_bytes += encoded
        return GatewayResult(
            safe,
            self._evidence(name, structured),
            _redact(parsed),
            int((monotonic() - started) * 1000),
        )

    def _evidence(self, name: str, raw: dict) -> list[dict]:
        evidence = []

        def add(path: str, start: int | None, end: int | None, excerpt: str, detector: str):
            self.evidence_counter += 1
            evidence.append(
                {
                    "id": f"ev-agent-{self.evidence_counter}",
                    "file_path": path,
                    "line_start": start,
                    "line_end": end,
                    "excerpt_hash": excerpt_hash(excerpt),
                    "detector": detector,
                }
            )

        if name == "repo_read_file" and isinstance(raw.get("text"), str):
            add(
                raw["path"],
                raw.get("start_line"),
                raw.get("end_line"),
                raw["text"],
                "repository-mcp.read-file.v1",
            )
        elif name == "repo_read_manifest":
            add(
                raw["path"],
                1,
                1,
                json.dumps(raw.get("manifest"), sort_keys=True),
                "repository-mcp.read-manifest.v1",
            )
        elif name == "repo_search_text":
            for match in raw.get("matches", []):
                add(
                    match["path"],
                    match["line"],
                    match["line"],
                    match.get("text", ""),
                    "repository-mcp.search-text.v1",
                )
        return evidence


@asynccontextmanager
async def repository_gateway(
    workspace: Path,
    metadata: dict,
    *,
    allowed_tools: set[str],
    read_limit: int,
    result_limit: int,
    output_budget: int,
    tool_timeout_seconds: int = 20,
):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "DF_REPOSITORY_WORKSPACE": str(workspace),
        "DF_REPOSITORY_METADATA": json.dumps(metadata, sort_keys=True),
        "DF_REPOSITORY_READ_BYTES": str(read_limit),
        "DF_REPOSITORY_RESULT_BUDGET": str(result_limit),
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_servers.repository_server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            offered = await session.list_tools()
            names = {tool.name for tool in offered.tools}
            if not set(TOOL_MAP.values()).issubset(names):
                raise ToolGatewayError("repository_mcp_schema_mismatch")
            yield RepositoryGateway(
                session,
                allowed_tools=allowed_tools,
                output_budget=output_budget,
                tool_timeout_seconds=tool_timeout_seconds,
            )
