"""Phase 2 read-only Repository MCP server.

The host supplies the workspace and trusted metadata through the environment;
tool callers can never select a filesystem root.
"""
import json
import os
from pathlib import Path

from mcp.server import MCPServer

from app.repository.scanner import IGNORED, SENSITIVE

ROOT = Path(os.environ["DF_REPOSITORY_WORKSPACE"]).resolve()
METADATA = json.loads(os.environ["DF_REPOSITORY_METADATA"])
mcp = MCPServer("deployflow-repository")


def safe(path: str) -> Path:
    candidate = (ROOT / path).resolve()
    if candidate != ROOT and ROOT not in candidate.parents:
        raise ValueError("invalid_path")
    relative = candidate.relative_to(ROOT)
    if any(part in IGNORED or part == ".git" for part in relative.parts) or candidate.name in SENSITIVE or candidate.suffix in {".pem", ".key"}:
        raise ValueError("path_denied")
    return candidate


@mcp.tool(name="repo.list_tree")
def list_tree(path: str = ".", depth: int = 2) -> dict:
    if not 1 <= depth <= 4:
        raise ValueError("invalid_depth")
    base = safe(path)
    rows = []
    for item in sorted(base.rglob("*")):
        relative = item.relative_to(base)
        if len(relative.parts) > depth or any(part in IGNORED for part in relative.parts):
            continue
        rows.append({"path": relative.as_posix(), "kind": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None})
        if len(rows) >= 500:
            return {"entries": rows, "truncated": True}
    return {"entries": rows, "truncated": False}


@mcp.tool(name="repo.read_file")
def read_file(path: str, start_line: int = 1, end_line: int = 500) -> dict:
    if start_line < 1 or end_line < start_line or end_line - start_line >= 500:
        raise ValueError("invalid_range")
    file = safe(path)
    data = file.read_bytes()
    if b"\0" in data:
        raise ValueError("binary_file")
    text = data.decode("utf-8")
    lines = text.splitlines()
    selected = "\n".join(lines[start_line - 1:end_line])
    if len(selected.encode()) > 64 * 1024:
        selected = selected.encode()[:64 * 1024].decode("utf-8", "ignore")
    return {"path": path, "start_line": start_line, "end_line": min(end_line, len(lines)), "text": selected}


@mcp.tool(name="repo.search_text")
def search_text(literal: str, include_patterns: list[str] | None = None) -> dict:
    if not literal or len(literal) > 256:
        raise ValueError("invalid_query")
    matches = []
    for file in ROOT.rglob("*"):
        if not file.is_file() or any(part in IGNORED for part in file.relative_to(ROOT).parts):
            continue
        if include_patterns and not any(file.match(pattern) for pattern in include_patterns[:10]):
            continue
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, 1):
            if literal in line:
                matches.append({"path": file.relative_to(ROOT).as_posix(), "line": number, "text": line[:512]})
                if len(matches) >= 100:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


@mcp.tool(name="repo.read_manifest")
def read_manifest(path: str) -> dict:
    file = safe(path)
    if file.name not in {"package.json", "package-lock.json"}:
        raise ValueError("unsupported_manifest")
    return {"path": path, "manifest": json.loads(file.read_text(encoding="utf-8"))}


@mcp.tool(name="repo.get_git_metadata")
def get_git_metadata() -> dict:
    return METADATA


if __name__ == "__main__":
    mcp.run()
