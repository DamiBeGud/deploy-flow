import asyncio
import logging
import re
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.agents.harness import AgentBudget, AgentError, HarnessOutcome, run_agent
from app.agents.provider import DeepSeekResponsesProvider
from app.agents.repository_mcp import repository_gateway
from app.analysis.service import fake_assessment
from app.config import settings
from app.persistence.database import Session
from app.persistence.models import (
    AgentToolEvent,
    AnalysisRun,
    AnalysisRunSkill,
    Application,
    AssessmentVersion,
    AuditEvent,
    EvidenceItem,
    PendingJob,
    SkillVersion,
)
from app.repository.archive import extract_tarball
from app.repository.errors import RepositoryError
from app.repository.github import GitHubClient
from app.repository.scanner import Scanner
from app.skills.registry import SkillBundle, SkillMetadata, SkillRegistry
from app.telemetry.events import TERMINAL, emit

log = logging.getLogger(__name__)


def finish(db, run, status, message, error_code=None):
    run.status = status
    run.error_code = error_code
    run.finished_at = db.scalar(select(func.now()))
    run.lease_token = None
    run.lease_expires_at = None
    job = db.get(PendingJob, run.id)
    if job:
        db.delete(job)
    emit(db, run, f"analysis.{status}", message)


def retry_or_fail(db, run, code, message=None):
    run.lease_token = None
    run.lease_expires_at = None
    if run.attempts >= settings().max_attempts:
        finish(db, run, "failed", message or "Analysis failed after bounded attempts.", code)
    else:
        run.status = "queued"
        run.progress = 0
        run.error_code = code
        db.get(PendingJob, run.id).next_dispatch_at = db.scalar(select(func.now())) + timedelta(
            seconds=min(2**run.attempts, 30)
        )
        emit(db, run, "analysis.retrying", "Analysis will retry after an interrupted attempt.")


def claim(run_id: UUID) -> UUID | None:
    with Session.begin() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
        if not run or run.status != "queued":
            return None
        now = db.scalar(select(func.now()))
        if (now - run.created_at).total_seconds() >= settings().queue_timeout_seconds:
            finish(db, run, "failed", "Analysis exceeded its total queue deadline.", "job_timeout")
            return None
        job = db.get(PendingJob, run.id)
        if not job or run.attempts >= settings().max_attempts:
            return None
        # Retry deliveries must honor the durable retry backoff. Initial delivery
        # ignores next_dispatch_at, which the dispatcher advanced before publish.
        if run.error_code and job.next_dispatch_at > now:
            return None
        run.status = "running"
        run.attempts += 1
        run.lease_token = uuid4()
        run.lease_expires_at = now + timedelta(seconds=settings().job_timeout_seconds)
        run.attempt_deadline_at = now + timedelta(seconds=settings().analysis_attempt_seconds)
        run.error_code = None
        emit(db, run, "analysis.started", f"Starting {run.analysis_mode} repository analysis.")
        log.info("analysis_started run_id=%s correlation_id=%s", run.id, run.correlation_id)
        return run.lease_token


def advance(run_id, token, kind, percent, *, complete=False):
    with Session.begin() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
        if not run or run.status != "running" or run.lease_token != token:
            return False
        now = db.scalar(select(func.now()))
        if (now - run.created_at).total_seconds() >= settings().queue_timeout_seconds:
            finish(db, run, "failed", "Analysis exceeded its total queue deadline.", "job_timeout")
            return False
        if run.lease_expires_at <= now:
            retry_or_fail(db, run, "attempt_timeout")
            return False
        run.progress = percent
        if complete:
            run.assessment_version = 1
            db.add(
                AssessmentVersion(
                    run_id=run.id, version=1, document=fake_assessment(), actor_id="fake-analysis"
                )
            )
            finish(db, run, "completed", "Simulated assessment ready for review.")
        else:
            emit(db, run, kind, "Simulating " + kind.removeprefix("analysis.") + ".")
        return True


def execute(run_id: str):
    identifier = UUID(run_id)
    token = claim(identifier)
    if token is None:
        return
    try:
        if settings().resolved_analysis_mode != "fake":
            execute_repository(identifier, token)
            return
        stages = [
            ("analysis.scanning", 25),
            ("analysis.agent_running", 55),
            ("analysis.validating", 85),
            ("analysis.completed", 100),
        ]
        for kind, progress in stages:
            time.sleep(settings().fake_step_seconds)
            if not advance(identifier, token, kind, progress, complete=progress == 100):
                return
    except RepositoryError as exc:
        with Session.begin() as db:
            run = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if run and run.status not in TERMINAL and run.lease_token == token:
                if exc.retryable:
                    retry_or_fail(db, run, exc.code, exc.user_message)
                else:
                    finish(db, run, "failed", exc.user_message, exc.code)
    except AgentError as exc:
        if exc.diagnostic:
            log.warning(
                "analysis_agent_failed run_id=%s code=%s provider_diagnostic=%s",
                identifier,
                exc.code,
                exc.diagnostic,
            )
        else:
            log.warning("analysis_agent_failed run_id=%s code=%s", identifier, exc.code)
        with Session.begin() as db:
            run = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if run and run.status not in TERMINAL and run.lease_token == token:
                if exc.retryable:
                    retry_or_fail(db, run, exc.code)
                else:
                    finish(db, run, "failed", "Agent analysis could not be completed.", exc.code)
    except Exception as exc:
        # The traceback identifies our failing code path without logging exception
        # text, request payloads, repository contents, or credentials.
        log.error(
            "analysis_attempt_failed run_id=%s exception_type=%s",
            identifier,
            type(exc).__name__,
            exc_info=True,
        )
        with Session.begin() as db:
            run = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if run and run.status not in TERMINAL and run.lease_token == token:
                retry_or_fail(db, run, "analysis_error")
        # Process termination / database outage is recovered by the lease reaper.


def _audit(db, application: Application, run: AnalysisRun, action: str, metadata: dict):
    db.add(
        AuditEvent(
            organization_id=application.organization_id,
            actor_id=run.actor_id,
            action=action,
            resource_type="analysis",
            resource_id=str(run.id),
            metadata_json=metadata,
        )
    )


def _persist_evidence(db, run: AnalysisRun, evidence: list[dict]):
    existing = {
        key
        for key in db.scalars(
            select(EvidenceItem.evidence_key).where(EvidenceItem.run_id == run.id)
        ).all()
        if key
    }
    for item in evidence:
        if item["id"] in existing:
            continue
        db.add(
            EvidenceItem(
                run_id=run.id,
                evidence_key=item["id"],
                file_path=item["file_path"],
                line_start=item["line_start"],
                line_end=item["line_end"],
                excerpt_hash=item["excerpt_hash"],
                detector=item["detector"],
            )
        )


def _snapshot_skills(db, run: AnalysisRun, selected: list[SkillBundle]) -> list[SkillBundle]:
    links = db.scalars(
        select(AnalysisRunSkill)
        .where(AnalysisRunSkill.run_id == run.id)
        .order_by(AnalysisRunSkill.ordinal)
    ).all()
    if links:
        bundles = []
        for link in links:
            version = db.get(SkillVersion, link.skill_version_id)
            bundles.append(
                SkillBundle(
                    SkillMetadata.model_validate(version.metadata_json),
                    version.instructions,
                    version.references_json,
                    version.content_hash,
                )
            )
        return bundles
    for ordinal, bundle in enumerate(selected):
        version = db.scalar(
            select(SkillVersion).where(
                SkillVersion.skill_id == bundle.metadata.id,
                SkillVersion.semantic_version == bundle.metadata.version,
                SkillVersion.content_hash == bundle.content_hash,
            )
        )
        if version is None:
            version = SkillVersion(
                skill_id=bundle.metadata.id,
                semantic_version=bundle.metadata.version,
                content_hash=bundle.content_hash,
                metadata_json=bundle.metadata.model_dump(),
                instructions=bundle.instructions,
                references_json=bundle.references,
            )
            db.add(version)
            db.flush()
        db.add(AnalysisRunSkill(run_id=run.id, skill_version_id=version.id, ordinal=ordinal))
    return selected


async def _run_agent(
    root: Path, metadata: dict, facts: dict, skills: list[SkillBundle], budget: AgentBudget
) -> HarnessOutcome:
    config = settings()
    allowed = set().union(*(set(bundle.metadata.allowed_tools) for bundle in skills))
    provider = DeepSeekResponsesProvider(
        api_key=config.deepseek_api_key,
        base_url=config.deepseek_base_url,
        model=config.deepseek_model,
        reasoning_effort=config.deepseek_reasoning_effort,
        timeout_seconds=config.agent_provider_timeout_seconds,
        max_output_tokens=config.agent_response_max_tokens,
    )
    try:
        async with repository_gateway(
            root,
            metadata,
            allowed_tools=allowed,
            read_limit=config.repository_read_bytes,
            result_limit=config.repository_result_budget,
            output_budget=config.agent_tool_output_bytes,
            tool_timeout_seconds=config.agent_tool_timeout_seconds,
        ) as gateway:
            return await run_agent(
                provider=provider, gateway=gateway, facts=facts, skills=skills, budget=budget
            )
    except BaseExceptionGroup as exc:
        # anyio wraps an error raised inside the MCP context manager in an
        # ExceptionGroup during cleanup. Preserve the typed agent error so the
        # worker can retain its diagnostic rather than treating it as unknown.
        pending = list(exc.exceptions)
        while pending:
            candidate = pending.pop()
            if isinstance(candidate, AgentError):
                raise candidate from exc
            if isinstance(candidate, BaseExceptionGroup):
                pending.extend(candidate.exceptions)
        raise


def _finalize_agent(db, application: Application, run: AnalysisRun, outcome: HarnessOutcome):
    _persist_evidence(db, run, outcome.new_evidence)
    for trace in outcome.traces:
        db.add(
            AgentToolEvent(
                run_id=run.id,
                turn=trace.turn,
                call_id=trace.call_id or f"call-{trace.turn}-{trace.tool_name}",
                tool_name=trace.tool_name,
                arguments_json=trace.arguments,
                result_json=trace.result,
                status=trace.status,
                latency_ms=trace.latency_ms,
            )
        )
    run.assessment_version = 1
    run.incomplete_reason = outcome.document.get("incomplete_reason")
    run.model_provider = "deepseek"
    run.model_name = outcome.returned_model or settings().deepseek_model
    run.prompt_version = "phase3-agent-v1"
    run.agent_turns = outcome.budget.used_turns
    run.agent_tool_calls = outcome.budget.used_tool_calls
    run.agent_input_tokens = outcome.budget.used_input_tokens
    run.agent_output_tokens = outcome.budget.used_output_tokens
    run.agent_tool_output_bytes = outcome.tool_output_bytes
    run.agent_latency_ms = outcome.budget.used_latency_ms
    run.reproducibility_json = {
        "scanner_version": "phase3-v1",
        "github_api_version": settings().github_api_version,
        "commit_sha": run.commit_sha,
        "provider": "deepseek",
        "requested_model": settings().deepseek_model,
        "returned_model": run.model_name,
        "prompt_version": run.prompt_version,
        "skills": [
            {
                "id": bundle.metadata.id,
                "version": bundle.metadata.version,
                "hash": bundle.content_hash,
            }
            for bundle in _load_run_skills(db, run)
        ],
        "usage": {
            "turns": run.agent_turns,
            "tool_calls": run.agent_tool_calls,
            "input_tokens": run.agent_input_tokens,
            "output_tokens": run.agent_output_tokens,
            "latency_ms": run.agent_latency_ms,
        },
    }
    db.add(
        AssessmentVersion(
            run_id=run.id, version=1, document=outcome.document, actor_id="deepseek-agent"
        )
    )
    _audit(
        db,
        application,
        run,
        "analysis.agent_completed",
        {"status": outcome.status, "turns": run.agent_turns, "tool_calls": run.agent_tool_calls},
    )
    run.progress = 100
    finish(
        db,
        run,
        outcome.status,
        "Agent assessment requires review."
        if outcome.status == "needs_input"
        else "Agent assessment ready for review.",
    )


def _load_run_skills(db, run: AnalysisRun) -> list[SkillBundle]:
    links = db.scalars(
        select(AnalysisRunSkill)
        .where(AnalysisRunSkill.run_id == run.id)
        .order_by(AnalysisRunSkill.ordinal)
    ).all()
    return [
        SkillBundle(
            SkillMetadata.model_validate(
                (version := db.get(SkillVersion, link.skill_version_id)).metadata_json
            ),
            version.instructions,
            version.references_json,
            version.content_hash,
        )
        for link in links
    ]


def execute_repository(identifier: UUID, token: UUID):
    with Session() as db:
        run = db.get(AnalysisRun, identifier)
        application = db.get(Application, run.application_id)
        url = application.repository_url
        name = application.name
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", url)
    if not match:
        raise RepositoryError()
    client = GitHubClient(settings())
    resolved = client.resolve(match.group(1), match.group(2), run.revision)
    workspace = None
    try:
        workspace = tempfile.mkdtemp(prefix=f"deployflow-{identifier}-")
        root = Path(workspace) / "repository"
        with Session.begin() as db:
            current = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if not current or current.lease_token != token or current.status != "running":
                return
            current.progress = 15
            current.commit_sha = resolved.commit_sha
            emit(
                db,
                current,
                "analysis.checkout_started",
                "Resolving and preparing the pinned repository revision.",
            )
        extract_tarball(
            client.download(resolved),
            root,
            compressed_limit=settings().repository_archive_bytes,
            extracted_limit=settings().repository_extracted_bytes,
            file_limit=settings().repository_file_count,
            path_limit=settings().repository_path_length,
        )
        metadata = {
            "url": resolved.url,
            "owner": resolved.owner,
            "name": resolved.name,
            "revision": resolved.revision,
            "commit_sha": resolved.commit_sha,
        }
        scan = Scanner(root).scan(repository=metadata, application_name=name)
        with Session.begin() as db:
            current = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if not current or current.lease_token != token or current.status != "running":
                return
            current.progress = 85
            emit(
                db,
                current,
                "analysis.scanning",
                "Scanning repository files without executing application code.",
            )
            current.scanner_facts_json = scan.facts
            _persist_evidence(db, current, scan.document["evidence"])
            _audit(
                db,
                application,
                current,
                "analysis.repository_scanned",
                {
                    "commit_sha": resolved.commit_sha,
                    "evidence_count": len(scan.document["evidence"]),
                },
            )
            emit(
                db,
                current,
                "analysis.validating",
                "Validating deterministic findings and evidence.",
            )
            if current.analysis_mode == "deterministic":
                current.assessment_version = 1
                current.incomplete_reason = scan.document["incomplete_reason"]
                current.reproducibility_json = {
                    "scanner_version": "phase3-v1",
                    "github_api_version": settings().github_api_version,
                    "commit_sha": resolved.commit_sha,
                }
                db.add(
                    AssessmentVersion(
                        run_id=current.id,
                        version=1,
                        document=scan.document,
                        actor_id="deterministic-scanner",
                    )
                )
                current.progress = 100
                finish(db, current, "completed", "Repository assessment ready for review.")
                return
            registry = SkillRegistry(
                settings().skill_directory, max_context_chars=settings().skill_context_chars
            ).load()
            skills = _snapshot_skills(db, current, registry.select(scan.facts))
            current.progress = 90
            emit(db, current, "analysis.skills_selected", "Selected reviewed investigation skills.")
            _audit(
                db,
                application,
                current,
                "analysis.skills_selected",
                {
                    "skills": [
                        {
                            "id": bundle.metadata.id,
                            "version": bundle.metadata.version,
                            "hash": bundle.content_hash,
                        }
                        for bundle in skills
                    ]
                },
            )
            emit(
                db,
                current,
                "analysis.agent_running",
                "Investigating repository uncertainty through read-only tools.",
            )
            budget = AgentBudget(
                settings().agent_max_turns,
                settings().agent_max_tool_calls,
                settings().agent_max_input_tokens,
                settings().agent_max_output_tokens,
                settings().agent_wall_seconds,
                current.agent_turns,
                current.agent_tool_calls,
                current.agent_input_tokens,
                current.agent_output_tokens,
                current.agent_latency_ms,
            )
        try:
            outcome = asyncio.run(_run_agent(root, metadata, scan.facts, skills, budget))
        except AgentError as exc:
            if exc.code != "agent_budget_exhausted":
                raise
            outcome = HarnessOutcome(
                {
                    **scan.document,
                    "mode": "agent",
                    "completion": "partial",
                    "incomplete_reason": "agent_budget_exhausted",
                    "summary": "Agent budget was exhausted; deterministic scanner facts remain available.",
                },
                [],
                [],
                budget,
                0,
                None,
                "completed",
            )
        with Session.begin() as db:
            current = db.scalar(
                select(AnalysisRun).where(AnalysisRun.id == identifier).with_for_update()
            )
            if not current or current.lease_token != token or current.status != "running":
                return
            _finalize_agent(db, application, current, outcome)
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)
