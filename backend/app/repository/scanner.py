import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.analysis.contracts import InfrastructureAssessmentV2
from app.repository.archive import excerpt_hash
from app.repository.native import detect_native

IGNORED = {".git", "node_modules", "vendor", ".venv", "dist", "build", "coverage"}
SENSITIVE = {".env", ".pem", ".key", "id_rsa"}
SCANNER_VERSION = "phase3-v3"


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
        self.evidence.clear()
        paths = list(self._files())
        names = {p.relative_to(self.root).as_posix() for p in paths}
        languages = []
        if any(p.suffix in {".js", ".mjs", ".cjs"} for p in paths):
            languages.append("javascript")
        if any(p.suffix in {".ts", ".mts", ".cts"} for p in paths):
            languages.append("typescript")
        package = self._package()
        node_services: set[str] = set()
        node_required: set[str] = set()
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
        native, unsupported = detect_native(self.root, paths)
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
                migration_script = scripts.get("migrate")
                if (
                    isinstance(migration_script, str)
                    and (match := re.fullmatch(r"node ([A-Za-z0-9_/.-]+\.m?js)", migration_script))
                    and match[1] in names
                    and "pg" in dependencies
                ):
                    finding("workflow.migration_command", "npm run migrate", "explicit", 0.98, [package_ev])
                for key, category in (
                    ("start", "runtime.command"),
                    ("test", "workflow.test_command"),
                    ("build", "workflow.build_command"),
                ):
                    if key in scripts:
                        command = "npm run build" if key == "build" else f"npm {key}"
                        finding(category, command, "explicit", 0.99, [package_ev])
                for dependency, kind in (
                    ("pg", "postgresql"),
                    ("redis", "redis"),
                    ("ioredis", "redis"),
                    ("amqplib", "rabbitmq"),
                    ("bullmq", "queue"),
                ):
                    if dependency in dependencies:
                        node_services.add(kind)
                        finding(f"dependencies.{kind}", dependency, "explicit", 0.95, [package_ev])

        env_names: set[str] = set()
        ports: list[int] = []
        health: list[str] = []
        for path in paths if not (native or unsupported) else ():
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
                if re.search(r"if\s*\(\s*!\s*process\.env\.API_TOKEN\s*\)", line):
                    node_required.add("API_TOKEN")
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
                if not default_port:
                    default_port = re.search(
                        r"""process\.env\.PORT\s*(?:\?\?|\|\|)\s*['"]?(\d{1,5})\b""",
                        line,
                    )
                if default_port:
                    port = int(default_port.group(1))
                    if not 1 <= port <= 65535:
                        continue
                    ports.append(port)
                    finding(
                        "network.port",
                        port,
                        "inferred",
                        0.85,
                        [self._add_evidence(path, number, line, "node.port-default.v1")],
                    )

        node_supported = any(
            f["category"] == "runtime" and f["value"] == "nodejs" for f in findings
        )
        if node_supported and native:
            native = None
            unsupported = "Multiple supported root runtimes require application selection."
        if unsupported or native:
            node_supported = False
            findings.clear()
            self.evidence.clear()
            ports.clear()
            health.clear()
            env_names.clear()
        if native:
            languages = [native.runtime]
            evidence = {}
            for item in native.findings:
                key = (item.path, item.line, item.excerpt)
                if key not in evidence:
                    evidence[key] = self._add_evidence(
                        self.root / item.path,
                        item.line,
                        item.excerpt,
                        f"native.{native.runtime}.v1",
                    )
                finding(item.category, item.value, "inferred", 0.9, [evidence[key]])

        def value(category):
            return next((item["value"] for item in findings if item["category"] == category), None)

        runtime = value("runtime")
        install = value("workflow.install_command")
        start = value("runtime.command")
        if native:
            ports = [port] if (port := value("network.port")) is not None else []
            health = [probe] if (probe := value("health_check")) else []
        if runtime == "nodejs":
            if "postgresql" in node_services and "DATABASE_URL" in env_names:
                node_required.add("DATABASE_URL")
            by_priority = ("/ready", "/health/ready", "/healthz", "/health", "/status", "/live", "/health/live")
            readiness = next((route for route in by_priority if route in health), None)
            if readiness:
                health = [readiness, *(route for route in health if route != readiness)]
        unknowns = []
        if not runtime:
            unknowns.append(unsupported or "No supported root application layout was detected.")
            finding("workload_type", "unknown", "unknown", 0.0, [])
        if native:
            for missing, label in (
                (not start, "start command"),
                (not ports, "service port"),
                (not health, "health path"),
            ):
                if missing:
                    unknowns.append(f"No unambiguous {label} was found for this runtime.")
        document = {
            "schema_version": "infrastructure-assessment-v2",
            "mode": "deterministic",
            "completion": "partial" if unknowns else "complete",
            "incomplete_reason": (
                "unsupported_stack"
                if not runtime
                else "insufficient_evidence"
                if unknowns
                else None
            ),
            "repository": repository,
            "application": {
                "name": application_name,
                "workload_type": "web_service" if runtime else "unknown",
            },
            "detected_stack": {
                "languages": languages,
                "frameworks": [framework]
                if (framework := native.framework if native else value("framework"))
                else [],
            },
            "build": {
                "install_command": install,
                "artifact": None,
            },
            "runtime": {
                "name": runtime,
                "command": start,
                "version": value("runtime.version"),
                **({"bundler": bundler} if (bundler := value("runtime.bundler")) else {}),
                **({"application_target": target} if (target := value("runtime.application_target")) else {}),
            },
            "review": {
                "runtime": runtime or "unknown",
                "port": min(ports) if ports else None,
                "health_path": health[0] if health else None,
                **({"liveness_path": "/health/live"} if (
                    health and health[0] != "/health/live"
                    and (value("health.liveness") or (runtime == "nodejs" and "/health/live" in health))
                ) else {}),
            },
            "network": {"ports": sorted(set(ports))},
            "dependencies": {
                "databases": [],
                "caches": [],
                "queues": [],
                "external_services": [],
                "detected_services": native.service_dependencies if native else sorted(node_services),
            },
            "configuration": {
                "required_variables": native.required_variables if native else sorted(node_required),
                "optional_variables": sorted(env_names - node_required),
            },
            "storage": [],
            "health_checks": sorted(set(health)),
            "workflow": {
                "test_command": value("workflow.test_command"),
                "build_command": value("workflow.build_command"),
                **({"worker_target": worker} if (worker := value("workflow.worker_target")) else {}),
                **(
                    {"migration_command": migration} if (migration := value("workflow.migration_command")) else {}
                ),
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
        validated = InfrastructureAssessmentV2.model_validate(document).model_dump(
            exclude={"command_inference"}
        )
        return ScanResult(
            document=validated,
            facts={
                "schema_version": "analysis-facts-v1",
                "paths": sorted(names),
                "languages": languages,
                "repository": repository,
                "scanner_version": SCANNER_VERSION,
                "document": validated,
            },
        )

    def _package(self):
        path = self.root / "package.json"
        if not path.is_file():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            return (path, manifest) if isinstance(manifest, dict) else None
        except (OSError, json.JSONDecodeError):
            return None
