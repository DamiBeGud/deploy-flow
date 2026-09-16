"""Bounded, read-only repository operations shared by scanners and MCP."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from app.repository.scanner import IGNORED, SENSITIVE


class RepositoryTools:
    def __init__(self, root: Path, metadata: dict, *, read_limit: int, result_limit: int):
        self.root = root.resolve()
        self.metadata = metadata
        self.read_limit = read_limit
        self.result_limit = result_limit

    def safe(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("invalid_path")
        relative = candidate.relative_to(self.root)
        if any(part in IGNORED or part == ".git" for part in relative.parts):
            raise ValueError("path_denied")
        if candidate.name in SENSITIVE or candidate.suffix in {".pem", ".key"}:
            raise ValueError("path_denied")
        return candidate

    def list_tree(self, path: str = ".", depth: int = 2) -> dict:
        if not 1 <= depth <= 4:
            raise ValueError("invalid_depth")
        base = self.safe(path)
        if not base.is_dir():
            raise ValueError("not_directory")
        rows, size = [], 0
        for item in sorted(base.rglob("*")):
            try:
                relative = item.relative_to(base)
                self.safe(item.relative_to(self.root).as_posix())
            except (ValueError, OSError):
                continue
            if len(relative.parts) > depth:
                continue
            row = {
                "path": relative.as_posix(),
                "kind": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            }
            encoded = len(json.dumps(row))
            if len(rows) >= 500 or size + encoded > self.result_limit:
                return {"entries": rows, "truncated": True}
            rows.append(row)
            size += encoded
        return {"entries": rows, "truncated": False}

    def read_file(self, path: str, start_line: int = 1, end_line: int = 500) -> dict:
        if start_line < 1 or end_line < start_line or end_line - start_line >= 500:
            raise ValueError("invalid_range")
        file = self.safe(path)
        if not file.is_file() or file.stat().st_size > self.read_limit:
            raise ValueError("file_too_large")
        data = file.read_bytes()
        if b"\0" in data:
            raise ValueError("binary_file")
        text = data.decode("utf-8")
        lines = text.splitlines()
        selected = "\n".join(lines[start_line - 1 : end_line])
        encoded = selected.encode()
        truncated = len(encoded) > self.read_limit
        if truncated:
            selected = encoded[: self.read_limit].decode("utf-8", "ignore")
        return {
            "path": path,
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "text": selected,
            "truncated": truncated,
        }

    def search_text(self, literal: str, include_patterns: list[str] | None = None) -> dict:
        if not literal or len(literal) > 256:
            raise ValueError("invalid_query")
        patterns = include_patterns or []
        if len(patterns) > 10 or any(len(item) > 128 for item in patterns):
            raise ValueError("invalid_patterns")
        matches, size = [], 0
        for file in sorted(self.root.rglob("*")):
            if not file.is_file():
                continue
            try:
                relative = file.relative_to(self.root).as_posix()
                self.safe(relative)
            except (ValueError, OSError):
                continue
            if patterns and not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                continue
            if file.stat().st_size > self.read_limit:
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                if literal not in line:
                    continue
                row = {"path": relative, "line": number, "text": line[:512]}
                encoded = len(json.dumps(row))
                if len(matches) >= 100 or size + encoded > self.result_limit:
                    return {"matches": matches, "truncated": True}
                matches.append(row)
                size += encoded
        return {"matches": matches, "truncated": False}

    def read_manifest(self, path: str) -> dict:
        file = self.safe(path)
        if file.name not in {"package.json", "package-lock.json"}:
            raise ValueError("unsupported_manifest")
        if not file.is_file() or file.stat().st_size > self.read_limit:
            raise ValueError("file_too_large")
        return {"path": path, "manifest": json.loads(file.read_text(encoding="utf-8"))}

    def get_git_metadata(self) -> dict:
        return self.metadata
