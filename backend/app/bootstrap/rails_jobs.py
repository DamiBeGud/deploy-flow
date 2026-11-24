"""Durable reconciliation of preapproved Rails requests, isolated from agents."""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.analysis.command_inference import (
    PHASE_FIELDS,
    command_blocker,
    command_inference,
    scanner_commands,
)
from app.bootstrap.managed_rails import (
    SECRET_NAMES,
    ManagedRailsError,
    RailsProfile,
    managed_profile,
    provision_rails,
)
from app.bootstrap.native_runtime import native_build
from app.bootstrap.postgres import PostgresBinding
from app.persistence.database import Session
from app.persistence.models import AnalysisRun, Application, AssessmentVersion, RailsProvisionJob

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
CRITICAL_PATHS = {
    "runtime.command": "config/puma.rb",
    "workflow.install_command": "Gemfile.lock",
    "workflow.test_command": "Rakefile",
    "network.port": "config/puma.rb",
    "rails.production.listen_host": "config/puma.rb",
    "rails.database.adapter": "config/database.yml",
    "rails.database.driver": "Gemfile.lock",
    "rails.database.url_reference": "config/database.yml",
    "rails.production.secret_reference": "config/environments/production.rb",
    "rails.migration.tasks": "Rakefile",
    "health_check": "config/routes.rb",
    "health.liveness": "config/routes.rb",
}
CRITICAL_FIELDS = set(CRITICAL_PATHS) | {"workflow.migration_command", "configuration.variable"}
AGENT_REQUIRED_PATHS = set(CRITICAL_PATHS.values())


def scoped(profile: RailsProfile, application: Application, run: AnalysisRun) -> bool:
    return (
        profile.admits(application, run.actor_id, run.revision)
        and run.application_id == application.id
        and run.commit_sha == profile.source_sha
    )


def eligible(
    profile: RailsProfile, application: Application, run: AnalysisRun, document: dict,
) -> PostgresBinding:
    if not scoped(profile, application, run):
        raise ManagedRailsError("rails_source_outside_approved_scope")
    reproducibility = run.reproducibility_json or {}
    source = document.get("repository", {})
    # Preserve the raw agent assessment. The scoped provisioning profile can
    # answer only these delivery choices after all evidence checks below pass.
    required_fields = {q["field_path"] for q in document.get("questions", []) if q["required"]}
    bootstrap = ((run.scanner_facts_json or {}).get("document") or {}).get("bootstrap", {})
    platform_choices = (
        run.analysis_mode == "agent" and run.status == "needs_input"
        and bool(required_fields)
        and required_fields <= {"bootstrap.dockerfile_source", "workflow.migration_execution"}
        and bootstrap.get("dockerfile_required") is True
        and bootstrap.get("workflow_required") is True
        and not document.get("conflicts")
    )
    if (
        not (platform_choices or (run.status == "completed" and not required_fields))
        or run.assessment_version != 1
        or run.analysis_mode not in {"agent", "deterministic"}
        or document.get("mode") != run.analysis_mode
        or (run.analysis_mode == "agent" and (
            reproducibility.get("fallback_used") is not False
            or not reproducibility.get("provider_response_ids")
            or run.model_provider != "deepseek"
            or not run.model_name
        ))
        or document.get("completion") != "complete"
        or f"{source.get('owner')}/{source.get('name')}".lower() != profile.repository
        or source.get("revision") != profile.revision
        or source.get("commit_sha") != profile.source_sha
        or document.get("application", {}).get("name") != application.name
        or document.get("review", {}).get("runtime") != "ruby"
    ):
        raise ManagedRailsError("rails_assessment_not_eligible")

    inference = command_inference(document)
    start = inference.get("commands", {}).get("start")
    if command_blocker(inference) or (start and start["status"] != "selected") or any(
        conflict.removeprefix("Model claim conflicts with scanner fact: ") in CRITICAL_FIELDS
        for conflict in document.get("conflicts", [])
    ):
        raise ManagedRailsError("rails_critical_evidence_conflict")
    if run.analysis_mode == "agent":
        paths = {
            item["file_path"] for item in document.get("evidence", [])
            if item["detector"] in {"repository-mcp.read-file.v1", "repository-mcp.search-text.v1"}
        }
        rails_skill = any(
            skill["id"] == "rails" and re.fullmatch(r"[0-9a-f]{64}", skill["hash"])
            for skill in reproducibility.get("skills", [])
        )
        if (
            not rails_skill or not AGENT_REQUIRED_PATHS.issubset(paths)
            or not any(re.fullmatch(r"db/migrate/\d+_[a-z0-9_]+\.rb", path) for path in paths)
        ):
            raise ManagedRailsError("rails_agent_source_review_incomplete")

    # The immutable scanner facts are independent of any claims added by the agent.
    scanner = (run.scanner_facts_json or {}).get("document") or {}
    review = document["review"]
    runtime = document.get("runtime", {})
    if (
        scanner.get("repository") != source
        or set(document.get("configuration", {}).get("required_variables", [])) != {"DATABASE_URL", "SECRET_KEY_BASE"}
        or set(document.get("dependencies", {}).get("detected_services", [])) != {"postgresql"}
        or runtime.get("command") != "bundle exec puma -C config/puma.rb"
        or document.get("workflow", {}).get("migration_command") != "bundle exec rails db:migrate"
        or not review.get("port")
        or not review.get("health_path")
        or review.get("liveness_path") != "/health/live"
        or review["health_path"] == review["liveness_path"]
    ):
        raise ManagedRailsError("rails_profile_requirements_unsupported")

    expected = {
        "runtime.command": runtime["command"],
        "workflow.install_command": "bundle install",
        "workflow.test_command": "bundle exec rake unit",
        "network.port": review["port"],
        "rails.production.listen_host": "0.0.0.0",
        "rails.database.adapter": "postgresql",
        "rails.database.driver": "pg",
        "rails.database.url_reference": "DATABASE_URL",
        "rails.production.secret_reference": "SECRET_KEY_BASE",
        "rails.migration.tasks": "Rails.application.load_tasks",
        "health_check": review["health_path"],
        "health.liveness": review["liveness_path"],
        "workflow.migration_command": document["workflow"]["migration_command"],
    }
    evidence = {item["id"]: item for item in scanner.get("evidence", [])}
    for category, value in expected.items():
        witnesses = (
            evidence[key]
            for finding in scanner.get("findings", [])
            if finding["category"] == category and finding["value"] == value
            for key in finding["evidence_ids"] if key in evidence
        )
        if not any(
            item["detector"] == "native.ruby.v1" and (
                item["file_path"] == CRITICAL_PATHS[category] if category in CRITICAL_PATHS
                else re.fullmatch(r"db/migrate/\d+_[a-z0-9_]+\.rb", item["file_path"])
            )
            for item in witnesses
        ):
            raise ManagedRailsError("rails_critical_evidence_missing")
    commands = {field: text.split() if text else None for field, text in scanner_commands(document).items()}
    for phase, resolution in inference.get("commands", {}).items():
        commands[PHASE_FIELDS[phase]] = resolution.get("argv")
    try:
        native_build(
            "ruby", runtime.get("version"), review["port"], commands["install_command"],
            commands["test_command"], commands["build_command"], commands["start_command"],
            bundler=runtime.get("bundler"),
        )
    except ValueError:
        raise ManagedRailsError("rails_profile_commands_unsupported") from None
    return PostgresBinding(
        application_id=application.id,
        application_name=application.name,
        repository=profile.repository,
        source_sha=profile.source_sha,
        secret_name=SECRET_NAMES[0],  # The catalog selects the actual free slot.
        runtime="ruby",
        liveness_path=review["liveness_path"],
        ephemeral=True,
        managed=True,
    )


def request_provisioning(db, profile, application, run, assessment):
    binding = eligible(profile, application, run, assessment.document)
    db.execute(insert(RailsProvisionJob).values(
        application_id=binding.application_id,
        source_sha=binding.source_sha,
        run_id=run.id,
    ).on_conflict_do_nothing(index_elements=["application_id", "source_sha"]))
    return db.get(RailsProvisionJob, (binding.application_id, binding.source_sha))


def process_one() -> bool:
    # Keep the single job row locked during bounded API calls. A crash releases
    # the lock and the next worker reconciles any catalog or Secret already made.
    with Session.begin() as db:
        now = db.scalar(select(func.now()))
        job = db.scalar(
            select(RailsProvisionJob)
            .where(RailsProvisionJob.status == "queued", RailsProvisionJob.next_attempt_at <= now)
            .order_by(RailsProvisionJob.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return False
        job.attempts += 1
        try:
            profile = managed_profile()
            if profile is None:
                raise ManagedRailsError("rails_profile_disabled")
            run = db.get(AnalysisRun, job.run_id)
            application = db.get(Application, job.application_id)
            assessment = db.get(AssessmentVersion, (run.id, run.assessment_version))
            if not assessment:
                raise ManagedRailsError("rails_assessment_not_eligible")
            binding = eligible(profile, application, run, assessment.document)
            if binding.source_sha != job.source_sha:
                raise ManagedRailsError("rails_source_outside_approved_scope")
            provision_rails(binding)
        except ManagedRailsError as exc:
            job.error_code = str(exc)
            if exc.retryable and job.attempts < MAX_ATTEMPTS:
                job.next_attempt_at = now + timedelta(seconds=2 ** job.attempts)
            else:
                job.status = "blocked"
        else:
            job.error_code = None
            job.status = "ready"
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            if process_one():
                continue
        except Exception:
            log.error("rails_provisioner_unavailable")
        time.sleep(2)


if __name__ == "__main__":
    main()
