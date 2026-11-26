"""Host-owned question IDs and answers for the study's external Redis path."""

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from app.bootstrap.contracts import GENERATED_WORKFLOW_PATH
from app.bootstrap.native_runtime import python_base_image
from app.bootstrap.redis import REDIS_WORKLOAD_REPLICAS, redis_for_source

DECISION_CONTRACT_VERSION = "study-redis-deployment-decisions-v1"


class DecisionId(StrEnum):
    REDIS_CONNECTION = "redis.connection_references"
    QUEUE_NAMESPACE = "redis.queue_namespace"
    WORKER_LAYOUT = "redis.worker_layout"
    DOCKERFILE = "delivery.dockerfile"
    WORKFLOW = "delivery.workflow"
    TOKEN_REFERENCE = "auth.api_token.reference"
    TOKEN_ROTATION = "auth.api_token.rotation"
    AUTH_ENFORCEMENT = "auth.enforcement"
    OTHER = "other"


CATALOG = {
    DecisionId.REDIS_CONNECTION: ("dependencies.redis.topology", "Redis broker and result Secret references"),
    DecisionId.QUEUE_NAMESPACE: ("configuration.QUEUE_NAMESPACE.value", "Shared queue namespace"),
    DecisionId.WORKER_LAYOUT: ("workload.worker_replicas", "Celery worker placement, replica count or restart policy"),
    DecisionId.DOCKERFILE: ("build.dockerfile.base_image", "Generation of the native Python Dockerfile or its base image"),
    DecisionId.WORKFLOW: ("workflow.definition_path", "Generation of the default CI workflow"),
    DecisionId.TOKEN_REFERENCE: ("configuration.API_TOKEN.secret_reference", "Application API token Secret reference"),
    DecisionId.TOKEN_ROTATION: ("configuration.API_TOKEN.rotation", "Application API token lifecycle or rotation"),
    DecisionId.AUTH_ENFORCEMENT: ("security.auth_enforcement", "Application authentication or access policy"),
    DecisionId.OTHER: ("deployment.other", "A deployment decision outside this catalog or one that cannot be classified"),
}


@dataclass(frozen=True)
class DecisionSnapshot:
    answers: dict[DecisionId, tuple[str, str]]
    missing: tuple[DecisionId, ...]
    fingerprint: str

    def model_context(self) -> list[dict[str, str]]:
        return [
            {"decision_id": choice, "description": detail, "availability": self.answers.get(choice, ("unresolved", ""))[0]}
            for choice, (_, detail) in CATALOG.items()
        ]


def is_redis_assessment(assessment: dict) -> bool:
    return (
        assessment.get("review", {}).get("runtime") == "python"
        and set(assessment.get("dependencies", {}).get("detected_services", [])) == {"celery", "redis"}
    )


def redis_decisions(assessment: dict, application_id: str) -> DecisionSnapshot:
    binding = redis_for_source(assessment, application_id)
    binding = binding if binding and binding.redis_connection_secret else None
    answers: dict[DecisionId, tuple[str, str]] = {}
    required = [DecisionId.REDIS_CONNECTION, DecisionId.QUEUE_NAMESPACE, DecisionId.WORKER_LAYOUT]
    bootstrap = assessment.get("bootstrap", {})
    if bootstrap.get("dockerfile_required") is True:
        required.append(DecisionId.DOCKERFILE)
        try:
            image = python_base_image(assessment.get("runtime", {}).get("version"))
        except ValueError:
            pass
        else:
            answers[DecisionId.DOCKERFILE] = ("platform_default", image)
    if bootstrap.get("workflow_required") is True:
        answers[DecisionId.WORKFLOW] = ("platform_default", GENERATED_WORKFLOW_PATH)
    if "API_TOKEN" in assessment.get("configuration", {}).get("required_variables", []):
        required.append(DecisionId.TOKEN_REFERENCE)
    if binding:
        answers.update({
            DecisionId.REDIS_CONNECTION: ("provided", "kubernetes_secret_references"),
            DecisionId.QUEUE_NAMESPACE: ("provided", binding.environment()["QUEUE_NAMESPACE"]),
            DecisionId.WORKER_LAYOUT: ("platform_default", f"{REDIS_WORKLOAD_REPLICAS} replica with a Celery container"),
        })
        if binding.api_token_secret:
            answers[DecisionId.TOKEN_REFERENCE] = ("provided", "kubernetes_secret_reference")
            if binding.api_token_lifecycle:
                answers[DecisionId.TOKEN_ROTATION] = ("provided", binding.api_token_lifecycle)

    missing = tuple(choice for choice in required if choice not in answers)
    payload = {
        "version": DECISION_CONTRACT_VERSION, "answers": answers, "missing": missing,
        "binding": binding.model_dump(mode="json") if binding else None,
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return DecisionSnapshot(answers, missing, fingerprint)


def resolve_decisions(document: dict, snapshot: DecisionSnapshot) -> list[dict]:
    resolutions = []
    for question in document.get("questions", []):
        decision = question.get("decision_id")
        if question["required"] and (answer := snapshot.answers.get(decision)):
            availability, value = answer
            question["required"] = False
            resolutions.append({
                "question_id": question["id"], "decision_id": decision, "field_path": question["field_path"],
                "origin": "approved_redis_binding" if availability == "provided" else "deployment_renderer",
                "value": value,
            })
    return resolutions
