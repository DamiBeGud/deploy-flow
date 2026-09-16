import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.analysis.contracts import InfrastructureAssessmentV2
from app.repository.archive import excerpt_hash

IGNORED = {".git", "node_modules", "vendor", ".venv", "dist", "build", "coverage"}
SENSITIVE = {".env", ".pem", ".key", "id_rsa"}


@dataclass
class Evidence:
    id: str
    file_path: str
    line_start: int | None
    line_end: int | None
    excerpt_hash: str
    detector: str

    def document(self):
        return self.__dict__.copy()


@dataclass
class ScanResult:
    document: dict
    facts: dict


class Scanner:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.evidence: list[Evidence] = []

    def _add_evidence(self, path: Path, line: int | None, excerpt: str, detector: str) -> str:
        identifier = f"ev-{len(self.evidence) + 1}"
        self.evidence.append(
            Evidence(
                identifier,
                path.relative_to(self.root).as_posix(),
                line,
                line,
                excerpt_hash(excerpt),
                detector,
            )
        )
        return identifier

    def _files(self):
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or any(
                part in IGNORED for part in path.relative_to(self.root).parts
            ):
                continue
            if path.name in SENSITIVE or path.suffix in {".pem", ".key"}:
                continue
            yield path

    def scan(self, *, repository: dict, application_name: str) -> ScanResult:
        paths = list(self._files())
        names = {p.relative_to(self.root).as_posix() for p in paths}
        languages = []
        if any(p.suffix in {".js", ".mjs", ".cjs"} for p in paths):
            languages.append("javascript")
        package = self._package()
        findings = []

        def finding(category, value, status, confidence, evidence_ids):
            findings.append(
                {
                    "id": f"finding-{len(findings) + 1}",
                    "category": category,
                    "value": value,
                    "status": status,
                    "confidence": confidence,
                    "evidence_ids": evidence_ids,
                }
            )

        delivery = {
            "dockerfile_required": "Dockerfile" not in names,
            "workflow_required": not any(p.startswith(".github/workflows/") for p in names),
            "manifest_kinds": [],
            "generated_paths": [],
        }
        if package:
            package_path, manifest = package
            manifest_text = package_path.read_text(encoding="utf-8", errors="replace")
            package_ev = self._add_evidence(package_path, 1, manifest_text, "node.package-json.v1")
            engines = (
                manifest.get("engines", {}) if isinstance(manifest.get("engines"), dict) else {}
            )
            scripts = (
                manifest.get("scripts", {}) if isinstance(manifest.get("scripts"), dict) else {}
            )
            dependencies = {
                **(manifest.get("dependencies") or {}),
                **(manifest.get("devDependencies") or {}),
            }
            is_node = (
                "node" in engines
                or bool(scripts)
                or any(name in dependencies for name in ("express", "fastify", "koa"))
            )
            if is_node:
                finding("runtime", "nodejs", "explicit", 0.99, [package_ev])
                if engines.get("node"):
                    finding("runtime.version", engines["node"], "explicit", 0.99, [package_ev])
                if "express" in dependencies:
                    finding("framework", "express", "explicit", 0.99, [package_ev])
                if "package-lock.json" in names:
                    finding("workflow.install_command", "npm ci", "explicit", 0.98, [package_ev])
                for key, category in (
                    ("start", "runtime.command"),
                    ("test", "workflow.test_command"),
                    ("build", "workflow.build_command"),
                ):
                    if key in scripts:
                        finding(category, f"npm {key}", "explicit", 0.99, [package_ev])
                for dependency, kind in (
                    ("pg", "postgresql"),
                    ("redis", "redis"),
                    ("ioredis", "redis"),
                    ("amqplib", "rabbitmq"),
                    ("bullmq", "queue"),
                ):
                    if dependency in dependencies:
                        finding(f"dependencies.{kind}", dependency, "explicit", 0.95, [package_ev])

        env_names: set[str] = set()
        ports: list[int] = []
        health: list[str] = []
        for path in paths:
            relative_parts = path.relative_to(self.root).parts
            if (
                "test" in relative_parts
                or "tests" in relative_parts
                or path.name.endswith(".test.js")
            ):
                continue
            if (
                path.suffix not in {".js", ".mjs", ".cjs", ".ts"}
                or path.stat().st_size > 512 * 1024
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            for number, line in enumerate(lines, start=1):
                names_found = re.findall(r"process\.env\.([A-Z][A-Z0-9_]*)", line)
                for name in names_found:
                    env_names.add(name)
                    finding(
                        "configuration.variable",
                        name,
                        "inferred",
                        0.95,
                        [self._add_evidence(path, number, line, "node.env.v1")],
                    )
                route = re.search(
                    r"\.(?:get|head)\(\s*['\"]([^'\"]*(?:health|ready|live)[^'\"]*)", line, re.I
                )
                if route:
                    health.append(route.group(1))
                    finding(
                        "health_check",
                        route.group(1),
                        "inferred",
                        0.9,
                        [self._add_evidence(path, number, line, "node.express-route.v1")],
                    )
                literal_port = re.search(r"\.listen\(\s*(\d{1,5})", line)
                if literal_port:
                    ports.append(int(literal_port.group(1)))
                    finding(
                        "network.port",
                        int(literal_port.group(1)),
                        "explicit",
                        0.9,
                        [self._add_evidence(path, number, line, "node.listen.v1")],
                    )
                default_port = re.search(r"(?:DEFAULT_)?PORT\s*=\s*(\d{1,5})", line)
                if default_port:
                    ports.append(int(default_port.group(1)))
                    finding(
                        "network.port",
                        int(default_port.group(1)),
                        "inferred",
                        0.85,
                        [self._add_evidence(path, number, line, "node.port-default.v1")],
                    )

        node_supported = any(
            f["category"] == "runtime" and f["value"] == "nodejs" for f in findings
        )
        unknowns = (
            []
            if node_supported
            else [
                "No supported Node/npm service was detected; only generic repository inventory is available."
            ]
        )
        if not node_supported:
            findings.append(
                {
                    "id": "finding-1",
                    "category": "workload_type",
                    "value": "unknown",
                    "status": "unknown",
                    "confidence": 0.0,
                    "evidence_ids": [],
                }
            )
        document = {
            "schema_version": "infrastructure-assessment-v2",
            "mode": "deterministic",
            "completion": "complete" if node_supported else "partial",
            "incomplete_reason": None if node_supported else "unsupported_stack",
            "repository": repository,
            "application": {
                "name": application_name,
                "workload_type": "web_service" if node_supported else "unknown",
            },
            "detected_stack": {
                "languages": languages,
                "frameworks": ["express"] if any(f["value"] == "express" for f in findings) else [],
            },
            "build": {
                "install_command": "npm ci"
                if "package-lock.json" in names and node_supported
                else None,
                "artifact": None,
            },
            "runtime": {
                "name": "nodejs" if node_supported else None,
                "command": "npm start"
                if any(f["category"] == "runtime.command" for f in findings)
                else None,
                "version": next(
                    (f["value"] for f in findings if f["category"] == "runtime.version"), None
                ),
            },
            "review": {
                "runtime": "nodejs" if node_supported else "unknown",
                "port": min(ports) if ports else None,
                "health_path": health[0] if health else None,
            },
            "network": {"ports": sorted(set(ports))},
            "dependencies": {"databases": [], "caches": [], "queues": [], "external_services": []},
            "configuration": {"required_variables": [], "optional_variables": sorted(env_names)},
            "storage": [],
            "health_checks": sorted(set(health)),
            "workflow": {
                "test_command": "npm test"
                if any(f["category"] == "workflow.test_command" for f in findings)
                else None,
                "build_command": "npm build"
                if any(f["category"] == "workflow.build_command" for f in findings)
                else None,
            },
            "bootstrap": delivery,
            "risks": [],
            "conflicts": [],
            "unknowns": unknowns,
            "questions": [],
            "findings": findings,
            "evidence": [item.document() for item in self.evidence],
            "summary": "Deterministic repository scan; no application code was executed.",
        }
        validated = InfrastructureAssessmentV2.model_validate(document).model_dump()
        return ScanResult(
            document=validated,
            facts={
                "schema_version": "analysis-facts-v1",
                "paths": sorted(names),
                "languages": languages,
                "repository": repository,
                "scanner_version": "phase3-v1",
                "document": validated,
            },
        )

    def _package(self):
        path = self.root / "package.json"
        if not path.is_file():
            return None
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
