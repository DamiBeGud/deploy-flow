"""Narrow Kubernetes writes for the isolated deployment worker."""

from __future__ import annotations

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from app.kubernetes.client import NAMESPACE, ClusterError, _api_client, _failure

_METHODS = {
    "ConfigMap": (client.CoreV1Api, "read_namespaced_config_map", "create_namespaced_config_map", "patch_namespaced_config_map"),
    "Service": (client.CoreV1Api, "read_namespaced_service", "create_namespaced_service", "patch_namespaced_service"),
    "Deployment": (client.AppsV1Api, "read_namespaced_deployment", "create_namespaced_deployment", "patch_namespaced_deployment"),
    "Ingress": (client.NetworkingV1Api, "read_namespaced_ingress", "create_namespaced_ingress", "patch_namespaced_ingress"),
}


def apply_owned(item: dict, application_id: str, expected_uid: str | None = None) -> dict:
    """Create or apply an already-checked release object, rejecting foreign ownership."""
    kind = item["kind"]
    name = item["metadata"]["name"]
    api_type, read_method, create_method, patch_method = _METHODS[kind]
    try:
        with _api_client() as api:
            instance = api_type(api)
            try:
                existing = getattr(instance, read_method)(name, NAMESPACE, _request_timeout=8)
            except ApiException as exc:
                if exc.status != 404:
                    raise
                existing = None
            if existing is None:
                if expected_uid:
                    raise ClusterError("resource_disappeared")
                result = getattr(instance, create_method)(
                    NAMESPACE, item, field_manager="deployflow-executor", field_validation="Strict",
                    _request_timeout=8,
                )
            else:
                labels = existing.metadata.labels or {}
                if (labels.get("app.kubernetes.io/managed-by") != "deployflow"
                        or labels.get("app.kubernetes.io/instance") != application_id):
                    raise ClusterError("resource_ownership_conflict")
                if expected_uid and expected_uid != existing.metadata.uid:
                    raise ClusterError("resource_identity_changed")
                body = {**item, "metadata": {
                    **item["metadata"], "uid": existing.metadata.uid,
                    "resourceVersion": existing.metadata.resource_version,
                }}
                result = getattr(instance, patch_method)(
                    name, NAMESPACE, body, field_manager="deployflow-executor", force=False,
                    field_validation="Strict", _content_type="application/apply-patch+yaml",
                    _request_timeout=8,
                )
            return {"kind": kind, "name": name, "uid": result.metadata.uid,
                    "generation": result.metadata.generation or 1}
    except ApiException as exc:
        if exc.status in {409, 422}:
            raise ClusterError("resource_apply_conflict") from None
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc


def rollout_status(name: str, application_id: str, expected_uid: str, image_reference: str) -> dict:
    try:
        with _api_client() as api:
            deployment = client.AppsV1Api(api).read_namespaced_deployment(name, NAMESPACE, _request_timeout=8)
            meta = deployment.metadata
            labels = meta.labels or {}
            if (meta.uid != expected_uid or labels.get("app.kubernetes.io/instance") != application_id
                    or labels.get("app.kubernetes.io/managed-by") != "deployflow"):
                raise ClusterError("resource_identity_changed")
            spec = deployment.spec
            status = deployment.status
            image = spec.template.spec.containers[0].image
            if image != image_reference:
                raise ClusterError("deployment_image_changed")
            desired = spec.replicas or 1
            result = {
                "name": name, "generation": meta.generation,
                "observed_generation": status.observed_generation or 0,
                "desired_replicas": desired,
                "updated_replicas": status.updated_replicas or 0,
                "ready_replicas": status.ready_replicas or 0,
                "available_replicas": status.available_replicas or 0,
            }
            for condition in status.conditions or []:
                if condition.type == "Progressing" and condition.status == "False":
                    return {**result, "status": "failed", "reason": str(condition.reason or "rollout_failed")[:80]}
            if (result["observed_generation"] >= result["generation"]
                    and result["updated_replicas"] >= desired
                    and result["ready_replicas"] >= desired
                    and result["available_replicas"] >= desired):
                return {**result, "status": "ready"}
            pods = client.CoreV1Api(api).list_namespaced_pod(
                NAMESPACE, label_selector=f"app.kubernetes.io/instance={application_id}",
                limit=20, _request_timeout=8,
            )
            for pod in pods.items:
                for state in pod.status.container_statuses or []:
                    waiting = state.state.waiting if state.state else None
                    if waiting and waiting.reason in {"ErrImagePull", "ImagePullBackOff", "CrashLoopBackOff", "CreateContainerConfigError"}:
                        return {**result, "status": "failed", "reason": waiting.reason}
            return {**result, "status": "pending"}
    except ApiException as exc:
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc


def service_health(name: str, path: str) -> dict:
    """HTTP GET through the Kubernetes Service proxy from inside the cluster."""
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
        raise ClusterError("health_path_denied")
    try:
        with _api_client() as api:
            core = client.CoreV1Api(api)
            response = core.connect_get_namespaced_service_proxy_with_path(
                name, NAMESPACE, path.lstrip("/"), _request_timeout=8,
            )
            # The API raises on non-2xx. Never persist the application's response body.
            return {"status": "passed", "service": name, "path": path,
                    "response_received": response is not None}
    except ApiException as exc:
        if exc.status in {400, 404, 500, 502, 503, 504}:
            return {"status": "pending", "service": name, "path": path,
                    "code": "application_unhealthy"}
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc
