"""Durable, opt-in execution of one stored and verified release."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from uuid import UUID, uuid4

import yaml
from sqlalchemy import func, select

from app.bootstrap.generator import canonical_digest
from app.bootstrap.profiles import local_k3s_profile
from app.config import settings
from app.deployments.external_health import ExternalProbeError, external_health
from app.deployments.preparation import ReleasePreparationError, _check_github, _source
from app.deployments.release import ReleaseError, bind_verified_image
from app.deployments.rollback import RollbackError, release_files
from app.deployments.state import transition
from app.integrations.github.api import GitHubPhase6Error
from app.kubernetes.client import ClusterError
from app.kubernetes.executor import apply_owned, rollout_status, service_health
from app.kubernetes.gateway import GatewayError, call_kubernetes
from app.kubernetes.policy import ManifestPolicyError, checked_manifests
from app.persistence.database import Session
from app.persistence.models import (
    DeploymentPolicy,
    DeploymentResource,
    DeploymentRun,
    ExecutionJob,
    ReleaseFile,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)

log = logging.getLogger(__name__)
_ORDER = {"ConfigMap": 0, "Service": 1, "Deployment": 2, "Ingress": 3}


class ExecutionError(ValueError):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


def _claim(job_id: UUID) -> tuple[UUID, UUID] | None:
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        if not job or job.purpose != "deploy" or job.status not in {"queued", "retry_wait", "running"}:
            return None
        now = db.scalar(select(func.now()))
        if job.status == "running" and job.lease_expires_at and job.lease_expires_at > now:
            return None
        if job.status == "retry_wait" and job.next_dispatch_at > now:
            return None
        run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
        if run.status == "succeeded":
            job.status = "completed"
            job.lease_token = None
            job.lease_expires_at = None
            return None
        # Rollout and health observation are bounded by the deadline, not by
        # the retry budget intended for pre-write failures.
        observing = run.status in {"monitoring", "checking_health"}
        if now >= job.deadline_at or (not observing and job.attempts >= settings().execution_max_attempts):
            job.status = "blocked"
            job.error_code = "execution_deadline_exceeded"
            job.lease_token = None
            job.lease_expires_at = None
            changed = db.scalar(select(DeploymentResource.id).where(
                DeploymentResource.deployment_run_id == run.id,
                DeploymentResource.status.in_({"sending", "applied"}),
            ).limit(1))
            transition(db, run, "reconciling" if changed else "failed", error_code=job.error_code)
            return None
        job.status = "running"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=settings().execution_lease_seconds)
        return job.lease_token, job.deployment_run_id


def _context(db, run: DeploymentRun, *, observe_only: bool = False):
    profile = local_k3s_profile()
    record = db.get(ReleaseSpecificationRecord, run.release_id)
    build = db.get(VerifiedBuild, run.verified_build_id)
    if not record or not build or record.verified_build_id != build.id:
        raise ExecutionError("release_record_corrupt")
    rows = db.scalars(select(ReleaseFile).where(ReleaseFile.release_id == record.id)).all()
    files = {row.path: row.content for row in rows}
    if (
        not rows or len(files) != len(rows)
        or any(hashlib.sha256(row.content.encode()).hexdigest() != row.content_hash for row in rows)
        or canonical_digest(files) != record.manifest_digest
        or record.application_id != run.application_id
        or record.environment_profile_id != profile["id"]
        or record.environment_profile_hash != profile["hash"]
    ):
        raise ExecutionError("release_record_corrupt")
    if record.kind == "rollback":
        target = db.get(ReleaseSpecificationRecord, record.rollback_target_release_id)
        if (
            target is None or target.id == record.id
            or target.application_id != run.application_id
            or target.environment_profile_id != profile["id"]
            or target.environment_profile_hash != profile["hash"]
            or target.verified_build_id != record.verified_build_id
            or target.manifest_digest != record.manifest_digest
            or target.image_reference != record.image_reference
            or target.plan_id != record.plan_id
            or target.approval_id != record.approval_id
            or target.source_bundle_digest != record.source_bundle_digest
            or target.merged_sha != record.merged_sha
            or run.previous_release_id != record.rollback_from_release_id
            or release_files(db, target) != files
        ):
            raise ExecutionError("release_record_corrupt")
        source = None
    elif record.kind == "standard":
        if observe_only:
            source = None
            if record.image_reference != f"{build.image_repository}@{build.image_digest}":
                raise ExecutionError("release_record_corrupt")
        else:
            source = _source(db, build.pull_request_id, build.id, profile)
            if (
                record.application_id != source.application_id
                or record.plan_id != source.plan_id or record.approval_id != source.approval_id
                or record.source_bundle_digest != source.bundle_digest
                or record.merged_sha != source.merged_sha
            ):
                raise ExecutionError("release_record_corrupt")
            expected = bind_verified_image(
                bundle_files=source.files, bundle_digest=source.bundle_digest,
                image_binding=source.image_binding, image_repository=source.image_repository,
                image_digest=source.image_digest, profile=profile,
                application_id=str(source.application_id),
            )
            if expected.files != files or expected.digest != record.manifest_digest or expected.image_reference != record.image_reference:
                raise ExecutionError("release_record_corrupt")
    else:
        raise ExecutionError("release_record_corrupt")
    if run.previous_release_id:
        previous_record = db.get(ReleaseSpecificationRecord, run.previous_release_id)
        if previous_record is None or previous_record.application_id != run.application_id:
            raise ExecutionError("previous_release_invalid")
        previous_files = release_files(db, previous_record)
        config_path = "deploy/kubernetes/configmap.yaml"
        deployment_path = "deploy/kubernetes/deployment.yaml"
        if previous_files.get(config_path) != files.get(config_path):
            previous_deployment = previous_files.get(deployment_path)
            if not previous_deployment or deployment_path not in files:
                raise ExecutionError("configuration_rollout_missing")
            previous_template = yaml.safe_load(previous_deployment)["spec"]["template"]
            next_template = yaml.safe_load(files[deployment_path])["spec"]["template"]
            if previous_template == next_template:
                raise ExecutionError("configuration_rollout_missing")
    policy = db.scalar(select(DeploymentPolicy).where(
        DeploymentPolicy.application_id == run.application_id,
        DeploymentPolicy.environment_profile_id == run.environment_profile_id,
        DeploymentPolicy.enabled.is_(True),
    ))
    if not observe_only and (policy is None or not settings().deployment_execution_enabled or settings().auth_mode != "oidc"):
        raise ExecutionError("deployment_not_authorized")
    manifests = checked_manifests(
        files, digest=record.manifest_digest, namespace=profile["namespace"],
        application_id=str(record.application_id), registry=profile["registry"],
    )
    if run.previous_release_id:
        previous_manifests = checked_manifests(
            previous_files, digest=previous_record.manifest_digest,
            namespace=profile["namespace"], application_id=str(run.application_id),
            registry=profile["registry"],
        )
        current_objects = {(item["kind"], item["metadata"]["name"]) for item in manifests}
        if any((item["kind"], item["metadata"]["name"]) not in current_objects for item in previous_manifests):
            raise ExecutionError("resource_removal_requires_cleanup")
    return source, record, files, sorted(manifests, key=lambda item: _ORDER[item["kind"]])


def _fenced(db, job_id: UUID, token: UUID, run_id: UUID):
    job = db.get(ExecutionJob, job_id, with_for_update=True)
    now = db.scalar(select(func.now()))
    if (not job or job.deployment_run_id != run_id or job.purpose != "deploy" or job.status != "running"
            or job.lease_token != token or not job.lease_expires_at
            or job.lease_expires_at <= now or job.deadline_at <= now):
        raise ExecutionError("execution_attempt_stale")
    run = db.get(DeploymentRun, run_id, with_for_update=True)
    if run.status not in {"checking", "applying", "monitoring", "checking_health", "reconciling"}:
        raise ExecutionError("deployment_state_changed")
    return job, run


def _apply(job_id: UUID, token: UUID, run_id: UUID, item: dict) -> None:
    with Session.begin() as db:
        _, run = _fenced(db, job_id, token, run_id)
        _context(db, run)
        kind, name = item["kind"], item["metadata"]["name"]
        journal = db.scalar(select(DeploymentResource).where(
            DeploymentResource.deployment_run_id == run_id,
            DeploymentResource.kind == kind,
            DeploymentResource.name == name,
        ).with_for_update())
        if journal is None:
            journal = DeploymentResource(
                deployment_run_id=run_id, kind=kind, name=name, status="sending",
            )
            db.add(journal)
        else:
            if journal.status == "applied" and journal.uid:
                return
            journal.status = "sending"
        transition(db, run, "applying")
    with Session.begin() as db:
        _, run = _fenced(db, job_id, token, run_id)
        _context(db, run)
        journal = db.scalar(select(DeploymentResource).where(
            DeploymentResource.deployment_run_id == run_id,
            DeploymentResource.kind == kind,
            DeploymentResource.name == name,
        ).with_for_update())
        # Hold the claim and run row locks while the bounded API request is in flight.
        # A second worker cannot start a competing write after the lease expires.
        observed = apply_owned(item, str(run.application_id), journal.uid)
        journal.uid = observed["uid"]
        journal.generation = observed["generation"]
        journal.status = "applied"
        journal.error_code = None


def _observe(job_id: UUID, token: UUID, run_id: UUID, deployment: dict, image: str) -> bool:
    with Session.begin() as db:
        _, run = _fenced(db, job_id, token, run_id)
        row = db.scalar(select(DeploymentResource).where(
            DeploymentResource.deployment_run_id == run_id,
            DeploymentResource.kind == "Deployment",
            DeploymentResource.name == deployment["metadata"]["name"],
        ))
        if not row or not row.uid:
            raise ExecutionError("deployment_inventory_missing")
        status = rollout_status(row.name, str(run.application_id), row.uid, image, row.generation)
        run.rollout_json = status
        transition(db, run, "monitoring")
    if status["status"] == "failed":
        reason = status.get("reason")
        failure = {
            "ErrImagePull": "image_pull_failed",
            "ImagePullBackOff": "image_pull_failed",
            "CrashLoopBackOff": "application_start_failed",
            "CreateContainerConfigError": "dependency_configuration_missing",
            "ProgressDeadlineExceeded": "rollout_deadline_exceeded",
        }.get(reason, "rollout_failed")
        raise ExecutionError(failure)
    return status["status"] == "ready"


def _health(job_id: UUID, token: UUID, run_id: UUID, service: dict, deployment: dict, ingress: dict | None = None) -> bool:
    name = service["metadata"]["name"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    path = container["readinessProbe"]["httpGet"]["path"]
    with Session.begin() as db:
        _, run = _fenced(db, job_id, token, run_id)
        row = db.scalar(select(DeploymentResource).where(
            DeploymentResource.deployment_run_id == run_id,
            DeploymentResource.kind == "Service",
            DeploymentResource.name == name,
        ))
        if not row or not row.uid:
            raise ExecutionError("service_inventory_missing")
        port = service["spec"]["ports"][0]["port"]
        result = service_health(name, path, str(run.application_id), row.uid, port)
        if result["status"] == "passed" and ingress is not None:
            rule = ingress["spec"]["rules"][0]
            external_path = rule["http"]["paths"][0]["path"]
            external = external_health(
                rule["host"], external_path, bool(ingress["spec"].get("tls")),
            )
            result = {
                **result, "status": external["status"],
                "code": external.get("code"), "external": external,
            }
        run.health_json = result
        transition(db, run, "checking_health")
        if result["status"] == "passed":
            transition(db, run, "succeeded")
            return True
        return False


async def _execute(job_id: UUID, token: UUID, run_id: UUID) -> None:
    with Session() as db:
        run = db.get(DeploymentRun, run_id)
        if not run:
            raise ExecutionError("deployment_missing")
        candidate_observation = run.status in {"monitoring", "checking_health"}
        source, record, files, manifests = _context(db, run, observe_only=candidate_observation)
        applied = {(row.kind, row.name) for row in db.scalars(select(DeploymentResource).where(
            DeploymentResource.deployment_run_id == run_id,
            DeploymentResource.status == "applied",
            DeploymentResource.uid.is_not(None),
        )).all()}
        observing = candidate_observation and applied == {
            (item["kind"], item["metadata"]["name"]) for item in manifests
        }
        if candidate_observation and not observing:
            source, record, files, manifests = _context(db, run)
    if not observing:
        if source is not None:
            _check_github(source)
        admission = await call_kubernetes("validator", "kube.validate_manifests", {
            "files": files, "digest": record.manifest_digest,
            "application_id": str(record.application_id), "registry": local_k3s_profile()["registry"],
        })
        if admission.get("status") != "passed":
            raise ExecutionError(
                "execution_admission_unavailable" if admission.get("status") == "unavailable"
                else "execution_admission_failed",
                transient=admission.get("status") == "unavailable",
            )
        with Session.begin() as db:
            _, run = _fenced(db, job_id, token, run_id)
            if _context(db, run)[0] != source:
                raise ExecutionError("release_source_stale")
            if record.kind == "rollback":
                run.preflight_validation_json = admission
        for item in manifests:
            _apply(job_id, token, run_id, item)
    deployment = next(item for item in manifests if item["kind"] == "Deployment")
    service = next(item for item in manifests if item["kind"] == "Service")
    ingress = next((item for item in manifests if item["kind"] == "Ingress"), None)
    if not _observe(job_id, token, run_id, deployment, record.image_reference):
        raise ExecutionError("rollout_pending", transient=True)
    if not _health(job_id, token, run_id, service, deployment, ingress):
        raise ExecutionError("application_health_pending", transient=True)


def _finish(job_id: UUID, token: UUID, error: ExecutionError | None) -> None:
    with Session.begin() as db:
        job = db.get(ExecutionJob, job_id, with_for_update=True)
        now = db.scalar(select(func.now()))
        if (not job or job.purpose != "deploy" or job.status != "running" or job.lease_token != token
                or not job.lease_expires_at or job.lease_expires_at <= now):
            return
        run = db.get(DeploymentRun, job.deployment_run_id, with_for_update=True)
        job.lease_token = None
        job.lease_expires_at = None
        if error is None:
            job.status = "completed"
            job.error_code = None
            return
        job.error_code = error.code
        observing = run.status in {"monitoring", "checking_health"}
        if error.transient and now < job.deadline_at and (
            observing or job.attempts < settings().execution_max_attempts
        ):
            job.status = "retry_wait"
            job.next_dispatch_at = now + timedelta(seconds=min(
                settings().execution_retry_seconds * 2 ** min(job.attempts - 1, 6), 90,
            ))
            return
        job.status = "blocked"
        changed = db.scalar(select(DeploymentResource.id).where(
            DeploymentResource.deployment_run_id == run.id,
            DeploymentResource.status.in_({"sending", "applied"}),
        ).limit(1))
        transition(db, run, "reconciling" if changed else "failed", error_code=error.code)


def execute(job_id: str) -> None:
    identifier = UUID(job_id)
    claim = _claim(identifier)
    if claim is None:
        return
    token, run_id = claim
    error = None
    try:
        asyncio.run(_execute(identifier, token, run_id))
    except Exception as exc:
        if isinstance(exc, ExecutionError):
            error = exc
        elif isinstance(exc, ClusterError):
            error = ExecutionError(exc.code, transient=exc.code in {
                "cluster_unavailable", "cluster_access_expired",
            })
        elif isinstance(exc, GitHubPhase6Error):
            error = ExecutionError(exc.code, transient=exc.transient)
        elif isinstance(exc, GatewayError):
            error = ExecutionError(str(exc), transient=str(exc) != "cluster_access_unconfigured")
        elif isinstance(exc, ExternalProbeError):
            error = ExecutionError(str(exc), transient=str(exc) == "external_probe_unavailable")
        elif isinstance(exc, (ReleasePreparationError, ReleaseError, RollbackError, ManifestPolicyError)):
            error = ExecutionError(str(exc))
        else:
            log.exception("execution_job_unexpected job_id=%s", identifier)
            error = ExecutionError("execution_unexpected")
        log.warning("execution_job_failed job_id=%s code=%s", identifier, error.code)
    _finish(identifier, token, error)
