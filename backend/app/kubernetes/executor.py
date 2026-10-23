"""Narrow Kubernetes writes for the isolated deployment worker."""

from __future__ import annotations

import json

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from app.kubernetes.client import (
    DEPLOYMENT_FIELD_MANAGER,
    NAMESPACE,
    ClusterError,
    _api_client,
    _failure,
    image_update_patch,
)

_METHODS = {
    "ConfigMap": (client.CoreV1Api, "read_namespaced_config_map", "create_namespaced_config_map", "patch_namespaced_config_map"),
    "Service": (client.CoreV1Api, "read_namespaced_service", "create_namespaced_service", "patch_namespaced_service"),
    "Deployment": (client.AppsV1Api, "read_namespaced_deployment", "create_namespaced_deployment", "patch_namespaced_deployment"),
    "Ingress": (client.NetworkingV1Api, "read_namespaced_ingress", "create_namespaced_ingress", "patch_namespaced_ingress"),
}
_DELETE_METHODS = {
    "ConfigMap": (client.CoreV1Api, "read_namespaced_config_map", "delete_namespaced_config_map"),
    "Service": (client.CoreV1Api, "read_namespaced_service", "delete_namespaced_service"),
    "Deployment": (client.AppsV1Api, "read_namespaced_deployment", "delete_namespaced_deployment"),
    "Ingress": (client.NetworkingV1Api, "read_namespaced_ingress", "delete_namespaced_ingress"),
}


def delete_owned(kind: str, name: str, application_id: str, expected_uid: str | None) -> bool:
    """Delete one journaled object with an observed UID precondition.

    Return true only after a read confirms the object is absent. A missing
    journal UID is allowed for a crash in the create/response gap, but labels
    and the live UID are still checked before a delete is issued.
    """
    api_type, read_method, delete_method = _DELETE_METHODS[kind]
    try:
        with _api_client() as api:
            instance = api_type(api)
            try:
                existing = getattr(instance, read_method)(name, NAMESPACE, _request_timeout=8)
            except ApiException as exc:
                if exc.status == 404:
                    return True
                raise
            labels = existing.metadata.labels or {}
            if (labels.get("app.kubernetes.io/managed-by") != "deployflow"
                    or labels.get("app.kubernetes.io/instance") != application_id):
                raise ClusterError("resource_ownership_conflict")
            if expected_uid and existing.metadata.uid != expected_uid:
                raise ClusterError("resource_identity_changed")
            if existing.metadata.deletion_timestamp:
                return False
            getattr(instance, delete_method)(
                name, NAMESPACE,
                body=client.V1DeleteOptions(
                    preconditions=client.V1Preconditions(uid=existing.metadata.uid),
                ),
                _request_timeout=8,
            )
            return False
    except ApiException as exc:
        if exc.status == 404:
            return True
        if exc.status == 409:
            raise ClusterError("resource_identity_changed") from None
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc


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
                    NAMESPACE, item, field_manager=DEPLOYMENT_FIELD_MANAGER, field_validation="Strict",
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
                patch = image_update_patch(api, item, existing)
                options = {"field_manager": DEPLOYMENT_FIELD_MANAGER, "field_validation": "Strict", "_request_timeout": 8}
                if patch is None:
                    options.update(force=False, _content_type="application/apply-patch+yaml")
                else:
                    options.update(_content_type="application/json-patch+json")
                result = getattr(instance, patch_method)(name, NAMESPACE, patch if patch is not None else body, **options)
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


def rollout_status(name: str, application_id: str, expected_uid: str, image_reference: str, expected_generation: int | None = None) -> dict:
    try:
        with _api_client() as api:
            deployment = client.AppsV1Api(api).read_namespaced_deployment(name, NAMESPACE, _request_timeout=8)
            meta = deployment.metadata
            labels = meta.labels or {}
            if (meta.uid != expected_uid or labels.get("app.kubernetes.io/instance") != application_id
                    or labels.get("app.kubernetes.io/managed-by") != "deployflow"):
                raise ClusterError("resource_identity_changed")
            if expected_generation is not None and meta.generation != expected_generation:
                raise ClusterError("resource_generation_changed")
            spec = deployment.spec
            status = deployment.status
            image = spec.template.spec.containers[0].image
            if image != image_reference:
                raise ClusterError("deployment_image_changed")
            desired = spec.replicas if spec.replicas is not None else 1
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


def service_health(name: str, path: str, application_id: str | None = None, expected_uid: str | None = None, expected_port: int | None = None) -> dict:
    """Proxy to the credential-free in-cluster checker, never directly to the app."""
    if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
        raise ClusterError("health_path_denied")
    try:
        with _api_client() as api:
            core = client.CoreV1Api(api)
            if application_id is None or expected_uid is None or expected_port is None:
                raise ClusterError("service_inventory_missing")
            service = core.read_namespaced_service(name, NAMESPACE, _request_timeout=8)
            labels = service.metadata.labels or {}
            if (
                service.metadata.uid != expected_uid
                or labels.get("app.kubernetes.io/managed-by") != "deployflow"
                or labels.get("app.kubernetes.io/instance") != application_id
                or len(service.spec.ports or []) != 1
                or service.spec.ports[0].port != expected_port
            ):
                raise ClusterError("resource_identity_changed")
            try:
                response = core.connect_get_namespaced_service_proxy_with_path(
                    "deployflow-health-checker:http", NAMESPACE,
                    f"probe/{name}/{expected_port}/{path.lstrip('/')}", _request_timeout=8,
                    # The generated client declares this response as str and
                    # otherwise turns a JSON object into Python's dict repr.
                    _preload_content=False,
                )
            except ApiException as exc:
                if exc.status in {404, 502, 503, 504}:
                    return {"status": "pending", "service": name, "path": path,
                            "code": "health_checker_unavailable"}
                raise
            try:
                payload = response.read(4097)
                if len(payload) > 4096:
                    raise ClusterError("health_checker_invalid")
                result = json.loads(payload)
            except (ValueError, TypeError):
                raise ClusterError("health_checker_invalid") from None
            finally:
                response.close()
                response.release_conn()
            if (
                not isinstance(result, dict) or result.get("status") not in {"passed", "pending"}
                or result.get("service") != name or result.get("path") != path
            ):
                raise ClusterError("health_checker_invalid")
            return {"status": result["status"], "service": name, "path": path,
                    **({"code": result["code"]} if result.get("code") else {})}
    except ApiException as exc:
        if exc.status in {400, 404, 500, 502, 503, 504}:
            return {"status": "pending", "service": name, "path": path,
                    "code": "service_unreachable"}
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc
