"""Resolve analysis questions already answered by an exact platform binding."""

from app.bootstrap.native_runtime import python_base_image
from app.bootstrap.redis import redis_for_source

RESOLUTION_VERSION = "redis-platform-questions-v1"


def resolve_redis_questions(assessment: dict, application_id: str) -> list[dict[str, str]]:
    """Retain the model questions and record which required flags the host resolved."""
    binding = redis_for_source(assessment, application_id)
    if not binding or not binding.redis_connection_secret:
        return []

    decisions = {
        "dependencies.redis.topology": ("approved_redis_binding", "provided_connection_references"),
        "configuration.QUEUE_NAMESPACE.value": ("approved_redis_binding", binding.environment()["QUEUE_NAMESPACE"]),
        "workflow.worker_restart_policy": ("kubernetes_deployment", "Always"),
    }
    version = assessment.get("runtime", {}).get("version")
    if isinstance(version, str) and version:
        try:
            decisions["build.image.base"] = ("native_python_template", python_base_image(version))
        except ValueError:
            # A source the renderer cannot support still needs review.
            pass

    resolved = []
    for question in assessment.get("questions", []):
        field = question["field_path"]
        if question["required"] and (decision := decisions.get(field)):
            origin, value = decision
            question["required"] = False
            resolved.append({
                "question_id": question["id"], "field_path": field, "origin": origin, "value": value,
            })
    return resolved
