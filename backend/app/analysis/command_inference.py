"""Resolve typed delivery commands against inspected, pinned repository evidence."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.analysis.contracts import (
    AssessmentDraftV3,
    CommandInference,
    CommandPhase,
    CommandResolution,
    InferredHealth,
    SourceLocation,
    VerifiedCommand,
)
from app.bootstrap.native_runtime import PYTHON_INSTALL, PYTHON_TEST, python_listener

PHASE_FIELDS: dict[CommandPhase, str] = {
    "install": "install_command",
    "test": "test_command",
    "build": "build_command",
    "start": "start_command",
}
COMMAND_PROMPT = (
    " Return command_candidates for install, test, optional build, and start. Read the full "
    "README with repo_read_file starting at line 1, the installed requirements file, "
    "and the application entry point before selecting commands. Cite the file-read evidence "
    "IDs in each candidate, including the exact documented invocation. Return argv as tokens "
    "and keep the development and production launchers as separate candidates with their "
    "respective contexts. Use context unspecified for unqualified install, test and build. "
    "Use working_directory '.' for this root-layout contract. Also report network.port for "
    "the production bind and health_check with source evidence. An absent phase can be omitted."
)


@dataclass(frozen=True)
class SourceExcerpt:
    path: str
    start: int
    text: str


@dataclass(frozen=True)
class Declaration:
    phase: CommandPhase
    argv: tuple[str, ...]
    context: str
    line: int


def scanner_commands(document: dict) -> dict[str, str | None]:
    return {
        "install_command": document.get("build", {}).get("install_command"),
        "test_command": document.get("workflow", {}).get("test_command"),
        "build_command": document.get("workflow", {}).get("build_command"),
        "start_command": document.get("runtime", {}).get("command"),
    }


def _declarations(excerpt: SourceExcerpt):
    if not excerpt.path.lower().endswith(".md") or excerpt.start != 1:
        return
    sections: list[tuple[int, str]] = []
    fence = False
    for line, text in enumerate(excerpt.text.splitlines(), excerpt.start):
        if text.lstrip().startswith(("~~~", "```")):
            fence = not fence
            continue
        if not fence and (heading := re.match(r"^(#{1,6})\s+(.+)$", text)):
            level, title = len(heading[1]), heading[2].lower()
            while sections and sections[-1][0] >= level:
                sections.pop()
            sections.append((level, title))
            continue
        section = " ".join(title for _, title in sections)
        context = (
            "production"
            if re.search(r"\bproduction\b", section)
            else "development"
            if re.search(r"\bdevelopment\b", section)
            else "unspecified"
        )
        phase = next(
            (
                item for _, title in reversed(sections)
                for item in ("install", "test", "build", "start")
                if re.search(
                    rf"\b{item}\w*\b" if item != "start"
                    else r"\b(?:start|run|launch|deploy\w*|production|development)\b", title,
                )
            ),
            None,
        )
        if phase is None:
            continue
        for command in [text.strip()] if fence else re.findall(r"`([^`]+)`", text):
            try:
                argv = tuple(shlex.split(command, comments=True))
            except ValueError:
                continue
            if len(argv) >= 2:
                yield Declaration(phase, argv, context, line)


def _requirement(excerpts: list[SourceExcerpt], name: str) -> bool:
    return any(
        item.path == "requirements.txt"
        and re.search(rf"(?im)^\s*{re.escape(name)}(?=\[|\s|[<=>~!;@]|$)", item.text)
        for item in excerpts
    )


def _supported(candidate, document: dict, excerpts: list[SourceExcerpt]) -> tuple[bool, str]:
    family = document["review"]["runtime"]
    if candidate.working_directory != "." or candidate.runtime_family != family:
        return False, "The detected runtime or supported source root differs."
    argv = candidate.argv
    if family == "python":
        if (
            candidate.phase == "install"
            and argv == PYTHON_INSTALL
            and any(item.path == "requirements.txt" and item.text for item in excerpts)
        ):
            return True, "Pinned requirements define the installation."
        if candidate.phase == "test" and argv == PYTHON_TEST and _requirement(excerpts, "pytest"):
            if document["workflow"].get("test_command"):
                return True, "The pinned requirements and source tests support pytest."
        if candidate.phase == "start" and (listener := python_listener(argv)):
            target, _port = listener
            path = target.split(":")[0].replace(".", "/") + ".py"
            if target == document["runtime"].get("application_target") and any(
                item.path == path and item.text for item in excerpts
            ):
                necessary = ["gunicorn", "uvicorn-worker"] if argv[0] == "gunicorn" else ["uvicorn"]
                if all(_requirement(excerpts, name) for name in necessary):
                    return (
                        True,
                        "The documented launcher, entry point and installed dependencies agree.",
                    )
            return False, "The launcher's entry point or installed dependencies lack corroboration."
    else:
        conventional = scanner_commands(document)[PHASE_FIELDS[candidate.phase]]
        if conventional and argv == conventional.split():
            return True, "The declaration matches the supported scanner command."
    return False, "This command is outside the runtime's reviewed patterns."


def _identity(argv):
    if argv[0] != "gunicorn":
        return tuple(argv)
    aliases = {"--worker-class": "-k", "-b": "--bind"}
    return tuple(aliases.get(token, token) for token in argv)


def _select(phase, verified, conventional, declarations) -> CommandResolution:
    candidates = [item for item in verified if item.candidate.context != "development"]
    production = [item for item in candidates if item.candidate.context == "production"]
    candidates = production or candidates
    unique = {_identity(item.candidate.argv): item for item in candidates}
    if len(unique) > 1 or (phase == "start" and len(declarations) > 1):
        return CommandResolution(
            status="conflict",
            reason="Competing source declarations need resolution.",
            alternatives=verified,
        )
    if unique:
        choice = next(iter(unique.values()))
        alternatives = [item for item in verified if item is not choice]
        if not choice.supported:
            return CommandResolution(
                status="unsupported",
                reason=choice.reason,
                alternatives=[choice, *alternatives],
            )
        return CommandResolution(
            status="selected",
            selector="agent",
            argv=choice.candidate.argv,
            selected=choice,
            reason=choice.reason,
            alternatives=alternatives,
        )
    if (
        phase == "start"
        and declarations
        and (not conventional or _identity(conventional.split()) not in declarations)
    ):
        return CommandResolution(
            status="missing",
            reason="The production launcher was not verified.",
            alternatives=verified,
        )
    if conventional:
        return CommandResolution(
            status="selected",
            selector="scanner",
            argv=conventional.split(),
            reason="Scanner convention",
            alternatives=verified,
        )
    return CommandResolution(
        status="missing",
        reason="No verified command was found.",
        alternatives=verified,
    )


def infer_commands(
    document: dict, draft: AssessmentDraftV3, sources: dict[str, SourceExcerpt]
) -> CommandInference:
    conventional = scanner_commands(document)
    declarations = {key: list(_declarations(excerpt)) for key, excerpt in sources.items()}
    verified: dict[CommandPhase, list[VerifiedCommand]] = {phase: [] for phase in PHASE_FIELDS}
    for candidate in draft.command_candidates:
        if any(key not in sources for key in candidate.evidence_ids):
            continue
        excerpts = [sources[key] for key in candidate.evidence_ids]
        proof = next(
            (
                (sources[key], record.line)
                for key in candidate.evidence_ids
                if key in declarations
                for record in declarations[key]
                if record.phase == candidate.phase
                and record.context == candidate.context
                and record.argv == tuple(candidate.argv)
            ),
            None,
        )
        if proof is None:
            continue
        excerpt, line = proof
        supported, reason = _supported(candidate, document, excerpts)
        verified[candidate.phase].append(
            VerifiedCommand(
                candidate=candidate,
                source=SourceLocation(file_path=excerpt.path, line_start=line),
                supported=supported,
                reason=reason,
            )
        )
    production = {
        _identity(record.argv)
        for records in declarations.values()
        for record in records
        if record.phase == "start"
        and record.context == "production"
        and record.argv[0]
        in {"gunicorn", "uvicorn", "java", "bundle", "npm", "python", "./gradlew"}
    }
    commands = {
        phase: _select(phase, verified[phase], conventional[field], production)
        for phase, field in PHASE_FIELDS.items()
    }
    start = commands["start"]
    port = None
    if (
        start.selector == "agent"
        and start.argv
        and document["review"]["runtime"] == "python"
        and (listener := python_listener(start.argv))
    ):
        port = listener[1]
        if any(
            f["category"] == "network.port" and f["status"] == "explicit" and f["value"] != port
            for f in document["findings"]
        ):
            commands["start"] = CommandResolution(
                status="conflict",
                reason="The declared bind conflicts with a fixed source port.",
                alternatives=[start.selected, *start.alternatives],
            )
            port = None

    # Project a model health claim only when it agrees with a statically found
    # route and the model cites an actual source line that declares that route.
    health = None
    for finding in draft.findings:
        if (
            finding.category != "health_check"
            or finding.status == "unknown"
            or not isinstance(finding.value, str)
            or finding.value not in document["health_checks"]
        ):
            continue
        for key in finding.evidence_ids:
            if key not in sources:
                continue
            excerpt = sources[key]
            match = re.search(
                r"""(?:\.get\(\s*|@GetMapping\(\s*|\bget\s+)['"]"""
                + re.escape(finding.value)
                + r"""['"]""",
                excerpt.text,
            )
            if match and not excerpt.path.lower().endswith(".md"):
                health = InferredHealth(
                    path=finding.value,
                    evidence_ids=[key],
                    source=SourceLocation(
                        file_path=excerpt.path,
                        line_start=excerpt.start + excerpt.text.count("\n", 0, match.start()),
                    ),
                )
                break
        if health:
            break
    return CommandInference(
        source_sha=document["repository"]["commit_sha"],
        commands=commands,
        port=port,
        health=health,
    )


def command_inference(document: dict) -> dict:
    result = document.get("command_inference") or {}
    if (
        document.get("mode") == "agent"
        and result.get("schema_version") == "command-inference-v1"
        and result.get("source_sha") == document.get("repository", {}).get("commit_sha")
    ):
        return result
    return {}


def command_blocker(inference: dict) -> str | None:
    for phase, item in inference.get("commands", {}).items():
        if item["status"] in {"conflict", "unsupported"}:
            return f"{phase.title()} command requires review. {item['reason']}"
    return None
