"""Evidence-first Kubernetes deployment recommendations; this module never contacts a cluster."""

from __future__ import annotations

from copy import deepcopy

from app.bootstrap.contracts import (
    BUILD_SPECIFICATION_SCHEMA_VERSION,
    DEPLOYMENT_INTENT_SCHEMA_VERSION,
)


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
        resolved[field_path] = item
    return resolved


def recommend(assessment: dict, profile: dict, revision: str, assessment_version: int) -> dict:
    review = assessment.get("review", {})
    build = assessment.get("build", {})
    workflow = assessment.get("workflow", {})
    runtime = assessment.get("runtime", {})
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
        if name == "PORT" or any(marker in name for marker in secret_markers):
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
    intent = {
        "schema_version": DEPLOYMENT_INTENT_SCHEMA_VERSION,
        "version": 0,
        "assessment_version": assessment_version,
        "environment_profile_id": profile["id"],
        "connectivity": {
            "port": review.get("port") or 3000,
            "health_path": review.get("health_path") or "/healthz",
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
            "image": profile["default_image"],
            "build_context": ".",
            "install_command": (build.get("install_command") or "npm ci").split(),
            "test_command": (workflow.get("test_command") or "npm test").split(),
            "build_command": (workflow.get("build_command") or "").split() or None,
            "start_command": (runtime.get("command") or "npm start").split(),
        },
    }
    port_from_repository = bool(review.get("port"))
    health_from_repository = bool(review.get("health_path"))
    command_evidence = {
        "install_command": bool(build.get("install_command")),
        "test_command": bool(workflow.get("test_command")),
        "build_command": bool(workflow.get("build_command")),
        "start_command": bool(runtime.get("command")),
    }
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
            "recommended_value": profile["default_image"],
            "origin": "policy_default",
            "source": "Kubernetes environment profile",
            "confidence": 1.0,
            "explanation": "Allowlisted container registry and image repository for this environment.",
            "evidence": None,
        },
    }
    for command_name in ("install_command", "test_command", "build_command", "start_command"):
        from_repository = command_evidence[command_name]
        category = {
            "install_command": "workflow.install_command",
            "test_command": "workflow.test_command",
            "build_command": "build.command",
            "start_command": "runtime.command",
        }[command_name]
        field_recommendations[f"build.{command_name}"] = {
            "recommended_value": intent["build"][command_name],
            "origin": "inferred" if from_repository else "policy_default",
            "source": "repository evidence" if from_repository else "supported Node.js default",
            "confidence": 0.95 if from_repository else 0.6,
            "explanation": f"Reviewed {command_name.replace('_', ' ')} represented as an argument array.",
            "evidence": finding_evidence(category),
        }
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
    return {
        "intent": intent,
        "recommendations": {
            "runtime": {
                "value": review.get("runtime") or "nodejs",
                "source": evidence,
                "confidence": 0.95 if assessment.get("evidence") else 0.6,
            },
            "network": {"source": evidence, "confidence": 0.9 if review.get("port") else 0.6},
            "resources": {"source": "Kubernetes policy default", "confidence": 0.7},
            "exposure": {"source": "Kubernetes policy default", "confidence": 0.8},
            "commands": {
                "source": evidence,
                "confidence": 0.95 if build.get("install_command") else 0.6,
            },
        },
        "application_configuration": application_configuration,
    }
