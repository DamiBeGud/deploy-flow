import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.analysis.jobs import finish
from app.analysis.service import enqueue, run_document
from app.api.identity import Identity, get_identity
from app.api.schemas import (
    ApprovePlan,
    AssessmentPatch,
    CreateApplication,
    CreateInfrastructurePlan,
    CreatePullRequest,
    DeploymentIntentWrite,
    StartAnalysis,
    ValidatePlan,
)
from app.bootstrap.contracts import INFRASTRUCTURE_PLAN_SCHEMA_VERSION
from app.bootstrap.generator import MANAGED_PATHS, TEMPLATE_VERSION, PlanError, render
from app.bootstrap.profiles import local_k3s_profile
from app.bootstrap.recommendations import recommend, resolve_field_recommendations
from app.config import settings
from app.kubernetes.gateway import GatewayError, call_kubernetes
from app.kubernetes.policy import ManifestPolicyError, checked_manifests, file_hashes_match
from app.persistence.commands import application_for, idempotent, run_for
from app.persistence.database import Session
from app.persistence.models import (
    AnalysisRun,
    Application,
    AssessmentVersion,
    DeliveryJob,
    DeploymentIntentVersion,
    DeploymentPolicy,
    DeploymentRun,
    Event,
    GitHubJob,
    InfrastructurePlan,
    InfrastructurePlanFile,
    InfrastructurePlanValidation,
    InfrastructurePullRequest,
    PlanApproval,
    ReleaseSpecificationRecord,
    VerifiedBuild,
)
from app.telemetry.events import TERMINAL, emit

router = APIRouter(prefix="/api/v1")
Actor = Annotated[Identity, Depends(get_identity)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _intent_document(intent: DeploymentIntentVersion) -> dict:
    return {
        "id": str(intent.id),
        "version": intent.version,
        "analysis_run_id": str(intent.analysis_run_id),
        "assessment_version": intent.assessment_version,
        **intent.document,
        "created_at": intent.created_at.isoformat(),
    }


def _plan_document(db, plan: InfrastructurePlan) -> dict:
    current = _plan_is_current(plan)
    files = db.scalars(
        select(InfrastructurePlanFile)
        .where(InfrastructurePlanFile.plan_id == plan.id)
        .order_by(InfrastructurePlanFile.path)
    ).all()
    approval = db.scalar(
        select(PlanApproval)
        .where(PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None))
        .order_by(PlanApproval.created_at.desc())
    )
    pull_request = db.scalar(select(InfrastructurePullRequest).where(InfrastructurePullRequest.plan_id == plan.id))
    previous_files = []
    if plan.previous_plan_id:
        previous_files = db.scalars(
            select(InfrastructurePlanFile).where(
                InfrastructurePlanFile.plan_id == plan.previous_plan_id
            )
        ).all()
    previous_by_path = {item.path: item.content_hash for item in previous_files}
    current_by_path = {item.path: item.content_hash for item in files}
    added = sorted(set(current_by_path) - set(previous_by_path))
    removed = sorted(set(previous_by_path) - set(current_by_path))
    changed = sorted(
        path
        for path in set(current_by_path).intersection(previous_by_path)
        if current_by_path[path] != previous_by_path[path]
    )
    validation = dict(plan.validation_json)
    validation.setdefault("schema_version", INFRASTRUCTURE_PLAN_SCHEMA_VERSION)
    validation.setdefault(
        "image_binding",
        {
            "name": "application-image",
            "repository": validation.get("provisional_image", "").rsplit(":", 1)[0],
            "preview_reference": validation.get("provisional_image", ""),
            "status": "provisional",
            "final_reference_kind": "digest",
        },
    )
    latest_cluster_validation = db.scalar(
        select(InfrastructurePlanValidation)
        .where(InfrastructurePlanValidation.plan_id == plan.id)
        .order_by(InfrastructurePlanValidation.created_at.desc(), InfrastructurePlanValidation.id.desc())
    )
    cluster_validation = (
        latest_cluster_validation.result_json
        if latest_cluster_validation
        and latest_cluster_validation.bundle_digest == plan.bundle_digest
        and latest_cluster_validation.environment_profile_hash == plan.environment_profile_hash
        and current
        else None
    )
    return {
        "schema_version": INFRASTRUCTURE_PLAN_SCHEMA_VERSION,
        "id": str(plan.id),
        "application_id": str(plan.application_id),
        "analysis_run_id": str(plan.analysis_run_id),
        "assessment_version": plan.assessment_version,
        "environment_profile_id": plan.environment_profile_id,
        "bundle_digest": plan.bundle_digest,
        "status": plan.status if current else "stale",
        "validation": validation,
        "cluster_validation": cluster_validation,
        "previous_plan_id": str(plan.previous_plan_id) if plan.previous_plan_id else None,
        "files": [
            {
                "path": item.path,
                "content": item.content,
                "hash": item.content_hash,
                "change": (
                    "added"
                    if item.path in added
                    else "changed"
                    if item.path in changed
                    else "unchanged"
                ),
            }
            for item in files
        ],
        "comparison": {
            "previous_plan_id": str(plan.previous_plan_id) if plan.previous_plan_id else None,
            "added": added,
            "changed": changed,
            "removed": removed,
        },
        "approval": {"actor_id": approval.actor_id, "created_at": approval.created_at.isoformat()}
        if approval and current
        else None,
        "pull_request": _pull_request_document(db, pull_request) if pull_request else None,
        "created_at": plan.created_at.isoformat(),
    }


def _pull_request_document(db, pull_request: InfrastructurePullRequest) -> dict:
    build = db.scalar(select(VerifiedBuild).where(
        VerifiedBuild.pull_request_id == pull_request.id,
    ).order_by(VerifiedBuild.verified_at.desc(), VerifiedBuild.run_id.desc(), VerifiedBuild.run_attempt.desc()))
    preparation = db.scalar(select(DeliveryJob).where(
        DeliveryJob.kind == "prepare", DeliveryJob.verified_build_id == build.id,
    )) if build else None
    release = db.scalar(select(ReleaseSpecificationRecord).where(
        ReleaseSpecificationRecord.verified_build_id == build.id,
    )) if build else None
    deployment = db.scalar(select(DeploymentRun).where(
        DeploymentRun.release_id == release.id,
    )) if release else None
    return {
        "id": str(pull_request.id),
        "plan_id": str(pull_request.plan_id),
        "status": pull_request.status,
        "url": pull_request.url,
        "number": pull_request.number,
        "head_sha": pull_request.head_sha,
        "merged_sha": pull_request.merged_sha,
        "error_code": pull_request.error_code,
        "verified_build": {
            "id": str(build.id),
            "source_sha": build.source_sha or build.merged_sha,
            "baseline_sha": build.merged_sha,
            "artifact_id": build.artifact_id,
            "run_id": build.run_id,
            "run_attempt": build.run_attempt,
            "image_repository": build.image_repository,
            "image_digest": build.image_digest,
        } if build else None,
        "release_preparation": {
            "job_id": str(preparation.id),
            "status": preparation.status,
            "error_code": preparation.error_code,
        } if preparation else None,
        "release": {
            "id": str(release.id), "digest": release.manifest_digest,
            "image_reference": release.image_reference,
        } if release else None,
        "deployment": {
            "id": str(deployment.id), "status": deployment.status,
            "error_code": deployment.error_code,
        } if deployment else None,
    }


def _plan_is_current(plan: InfrastructurePlan) -> bool:
    try:
        profile = local_k3s_profile()
    except ValueError:
        return False
    return (
        plan.environment_profile_id == profile["id"]
        and plan.environment_profile_hash == profile["hash"]
        and plan.template_version == TEMPLATE_VERSION
    )


def _safe_cluster_validation(value: dict) -> dict:
    status = value.get("status")
    if status not in {"passed", "failed", "unavailable"}:
        status = "unavailable"
    raw_checks = value.get("checks")
    checks = []
    for item in raw_checks[:12] if isinstance(raw_checks, list) else []:
        if not isinstance(item, dict):
            continue
        raw_details = item.get("details")
        checks.append(
            {
                "kind": str(item.get("kind") or "")[:40],
                "name": str(item.get("name") or "")[:63],
                "code": str(item.get("code") or "cluster_check_failed")[:80],
                "details": [
                    {"field": str(detail.get("field") or "")[:160], "reason": str(detail.get("reason") or "")[:80]}
                    for detail in (raw_details[:12] if isinstance(raw_details, list) else [])
                    if isinstance(detail, dict)
                ],
            }
        )
    return {
        "status": status,
        "checks": checks,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/services")
async def list_services(
    identity: Actor,
    cluster_id: str = "local-k3s",
    namespace: str = "deployflow-test",
):
    if cluster_id != "local-k3s" or namespace != "deployflow-test":
        raise HTTPException(404, "Cluster scope not found")
    try:
        return await call_kubernetes("reader", "kube.list_workloads", {})
    except GatewayError as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/services/{workload_id}")
async def get_service(workload_id: str, identity: Actor):
    parts = workload_id.split(":")
    if len(parts) != 3 or parts[0] != "deployflow-test" or parts[1] not in {"Deployment", "StatefulSet", "Job"}:
        raise HTTPException(404, "Workload not found")
    try:
        result = await call_kubernetes("reader", "kube.get_rollout_status", {"workload_id": workload_id})
    except GatewayError as exc:
        raise HTTPException(503, str(exc)) from None
    if result.get("error"):
        raise HTTPException(404, "Workload not found")
    return result


@router.get("/clusters/{cluster_id}/capabilities")
async def cluster_capabilities(cluster_id: str, identity: Actor):
    if cluster_id != "local-k3s":
        raise HTTPException(404, "Cluster not found")
    try:
        return await call_kubernetes("reader", "kube.get_capabilities", {})
    except GatewayError as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/me")
def me(identity: Actor):
    return {
        "actor_id": identity.actor_id,
        "organization_id": identity.organization_id,
        "roles": sorted(identity.roles),
        "authentication": settings().auth_mode,
        "clusters": [],
    }


@router.get("/auth/config")
def auth_config():
    config = settings()
    return {
        "mode": config.auth_mode,
        "issuer": config.oidc_issuer if config.auth_mode == "oidc" else None,
        "authorization_endpoint": config.oidc_authorization_endpoint if config.auth_mode == "oidc" else None,
        "token_endpoint": config.oidc_token_endpoint if config.auth_mode == "oidc" else None,
        "client_id": config.oidc_client_id if config.auth_mode == "oidc" else None,
    }


@router.post("/applications", status_code=201)
def create_application(body: CreateApplication, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:

        def create():
            app = Application(
                **body.model_dump(),
                organization_id=identity.organization_id,
                owner_id=identity.actor_id,
            )
            db.add(app)
            db.flush()
            return {
                "id": str(app.id),
                **body.model_dump(),
                "created_at": app.created_at.isoformat(),
            }, 201

        response, code = idempotent(
            db, identity, "applications.create", key, body.model_dump(), create
        )
    return JSONResponse(response, status_code=code)


@router.get("/applications")
def list_applications(identity: Actor, limit: int = Query(default=50, ge=1, le=100)):
    with Session() as db:
        query = select(Application).where(Application.organization_id == identity.organization_id)
        if "administrator" not in identity.roles:
            query = query.where(Application.owner_id == identity.actor_id)
        apps = db.scalars(query.order_by(Application.created_at.desc()).limit(limit)).all()
        ids = [app.id for app in apps]
        policies = {row.application_id: row.enabled for row in db.scalars(select(DeploymentPolicy).where(DeploymentPolicy.application_id.in_(ids)))}
        recent = {}
        for run, release in db.execute(select(DeploymentRun, ReleaseSpecificationRecord).join(
            ReleaseSpecificationRecord, DeploymentRun.release_id == ReleaseSpecificationRecord.id,
        ).where(DeploymentRun.application_id.in_(ids)).order_by(DeploymentRun.created_at.desc())):
            recent.setdefault(run.application_id, {"status": run.status, "source_sha": release.source_sha or release.merged_sha,
                                                   "image_reference": release.image_reference, "error_code": run.error_code})
        return [
            {
                "deployment_policy_enabled": policies.get(app.id, False),
                "latest_deployment": recent.get(app.id),
                "id": str(app.id),
                "name": app.name,
                "repository_url": app.repository_url,
                "revision": app.revision,
            }
            for app in apps
        ]


@router.get("/applications/{app_id}")
def get_application(app_id: UUID, identity: Actor):
    with Session() as db:
        app = application_for(db, app_id, identity)
        latest = db.scalar(
            select(AnalysisRun)
            .where(AnalysisRun.application_id == app.id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
        return {
            "id": str(app.id),
            "name": app.name,
            "repository_url": app.repository_url,
            "revision": app.revision,
            "lifecycle_version": 2,
            "latest_analysis": run_document(db, latest) if latest else None,
        }


@router.post("/applications/{app_id}/analyses", status_code=202)
def start_analysis(app_id: UUID, body: StartAnalysis, request: Request, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        application = application_for(db, app_id, identity)
        response, code = idempotent(
            db,
            identity,
            f"applications/{app_id}/analyses",
            key,
            body.model_dump(),
            lambda: enqueue(db, application, identity, request.state.correlation_id, body.revision),
        )
    return JSONResponse(response, status_code=code)


@router.get("/analyses/{run_id}")
def get_analysis(run_id: UUID, identity: Actor):
    with Session() as db:
        return run_document(db, run_for(db, run_id, identity))


@router.post("/analyses/{run_id}/reanalyze", status_code=202)
def reanalyze(run_id: UUID, body: StartAnalysis, request: Request, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        old = run_for(db, run_id, identity)
        application = application_for(db, old.application_id, identity)
        response, code = idempotent(
            db,
            identity,
            f"analyses/{run_id}/reanalyze",
            key,
            body.model_dump(),
            lambda: enqueue(
                db,
                application,
                identity,
                request.state.correlation_id,
                body.revision or old.revision,
            ),
        )
    return JSONResponse(response, status_code=code)


@router.post("/analyses/{run_id}/cancel")
def cancel(run_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        run = run_for(db, run_id, identity, lock=True)

        def operation():
            if run.status == "cancelled":
                return {"run_id": str(run.id), "status": run.status}, 200
            if run.status in TERMINAL:
                raise HTTPException(409, "A finished analysis cannot be cancelled")
            finish(db, run, "cancelled", "Analysis cancelled by user.")
            return {"run_id": str(run.id), "status": run.status}, 200

        response, code = idempotent(db, identity, f"analyses/{run_id}/cancel", key, {}, operation)
    return JSONResponse(response, status_code=code)


@router.patch("/analyses/{run_id}/assessment")
def patch_assessment(run_id: UUID, body: AssessmentPatch, identity: Actor, key: Key):
    identity.require_write()
    changes = body.model_dump(exclude_none=True, exclude={"expected_version"})
    if not changes:
        raise HTTPException(422, "Provide port or health_path")
    with Session.begin() as db:
        run = run_for(db, run_id, identity, lock=True)

        def operation():
            if run.status not in {"completed", "needs_input"}:
                raise HTTPException(409, "Assessment is not ready for review")
            if run.assessment_version != body.expected_version:
                raise HTTPException(409, "Assessment version changed; reload before editing")
            previous = db.get(AssessmentVersion, (run.id, run.assessment_version))
            document = {**previous.document}
            if document.get("schema_version") == "infrastructure-assessment-v2":
                review = {**document.get("review", {})}
                if "port" in changes:
                    review["port"] = changes["port"]
                    document["network"] = {
                        **document.get("network", {}),
                        "ports": [changes["port"]],
                    }
                if "health_path" in changes:
                    review["health_path"] = changes["health_path"]
                    document["health_checks"] = [changes["health_path"]]
                document["review"] = review
                answers = changes.get("question_answers", {})
                known_questions = {question["id"] for question in document.get("questions", [])}
                if set(answers).difference(known_questions):
                    raise HTTPException(422, "Question answer does not belong to this assessment")
                remaining = []
                for question in document.get("questions", []):
                    answer = answers.get(question["id"])
                    if answer is None:
                        remaining.append(question)
                        continue
                    if question["answer_type"] == "integer" and not isinstance(answer, int):
                        raise HTTPException(422, "Question answer type is invalid")
                    if question["answer_type"] == "boolean" and not isinstance(answer, bool):
                        raise HTTPException(422, "Question answer type is invalid")
                    if (
                        question["answer_type"] == "single_choice"
                        and answer not in question["choices"]
                    ):
                        raise HTTPException(422, "Question answer is not an allowed choice")
                    document.setdefault("findings", []).append(
                        {
                            "id": f"finding-user-{run.assessment_version + 1}-{len(document['findings']) + 1}",
                            "category": question["field_path"],
                            "value": answer,
                            "status": "user_supplied",
                            "confidence": 1.0,
                            "evidence_ids": [],
                        }
                    )
                document["questions"] = remaining
                if not any(question["required"] for question in remaining):
                    document["completion"] = "complete"
                    document["incomplete_reason"] = None
            else:
                for field, value in changes.items():
                    document[field] = {"value": value, "source": "user"}
            run.assessment_version += 1
            for plan in db.scalars(
                select(InfrastructurePlan).where(
                    InfrastructurePlan.analysis_run_id == run.id,
                    InfrastructurePlan.status.in_({"proposed", "approved"}),
                )
            ):
                plan.status = "stale"
                for approval in db.scalars(
                    select(PlanApproval).where(
                        PlanApproval.plan_id == plan.id,
                        PlanApproval.invalidated_at.is_(None),
                    )
                ):
                    approval.invalidated_at = db.scalar(select(func.now()))
                    approval.invalidated_reason = "assessment_changed"
            if run.status == "needs_input" and not any(
                question["required"] for question in document.get("questions", [])
            ):
                run.status = "completed"
            db.add(
                AssessmentVersion(
                    run_id=run.id,
                    version=run.assessment_version,
                    document=document,
                    actor_id=identity.actor_id,
                )
            )
            emit(db, run, "analysis.assessment_updated", "User corrections saved as a new version.")
            return {
                "run_id": str(run.id),
                "version": run.assessment_version,
                "assessment": document,
            }, 200

        response, code = idempotent(
            db, identity, f"analyses/{run_id}/assessment", key, body.model_dump(), operation
        )
    return JSONResponse(response, status_code=code)


@router.get("/environment-profiles")
def environment_profiles(identity: Actor):
    try:
        profile = local_k3s_profile()
    except ValueError:
        return []
    return [
        {
            key: profile[key]
            for key in (
                "id",
                "namespace",
                "registry",
                "default_image",
                "ingress_class",
                "hostname_suffix",
                "max_replicas",
                "hash",
            )
        }
    ]


@router.get("/connections")
def connections(identity: Actor):
    """Phase 4 ships the safe catalogue shape but no preconfigured backing services."""
    return []


@router.get("/applications/{app_id}/kubernetes-recommendation")
def kubernetes_recommendation(app_id: UUID, analysis_run_id: UUID, identity: Actor):
    """Return one complete, editable Kubernetes recommendation without asking the user."""
    with Session() as db:
        application_for(db, app_id, identity)
        run = run_for(db, analysis_run_id, identity)
        if run.application_id != app_id or run.status not in {"completed", "needs_input"}:
            raise HTTPException(409, "Assessment is not ready for Kubernetes recommendations")
        assessment = db.get(AssessmentVersion, (run.id, run.assessment_version))
        if assessment is None:
            raise HTTPException(409, "Assessment has no reviewable version")
        try:
            result = recommend(
                assessment.document, local_k3s_profile(), run.revision, run.assessment_version
            )
            latest_version = db.scalar(
                select(DeploymentIntentVersion.version)
                .where(DeploymentIntentVersion.application_id == app_id)
                .order_by(DeploymentIntentVersion.version.desc())
            )
            result["intent"]["version"] = latest_version or 0
            return result
        except ValueError as exc:
            raise HTTPException(409, "Kubernetes profile is not configured") from exc


@router.get("/applications/{app_id}/deployment-intent")
def get_deployment_intent(app_id: UUID, analysis_run_id: UUID, identity: Actor):
    with Session() as db:
        application_for(db, app_id, identity)
        intent = db.scalar(
            select(DeploymentIntentVersion)
            .where(
                DeploymentIntentVersion.application_id == app_id,
                DeploymentIntentVersion.analysis_run_id == analysis_run_id,
            )
            .order_by(DeploymentIntentVersion.version.desc())
        )
        return _intent_document(intent) if intent else None


@router.put("/applications/{app_id}/deployment-intent")
def put_deployment_intent(app_id: UUID, body: DeploymentIntentWrite, identity: Actor, key: Key):
    identity.require_write()
    run_id = UUID(body.analysis_run_id)
    with Session.begin() as db:
        application = application_for(db, app_id, identity)
        run = run_for(db, run_id, identity, lock=True)
        if run.application_id != application.id or run.status not in {"completed", "needs_input"}:
            raise HTTPException(409, "Assessment is not ready for delivery settings")
        if run.assessment_version != body.expected_assessment_version:
            raise HTTPException(409, "Assessment version changed; reload before saving")
        assessment = db.get(AssessmentVersion, (run.id, run.assessment_version))
        if assessment is None:
            raise HTTPException(409, "Assessment has no reviewable version")

        def operation():
            latest = db.scalar(
                select(DeploymentIntentVersion)
                .where(DeploymentIntentVersion.application_id == app_id)
                .order_by(DeploymentIntentVersion.version.desc())
            )
            latest_version = latest.version if latest else 0
            if latest_version != body.expected_version:
                raise HTTPException(409, "Delivery settings changed; reload before saving")
            document = body.model_dump(
                exclude={"expected_version", "analysis_run_id", "expected_assessment_version"}
            )
            recommendation = recommend(
                assessment.document, local_k3s_profile(), run.revision, run.assessment_version
            )
            document["field_recommendations"] = resolve_field_recommendations(
                document, recommendation["intent"]["field_recommendations"]
            )
            if (
                latest is not None
                and latest.analysis_run_id == run.id
                and latest.assessment_version == run.assessment_version
                and latest.document == document
            ):
                return _intent_document(latest), 200
            intent = DeploymentIntentVersion(
                application_id=app_id,
                analysis_run_id=run.id,
                assessment_version=run.assessment_version,
                version=latest_version + 1,
                document=document,
                actor_id=identity.actor_id,
            )
            db.add(intent)
            db.flush()
            for plan in db.scalars(
                select(InfrastructurePlan).where(
                    InfrastructurePlan.application_id == app_id,
                    InfrastructurePlan.status.in_({"proposed", "approved"}),
                )
            ):
                plan.status = "stale"
                for approval in db.scalars(
                    select(PlanApproval).where(
                        PlanApproval.plan_id == plan.id,
                        PlanApproval.invalidated_at.is_(None),
                    )
                ):
                    approval.invalidated_reason = "intent_changed"
                    approval.invalidated_at = db.scalar(select(func.now()))
            return _intent_document(intent), 201

        response, code = idempotent(
            db,
            identity,
            f"applications/{app_id}/deployment-intent",
            key,
            body.model_dump(),
            operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/applications/{app_id}/infrastructure-plans", status_code=201)
def create_infrastructure_plan(
    app_id: UUID, body: CreateInfrastructurePlan, identity: Actor, key: Key
):
    identity.require_write()
    run_id = UUID(body.analysis_run_id)
    with Session.begin() as db:
        application = application_for(db, app_id, identity)
        run = run_for(db, run_id, identity)
        if (
            run.application_id != application.id
            or run.assessment_version != body.expected_assessment_version
        ):
            raise HTTPException(409, "Assessment version changed; reload before generating")
        assessment = db.get(AssessmentVersion, (run.id, run.assessment_version))
        intent = db.scalar(
            select(DeploymentIntentVersion).where(
                DeploymentIntentVersion.application_id == app_id,
                DeploymentIntentVersion.analysis_run_id == run.id,
                DeploymentIntentVersion.version == body.intent_version,
            )
        )
        if intent is None or intent.assessment_version != run.assessment_version:
            raise HTTPException(409, "Saved delivery settings are stale; save them again")

        def operation():
            paths = set((run.scanner_facts_json or {}).get("paths", []))
            conflict = sorted(paths.intersection(MANAGED_PATHS))
            if conflict:
                raise HTTPException(409, f"Repository already contains managed file: {conflict[0]}")
            try:
                profile = local_k3s_profile()
                profile["application_id"] = str(app_id)
                bundle = render(assessment.document, intent.document, profile)
            except (PlanError, ValueError) as exc:
                raise HTTPException(
                    422, f"Infrastructure plan cannot be generated: {exc}"
                ) from None
            previous = db.scalar(
                select(InfrastructurePlan)
                .where(
                    InfrastructurePlan.application_id == app_id,
                    InfrastructurePlan.environment_profile_id == profile["id"],
                )
                .order_by(InfrastructurePlan.created_at.desc())
            )
            if previous:
                previous.status = "stale"
                for approval in db.scalars(
                    select(PlanApproval).where(
                        PlanApproval.plan_id == previous.id, PlanApproval.invalidated_at.is_(None)
                    )
                ):
                    approval.invalidated_reason = "superseded_by_new_plan"
                    approval.invalidated_at = db.scalar(select(func.now()))
            plan = InfrastructurePlan(
                application_id=app_id,
                analysis_run_id=run.id,
                assessment_version=run.assessment_version,
                intent_version_id=intent.id,
                environment_profile_id=profile["id"],
                environment_profile_hash=profile["hash"],
                template_version=TEMPLATE_VERSION,
                bundle_digest=bundle.digest,
                validation_json=bundle.validation,
                previous_plan_id=previous.id if previous else None,
                created_by=identity.actor_id,
            )
            db.add(plan)
            db.flush()
            for path, content in bundle.files.items():
                db.add(
                    InfrastructurePlanFile(
                        plan_id=plan.id,
                        path=path,
                        content=content,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    )
                )
            emit(db, run, "plan.generated", "Infrastructure bundle generated for review.")
            return _plan_document(db, plan), 201

        response, code = idempotent(
            db,
            identity,
            f"applications/{app_id}/infrastructure-plans",
            key,
            body.model_dump(),
            operation,
        )
    return JSONResponse(response, status_code=code)


@router.get("/applications/{app_id}/infrastructure-plans/latest")
def get_latest_infrastructure_plan(app_id: UUID, analysis_run_id: UUID, identity: Actor):
    with Session() as db:
        application_for(db, app_id, identity)
        run = run_for(db, analysis_run_id, identity)
        if run.application_id != app_id:
            raise HTTPException(404, "Infrastructure plan not found")
        plan = db.scalar(
            select(InfrastructurePlan)
            .where(
                InfrastructurePlan.application_id == app_id,
                InfrastructurePlan.analysis_run_id == analysis_run_id,
            )
            .order_by(InfrastructurePlan.created_at.desc(), InfrastructurePlan.id.desc())
        )
        return _plan_document(db, plan) if plan else None


@router.get("/infrastructure-plans/{plan_id}")
def get_infrastructure_plan(plan_id: UUID, identity: Actor):
    with Session() as db:
        plan = db.get(InfrastructurePlan, plan_id)
        if plan is None:
            raise HTTPException(404, "Infrastructure plan not found")
        application_for(db, plan.application_id, identity)
        return _plan_document(db, plan)


@router.post("/infrastructure-plans/{plan_id}/validations", status_code=201)
async def validate_infrastructure_plan(
    plan_id: UUID, body: ValidatePlan, identity: Actor, key: Key
):
    identity.require_write()
    with Session() as db:
        plan = db.get(InfrastructurePlan, plan_id)
        if plan is None:
            raise HTTPException(404, "Infrastructure plan not found")
        application_for(db, plan.application_id, identity)
        if not _plan_is_current(plan) or plan.bundle_digest != body.expected_digest or plan.status == "stale":
            raise HTTPException(409, "Plan is stale; generate a new bundle")
        file_rows = db.scalars(
            select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan.id)
        ).all()
        files = {row.path: row.content for row in file_rows}
        stored_hashes = [dict(content=row.content, hash=row.content_hash) for row in file_rows]
        digest = plan.bundle_digest
        profile_hash = plan.environment_profile_hash
        application_id = str(plan.application_id)
        profile = local_k3s_profile()

    if not file_hashes_match(stored_hashes):
        result = {"status": "failed", "checks": [{"code": "file_hash_mismatch"}]}
    else:
        try:
            checked_manifests(
                files,
                digest=digest,
                namespace=profile["namespace"],
                application_id=application_id,
                registry=profile["registry"],
            )
            result = await call_kubernetes(
                "validator",
                "kube.validate_manifests",
                {
                    "files": files,
                    "digest": digest,
                    "application_id": application_id,
                    "registry": profile["registry"],
                },
            )
        except ManifestPolicyError as exc:
            result = {"status": "failed", "checks": [{"code": str(exc)}]}
        except GatewayError as exc:
            result = {"status": "unavailable", "checks": [{"code": str(exc)}]}
    result = _safe_cluster_validation(result)

    with Session.begin() as db:
        plan = db.get(InfrastructurePlan, plan_id)
        if plan is None or not _plan_is_current(plan) or plan.bundle_digest != digest or plan.status == "stale":
            raise HTTPException(409, "Plan changed during validation; retry")

        def operation():
            validation = InfrastructurePlanValidation(
                plan_id=plan.id,
                bundle_digest=digest,
                environment_profile_hash=profile_hash,
                result_json=result,
                actor_id=identity.actor_id,
            )
            db.add(validation)
            db.flush()
            return {"id": str(validation.id), **result}, 201

        response, code = idempotent(
            db,
            identity,
            f"infrastructure-plans/{plan_id}/validations",
            key,
            body.model_dump(),
            operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/infrastructure-plans/{plan_id}/approve")
def approve_infrastructure_plan(plan_id: UUID, body: ApprovePlan, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        plan = db.get(InfrastructurePlan, plan_id)
        if plan is None:
            raise HTTPException(404, "Infrastructure plan not found")
        application_for(db, plan.application_id, identity)

        def operation():
            if (
                plan.status != "proposed"
                or plan.bundle_digest != body.expected_digest
                or not _plan_is_current(plan)
            ):
                raise HTTPException(409, "Plan is stale; generate and review a new bundle")
            approval = PlanApproval(
                plan_id=plan.id, bundle_digest=plan.bundle_digest, actor_id=identity.actor_id
            )
            db.add(approval)
            plan.status = "approved"
            return {
                "plan_id": str(plan.id),
                "bundle_digest": plan.bundle_digest,
                "status": "approved",
            }, 201

        response, code = idempotent(
            db,
            identity,
            f"infrastructure-plans/{plan_id}/approve",
            key,
            body.model_dump(),
            operation,
        )
    return JSONResponse(response, status_code=code)


@router.post("/infrastructure-plans/{plan_id}/pull-requests", status_code=202)
def create_infrastructure_pull_request(plan_id: UUID, body: CreatePullRequest, identity: Actor, key: Key):
    identity.require_write()
    config = settings()
    if not (config.github_app_id and config.github_installation_id and config.github_private_key_path):
        raise HTTPException(503, "GitHub App write access is not configured")
    with Session.begin() as db:
        plan = db.get(InfrastructurePlan, plan_id, with_for_update=True)
        if plan is None:
            raise HTTPException(404, "Infrastructure plan not found")
        application = application_for(db, plan.application_id, identity)

        def operation():
            if plan.status not in {"proposed", "approved"} or plan.bundle_digest != body.expected_digest or not _plan_is_current(plan):
                raise HTTPException(409, "Plan is stale; generate and review a new bundle")
            existing = db.scalar(select(InfrastructurePullRequest).where(InfrastructurePullRequest.plan_id == plan.id))
            if existing:
                return _pull_request_document(db, existing), 202
            validation = db.scalar(select(InfrastructurePlanValidation).where(InfrastructurePlanValidation.plan_id == plan.id).order_by(InfrastructurePlanValidation.created_at.desc(), InfrastructurePlanValidation.id.desc()))
            if not validation or validation.bundle_digest != plan.bundle_digest or validation.environment_profile_hash != plan.environment_profile_hash or validation.result_json.get("status") != "passed":
                raise HTTPException(409, "A passing cluster admission check is required before creating the PR")
            run = db.get(AnalysisRun, plan.analysis_run_id)
            intent = db.get(DeploymentIntentVersion, plan.intent_version_id)
            if not run or not re.fullmatch(r"[0-9a-f]{40}", run.commit_sha or ""):
                raise HTTPException(409, "Repository analysis has no pinned Git commit")
            assessment = db.get(AssessmentVersion, (run.id, plan.assessment_version))
            if not assessment or (assessment.document.get("repository") or {}).get("commit_sha") != run.commit_sha:
                raise HTTPException(409, "Assessment does not match the pinned Git commit")
            if not intent or not intent.document.get("build", {}).get("test_command"):
                raise HTTPException(409, "A reviewed test command is required before creating the PR")
            files = db.scalars(select(InfrastructurePlanFile).where(InfrastructurePlanFile.plan_id == plan.id)).all()
            if not files or not file_hashes_match([{"content": row.content, "hash": row.content_hash} for row in files]):
                raise HTTPException(409, "Stored bundle file hashes do not match")
            match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", application.repository_url)
            if not match:
                raise HTTPException(409, "Repository URL is not a supported GitHub URL")
            approval = db.scalar(select(PlanApproval).where(PlanApproval.plan_id == plan.id, PlanApproval.invalidated_at.is_(None)))
            if approval and approval.bundle_digest != plan.bundle_digest:
                raise HTTPException(409, "Approval does not match the current bundle")
            if not approval:
                db.add(PlanApproval(plan_id=plan.id, bundle_digest=plan.bundle_digest, actor_id=identity.actor_id))
            plan.status = "approved"
            pull_request = InfrastructurePullRequest(
                plan_id=plan.id, owner=match.group(1), repository=match.group(2),
                base_branch=intent.document["build"]["branch"], base_sha=run.commit_sha,
                head_branch=f"deployflow/plan-{plan.id}", status="queued",
            )
            db.add(pull_request)
            db.flush()
            db.add(GitHubJob(kind="create", pull_request_id=pull_request.id, status="queued", attempts=0))
            return _pull_request_document(db, pull_request), 202

        response, code = idempotent(db, identity, f"infrastructure-plans/{plan_id}/pull-requests", key, body.model_dump(), operation)
    return JSONResponse(response, status_code=code)


@router.get("/pull-requests/{pull_request_id}")
def get_infrastructure_pull_request(pull_request_id: UUID, identity: Actor):
    with Session() as db:
        pull_request = db.get(InfrastructurePullRequest, pull_request_id)
        if pull_request is None:
            raise HTTPException(404, "Pull request not found")
        plan = db.get(InfrastructurePlan, pull_request.plan_id)
        application_for(db, plan.application_id, identity)
        return _pull_request_document(db, pull_request)


@router.post("/pull-requests/{pull_request_id}/refresh", status_code=202)
def refresh_infrastructure_pull_request(pull_request_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        pull_request = db.get(InfrastructurePullRequest, pull_request_id)
        if pull_request is None:
            raise HTTPException(404, "Pull request not found")
        plan = db.get(InfrastructurePlan, pull_request.plan_id)
        application_for(db, plan.application_id, identity)

        def operation():
            db.add(GitHubJob(kind="reconcile", pull_request_id=pull_request.id, status="queued", attempts=0))
            return _pull_request_document(db, pull_request), 202

        response, code = idempotent(db, identity, f"pull-requests/{pull_request_id}/refresh", key, {}, operation)
    return JSONResponse(response, status_code=code)


@router.post("/pull-requests/{pull_request_id}/retry", status_code=202)
def retry_infrastructure_pull_request(pull_request_id: UUID, identity: Actor, key: Key):
    identity.require_write()
    with Session.begin() as db:
        pull_request = db.get(InfrastructurePullRequest, pull_request_id, with_for_update=True)
        if pull_request is None:
            raise HTTPException(404, "Pull request not found")
        plan = db.get(InfrastructurePlan, pull_request.plan_id)
        application_for(db, plan.application_id, identity)

        def operation():
            if pull_request.status != "failed" or pull_request.number is not None or plan.status != "approved" or not _plan_is_current(plan):
                raise HTTPException(409, "This PR request cannot be retried")
            pull_request.status = "queued"
            pull_request.error_code = None
            db.add(GitHubJob(kind="create", pull_request_id=pull_request.id, status="queued", attempts=0))
            return _pull_request_document(db, pull_request), 202

        response, code = idempotent(db, identity, f"pull-requests/{pull_request_id}/retry", key, {}, operation)
    return JSONResponse(response, status_code=code)


def event_snapshot(run_id, identity, cursor):
    with Session() as db:
        run = run_for(db, run_id, identity)
        if cursor > run.event_sequence:
            raise HTTPException(400, "Event cursor is ahead of this run")
        # Bound to the observed sequence: if completion commits between these two
        # reads, its events and terminal status are returned together next poll.
        rows = db.scalars(
            select(Event)
            .where(
                Event.run_id == run_id,
                Event.event_id > cursor,
                Event.event_id <= run.event_sequence,
            )
            .order_by(Event.event_id)
            .limit(100)
        ).all()
        events = [
            {
                "event_id": e.event_id,
                "run_id": str(e.run_id),
                "type": e.kind,
                "occurred_at": e.occurred_at.isoformat(),
                **e.payload,
            }
            for e in rows
        ]
        return events, run.status in TERMINAL, run.event_sequence


@router.get("/analyses/{run_id}/events")
async def events(
    run_id: UUID,
    request: Request,
    identity: Actor,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: int = Query(default=0, ge=0),
):
    try:
        cursor = int(last_event_id) if last_event_id is not None else after
        if cursor < 0:
            raise ValueError
    except ValueError:
        raise HTTPException(400, "Last-Event-ID must be a non-negative integer") from None
    initial = await run_in_threadpool(event_snapshot, run_id, identity, cursor)

    async def stream():
        nonlocal cursor
        snapshot = initial
        ticks = 0
        while not await request.is_disconnected():
            batch, terminal, last_sequence = snapshot
            for event in batch:
                cursor = event["event_id"]
                data = json.dumps(jsonable_encoder(event))
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n"
            if terminal and cursor >= last_sequence:
                return
            ticks += 1
            if ticks % 20 == 0:
                yield ": heartbeat\n\n"
            if cursor >= last_sequence:
                await asyncio.sleep(0.5)
            snapshot = await run_in_threadpool(event_snapshot, run_id, identity, cursor)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
