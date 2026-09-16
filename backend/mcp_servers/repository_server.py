"""Phase 2 read-only Repository MCP server.

The host supplies the workspace and trusted metadata through the environment;
tool callers can never select a filesystem root.
"""

import json
import os
from pathlib import Path

from mcp.server import MCPServer

from app.repository.tools import RepositoryTools

ROOT = Path(os.environ["DF_REPOSITORY_WORKSPACE"]).resolve()
METADATA = json.loads(os.environ["DF_REPOSITORY_METADATA"])
mcp = MCPServer("deployflow-repository")
tools = RepositoryTools(
    ROOT,
    METADATA,
    read_limit=int(os.environ.get("DF_REPOSITORY_READ_BYTES", 64 * 1024)),
    result_limit=int(os.environ.get("DF_REPOSITORY_RESULT_BUDGET", 2 * 1024 * 1024)),
)


@mcp.tool(name="repo.list_tree")
def list_tree(path: str = ".", depth: int = 2) -> dict:
    return tools.list_tree(path, depth)


@mcp.tool(name="repo.read_file")
def read_file(path: str, start_line: int = 1, end_line: int = 500) -> dict:
    return tools.read_file(path, start_line, end_line)


@mcp.tool(name="repo.search_text")
def search_text(literal: str, include_patterns: list[str] | None = None) -> dict:
    return tools.search_text(literal, include_patterns)


@mcp.tool(name="repo.read_manifest")
def read_manifest(path: str) -> dict:
    return tools.read_manifest(path)


@mcp.tool(name="repo.get_git_metadata")
def get_git_metadata() -> dict:
    return tools.get_git_metadata()


if __name__ == "__main__":
    mcp.run()
