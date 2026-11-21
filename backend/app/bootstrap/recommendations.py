"""Evidence-first Kubernetes deployment recommendations; this module never contacts a cluster."""

from __future__ import annotations

import re
from copy import deepcopy

from app.analysis.command_inference import (
    PHASE_FIELDS,
    command_blocker,
    command_inference,
    scanner_commands,
)
from app.bootstrap.contracts import (
    BUILD_SPECIFICATION_SCHEMA_VERSION,
    DEPLOYMENT_INTENT_SCHEMA_VERSION,
)
from app.bootstrap.postgres import postgres_for_source
from app.bootstrap.redis import redis_for_source
from app.bootstrap.tokens import token_for_source


def _nested_value(document: dict, field_path: str):
    value = document
    for part in field_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def resolve_field_recommendations(intent: dict, recommendations: dict[str, dict]) -> dict:
    """Bind immutable recommendations to the current effective intent values."""

    resolved = {}
    for field_path, recommendation in recommendations.items():
        item = deepcopy(recommendation)
        recommendation_origin = item.get("recommendation_origin", item["origin"])
        effective = _nested_value(intent, field_path)
        item["recommendation_origin"] = recommendation_origin
        item["effective_value"] = effective
        item["overridden"] = effective != item["recommended_value"]
        item["origin"] = "user_supplied" if item["overridden"] else recommendation_origin
        if "selector" in item:
            item["recommendation_selector"] = item.get("recommendation_selector", item["selector"])
            item["selector"] = "user" if item["overridden"] else item["recommendation_selector"]
        resolved[field_path] = item
    return resolved


def recommend(
    assessment: dict, profile: dict, revision: str, assessment_version: int, *, application_id: str = ""
) -> dict:
    review = assessment.get("review", {})
    workflow = assessment.get("workflow", {})
    family = review.get("runtime") or "unknown"
    configuration = assessment.get("configuration", {})
    variables = sorted(
        set(
            configuration.get("required_variables", [])
            + configuration.get("optional_variables", [])
        )
    )
    evidence_by_id = {item.get("id"): item for item in assessment.get("evidence", [])}
    findings = assessment.get("findings", [])
    secret_markers = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY")

    def finding_evidence(category: str) -> dict | None:
        finding = next((item for item in findings if item.get("category") == category), None)
        if not finding:
            return None
        source = next(
            (
                evidence_by_id.get(evidence_id)
                for evidence_id in finding.get("evidence_ids", [])
                if evidence_by_id.get(evidence_id)
            ),
            None,
        )
        if not source:
            return None
        return {"file_path": source.get("file_path"), "line_start": source.get("line_start")}

    def configuration_item(name: str) -> dict | None:
        if name in {"PORT", "DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "QUEUE_NAMESPACE"} or any(
            marker in name for marker in secret_markers
        ):
            return None
        finding = next(
            (
                item
                for item in findings
                if item.get("category") == "configuration.variable" and item.get("value") == name
            ),
            {},
        )
        source = next(
            (
                evidence_by_id.get(item)
                for item in finding.get("evidence_ids", [])
                if evidence_by_id.get(item)
            ),
            None,
        )
        proposed = {"NODE_ENV": "production", "HOST": "0.0.0.0"}.get(name)
        purpose = {
            "NODE_ENV": "Selects production runtime behavior.",
            "HOST": "Controls the host address the application listens on.",
            "APP_GREETING": "Controls the application greeting shown to users.",
        }.get(name, "Detected in source; its purpose could not be inferred safely.")
        return {
            "name": name,
            "required": name in configuration.get("required_variables", []),
            "recommended_value": proposed,
            "purpose": purpose,
            "source": "Kubernetes policy default"
            if proposed is not None
            else "repository evidence",
            "confidence": 0.9 if proposed else float(finding.get("confidence", 0.6)),
            "evidence": {
                "file_path": source.get("file_path"),
                "line_start": source.get("line_start"),
            }
            if source
            else None,
        }

    application_configuration = [item for name in variables if (item := configuration_item(name))]
    evidence = "repository evidence" if assessment.get("evidence") else "Kubernetes policy default"
    repository = assessment.get("repository", {})
    slug = "/".join(str(repository.get(key) or "") for key in ("owner", "name")).lower()
    registry = profile.get("registry", "").rstrip("/")
    repository_image = family in {"nodejs", "python", "ruby", "java"} and bool(
        re.fullmatch(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*", slug)
    ) and bool(registry)
    image = f"{registry}/{slug.rsplit('/', 1)[1]}" if repository_image and "/" in registry else (
        f"{registry}/{slug}" if repository_image else profile["default_image"]
    )
    inference = command_inference(assessment)
    resolutions = inference.get("commands", {})
    commands = {
        field: text.split() if text else None for field, text in scanner_commands(assessment).items()
    }
    for phase, resolution in resolutions.items():
        commands[PHASE_FIELDS[phase]] = resolution.get("argv")
    port = inference.get("port") or review.get("port")
    health = (inference.get("health") or {}).get("path") or review.get("health_path")
    intent = {
        "schema_version": DEPLOYMENT_INTENT_SCHEMA_VERSION,
        "version": 0,
        "assessment_version": assessment_version,
        "environment_profile_id": profile["id"],
        "connectivity": {
            "port": port or 3000,
            "health_path": health or "/healthz",
            "replicas": 1,
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
            # Unknown optional values are deliberately omitted. An empty value is a
            # real Kubernetes configuration choice, not a placeholder for the user.
            "environment": {
                item["name"]: item["recommended_value"]
                for item in application_configuration
                if item["recommended_value"] is not None
            },
            "exposure": {
                "kind": "internal",
                "hostname": f"{assessment.get('application', {}).get('name', 'application')}{profile['hostname_suffix']}",
                "path": "/",
            },
        },
        "build": {
            "schema_version": BUILD_SPECIFICATION_SCHEMA_VERSION,
            "branch": revision,
            "image": image,
            "build_context": ".",
            "install_command": commands["install_command"] or [],
            "test_command": commands["test_command"],
            "build_command": commands["build_command"],
            "start_command": commands["start_command"] or [],
        },
    }
    port_from_repository = bool(port)
    health_from_repository = bool(health)
    field_recommendations = {
        "connectivity.port": {
            "recommended_value": intent["connectivity"]["port"],
            "origin": "inferred" if port_from_repository else "policy_default",
            "source": "repository evidence"
            if port_from_repository
            else "Kubernetes policy default",
            "confidence": 0.95 if port_from_repository else 0.6,
            "explanation": "Port exposed by the application and Kubernetes Service.",
            "evidence": finding_evidence("network.port"),
        },
        "connectivity.health_path": {
            "recommended_value": intent["connectivity"]["health_path"],
            "origin": "inferred" if health_from_repository else "policy_default",
            "source": "repository evidence"
            if health_from_repository
            else "Kubernetes policy default",
            "confidence": 0.9 if health_from_repository else 0.6,
            "explanation": "HTTP endpoint used by Kubernetes readiness and liveness probes.",
            "evidence": finding_evidence("health_check"),
        },
        "connectivity.replicas": {
            "recommended_value": 1,
            "origin": "policy_default",
            "source": "Kubernetes policy default",
            "confidence": 0.7,
            "explanation": "Safe starting replica count for the local k3s environment.",
            "evidence": None,
        },
        "connectivity.resources": {
            "recommended_value": intent["connectivity"]["resources"],
            "origin": "policy_default",
            "source": "Kubernetes policy default",
            "confidence": 0.7,
            "explanation": "Conservative starting requests and limits; no runtime measurements exist yet.",
            "evidence": None,
        },
        "connectivity.exposure.kind": {
            "recommended_value": "internal",
            "origin": "policy_default",
            "source": "Kubernetes policy default",
            "confidence": 0.8,
            "explanation": "Keep the service cluster-internal unless public access is required.",
            "evidence": None,
        },
        "connectivity.exposure.hostname": {
            "recommended_value": intent["connectivity"]["exposure"]["hostname"],
            "origin": "policy_default",
            "source": "Kubernetes environment profile",
            "confidence": 0.8,
            "explanation": "Policy-compliant hostname reserved if external exposure is enabled.",
            "evidence": None,
        },
        "connectivity.exposure.path": {
            "recommended_value": "/",
            "origin": "policy_default",
            "source": "Kubernetes policy default",
            "confidence": 0.9,
            "explanation": "Serve the application from the hostname root.",
            "evidence": None,
        },
        "build.branch": {
            "recommended_value": revision,
            "origin": "explicit",
            "source": "requested repository revision",
            "confidence": 1.0,
            "explanation": "Base branch or revision selected for repository analysis.",
            "evidence": None,
        },
        "build.image": {
            "recommended_value": image,
            "origin": "policy_default",
            "source": "Allowed registry and analyzed repository" if repository_image else "Kubernetes environment profile",
            "confidence": 1.0,
            "explanation": "Allowlisted container registry and image repository for this environment.",
            "evidence": None,
        },
    }
    if inference.get("port") and (entry := resolutions["start"]["selected"]):
        field_recommendations["connectivity.port"].update(
            source="Agent recommendation from production bind",
            explanation="The Service port must match the launcher bind.",
            evidence=entry["source"],
            evidence_ids=entry["candidate"]["evidence_ids"],
            selector="agent",
        )
    if entry := inference.get("health"):
        field_recommendations["connectivity.health_path"].update(
            source="Agent recommendation from application route",
            evidence=entry["source"],
            evidence_ids=entry["evidence_ids"],
            selector="agent",
        )
    for phase, command_name in PHASE_FIELDS.items():
        from_repository = bool(commands[command_name])
        category = {
            "install_command": "workflow.install_command",
            "test_command": "workflow.test_command",
            "build_command": "workflow.build_command",
            "start_command": "runtime.command",
        }[command_name]
        recommendation = {
            "recommended_value": intent["build"][command_name],
            "origin": "inferred" if from_repository else "policy_default",
            "source": "repository evidence" if from_repository else "No command detected",
            "confidence": 0.95 if from_repository else 0,
            "explanation": (
                f"Source-backed {command_name.replace('_', ' ')}."
                if from_repository else "Review this field if the application requires this command."
            ),
            "evidence": finding_evidence(category),
        }
        if resolution := resolutions.get(phase):
            selected = resolution.get("selected")
            recommendation.update(
                selector=resolution["selector"],
                status=resolution["status"],
                source=(
                    "Agent recommendation from repository declaration" if selected
                    else "Scanner convention" if resolution["selector"] == "scanner"
                    else "Review required" if resolution["status"] != "missing" or phase != "build"
                    else "No optional build detected"
                ),
                explanation=resolution["reason"],
                alternatives=[
                    {
                        "argv": item["candidate"]["argv"],
                        "context": item["candidate"]["context"],
                        "evidence": item["source"],
                        "reason": item["reason"],
                    }
                    for item in resolution["alternatives"]
                ],
            )
            if selected:
                recommendation.update(
                    evidence=selected["source"],
                    evidence_ids=selected["candidate"]["evidence_ids"],
                )
        field_recommendations[f"build.{command_name}"] = recommendation
    for item in application_configuration:
        field_recommendations[f"connectivity.environment.{item['name']}"] = {
            "recommended_value": item["recommended_value"],
            "origin": "policy_default" if item["recommended_value"] is not None else "inferred",
            "source": item["source"],
            "confidence": item["confidence"],
            "explanation": item["purpose"],
            "evidence": item["evidence"],
        }
    intent["field_recommendations"] = resolve_field_recommendations(intent, field_recommendations)
    postgres = postgres_for_source(assessment, application_id)
    redis = redis_for_source(assessment, application_id)
    token = token_for_source(assessment, application_id)
    generation_blocker = None
    if family == "ruby" and not postgres:
        generation_blocker = (
            "Rails commands were detected. Deployment requires approved database and secret "
            "references and a migration procedure for this target."
        )
        if workflow.get("migration_command") == "bundle exec rails db:migrate":
            generation_blocker += " Source migration command: bundle exec rails db:migrate."
    elif family not in {"nodejs", "python", "java", "ruby"}:
        generation_blocker = next(
            iter(assessment.get("unknowns", [])), "No supported runtime was detected."
        )
    elif family != "nodejs" and (not port_from_repository or not health_from_repository):
        generation_blocker = "Bundle generation needs a source-backed service port and health route."
    elif not (postgres or redis or token) and (
        configuration.get("required_variables")
        or assessment.get("dependencies", {}).get("detected_services")
    ):
        generation_blocker = "Detected service dependencies need approved configuration bindings."
    generation_blocker = generation_blocker or command_blocker(inference)
    return {
        "intent": intent,
        "generation_blocker": generation_blocker,
        "approved_queue": {"worker": " ".join(redis.worker_argv())} if redis else None,
        "approved_api_token": bool(token or (postgres and postgres.api_token) or (redis and redis.api_token_secret)),
        "approved_database": (
            {"migration": " ".join(postgres.migration(commands["start_command"] or []))}
            | ({"ephemeral": True} if postgres.ephemeral else {})
        ) if postgres else None,
        "recommendations": {
            "runtime": {
                "value": family,
                "source": evidence,
                "confidence": 0.95 if assessment.get("evidence") else 0.6,
            },
            "network": {"source": evidence, "confidence": 0.9 if port else 0.6},
            "resources": {"source": "Kubernetes policy default", "confidence": 0.7},
            "exposure": {"source": "Kubernetes policy default", "confidence": 0.8},
            "commands": {
                "source": "repository evidence" if any(commands.values()) else "No commands detected",
                "confidence": 0.95 if any(commands.values()) else 0,
            },
        },
        "application_configuration": application_configuration,
    }
