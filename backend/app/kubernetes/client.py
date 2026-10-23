"""Typed, single-namespace Kubernetes client used only inside MCP processes."""

from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client.exceptions import ApiException

NAMESPACE = "deployflow-test"
DEPLOYMENT_FIELD_MANAGER = "deployflow-executor"


def image_update_patch(api, item: dict, existing) -> list[dict] | None:
    """Bridge POST-created Deployment ownership without forcing SSA conflicts.

    Compatible source releases change only the image. Preserve defaults/foreign
    fields; reject other intended changes or foreign image ownership. The validator
    uses this same patch with dry_run=All under its separate identity.
    """
    if item["kind"] != "Deployment":
        return None
    owners = []
    for entry in getattr(existing.metadata, "managed_fields", None) or []:
        fields = entry.fields_v1 or {}
        containers = fields.get("f:spec", {}).get("f:template", {}).get("f:spec", {}).get("f:containers", {})
        if any("f:image" in value for key, value in containers.items() if key.startswith("k:")):
            owners.append((entry.manager, entry.operation))
    if (DEPLOYMENT_FIELD_MANAGER, "Update") not in owners:
        return None
    if any(manager != DEPLOYMENT_FIELD_MANAGER for manager, _ in owners):
        raise ClusterError("resource_image_ownership_conflict")
    wire = api.sanitize_for_serialization(existing)

    def project(current, desired):
        if isinstance(desired, dict):
            if not isinstance(current, dict) or set(desired) - set(current):
                raise ClusterError("resource_requirements_need_review")
            return {key: project(current[key], value) for key, value in desired.items()}
        if isinstance(desired, list):
            if not isinstance(current, list) or len(current) != len(desired):
                raise ClusterError("resource_requirements_need_review")
            return [project(a, b) for a, b in zip(current, desired)]
        return current

    desired = copy.deepcopy(item["spec"])
    for container in desired["template"]["spec"]["containers"]:
        if container.get("env") == []:
            container.pop("env")  # The API omits this known empty-list default.
    current = project(wire["spec"], desired)
    changes = []
    for index, container in enumerate(desired["template"]["spec"]["containers"]):
        previous = current["template"]["spec"]["containers"][index]
        if previous["name"] != container["name"]:
            raise ClusterError("resource_requirements_need_review")
        if previous["image"] != container["image"]:
            changes.append({"op": "replace", "path": f"/spec/template/spec/containers/{index}/image", "value": container["image"]})
        previous["image"] = container["image"]
    if current != desired:
        raise ClusterError("resource_requirements_need_review")
    for key in ("labels", "annotations"):
        expected = item["metadata"].get(key, {})
        if project(wire["metadata"].get(key, {}), expected) != expected:
            raise ClusterError("resource_requirements_need_review")
    return [
        {"op": "test", "path": "/metadata/uid", "value": existing.metadata.uid},
        {"op": "test", "path": "/metadata/resourceVersion", "value": existing.metadata.resource_version},
        *changes,
    ]


class ClusterError(Exception):
    def __init__(self, code: str, details: list[dict] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or []


def _failure(exc: ApiException) -> ClusterError:
    if exc.status == 401:
        return ClusterError("cluster_access_expired")
    if exc.status == 403:
        return ClusterError("cluster_access_denied")
    if exc.status in {408, 429, 500, 502, 503, 504}:
        return ClusterError("cluster_unavailable")
    details = []
    try:
        body = json.loads(exc.body or "{}")
        for cause in (body.get("details") or {}).get("causes") or []:
            field = str(cause.get("field") or "")[:160]
            reason = str(cause.get("reason") or "Invalid")[:80]
            if field:
                details.append({"field": field, "reason": reason})
    except (ValueError, TypeError, AttributeError):
        pass
    return ClusterError("cluster_admission_rejected", details[:12])


@contextmanager
def _api_client():
    config = client.Configuration()
    mode = os.environ.get("DF_KUBE_ACCESS_MODE", "ssh")
    if mode == "incluster":
        # Use the official projected-token loader on each bounded operation.
        # Never copy the rotating token into bootstrap tmpfs or fall back to
        # developer/admin kubeconfig if the Pod identity is unavailable.
        if any(os.environ.get(key) for key in (
            "DF_KUBE_API_SERVER", "DF_KUBE_CA_PATH", "DF_KUBE_TOKEN_PATH",
        )):
            raise ClusterError("cluster_access_unconfigured")
        try:
            kube_config.load_incluster_config(client_configuration=config, try_refresh_token=True)
        except (kube_config.ConfigException, OSError, ValueError) as exc:
            raise ClusterError("cluster_access_unconfigured") from exc
    elif mode == "ssh":
        endpoint = os.environ.get("DF_KUBE_API_SERVER", "https://127.0.0.1:16443")
        ca_path = os.environ.get("DF_KUBE_CA_PATH")
        token_path = os.environ.get("DF_KUBE_TOKEN_PATH")
        if not endpoint.startswith("https://127.0.0.1:") or not ca_path or not token_path:
            raise ClusterError("cluster_access_unconfigured")
        try:
            token = Path(token_path).read_text(encoding="ascii").strip()
            if not token or not Path(ca_path).is_file():
                raise OSError("credential_missing")
        except OSError as exc:
            raise ClusterError("cluster_access_unconfigured") from exc
        config.host = endpoint
        config.ssl_ca_cert = ca_path
        config.api_key = {"BearerToken": "Bearer " + token}
    else:
        raise ClusterError("cluster_access_unconfigured")
    config.verify_ssl = True
    config.retries = 0
    api = client.ApiClient(configuration=config)
    try:
        yield api
    finally:
        api.close()


def _serialized(api: client.ApiClient, value) -> dict:
    return api.sanitize_for_serialization(value)


def _items(api, method):
    rows, cursor = [], None
    for _ in range(5):
        kwargs = {"limit": 100, "_request_timeout": 8}
        if cursor:
            kwargs["_continue"] = cursor
        response = method(NAMESPACE, **kwargs)
        rows.extend(_serialized(api, item) for item in response.items)
        cursor = response.metadata._continue
        if not cursor:
            return rows
    raise ClusterError("cluster_result_limit")


def list_observations() -> dict:
    """Return safe, bounded workload/Service fields, never full API objects."""
    try:
        with _api_client() as api:
            apps = client.AppsV1Api(api)
            core = client.CoreV1Api(api)
            batch = client.BatchV1Api(api)
            deployments = _items(api, apps.list_namespaced_deployment)
            statefulsets = _items(api, apps.list_namespaced_stateful_set)
            jobs = _items(api, batch.list_namespaced_job)
            services = _items(api, core.list_namespaced_service)
    except ApiException as exc:
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc

    observed_at = datetime.now(timezone.utc).isoformat()
    service_rows = []
    for service in services:
        spec = service.get("spec") or {}
        status = service.get("status") or {}
        ingress = (status.get("loadBalancer") or {}).get("ingress") or []
        addresses = [str(item.get("ip") or item.get("hostname")) for item in ingress if item.get("ip") or item.get("hostname")]
        service_rows.append(
            {
                "name": service["metadata"]["name"],
                "uid": service["metadata"].get("uid"),
                "application_id": (service["metadata"].get("labels") or {}).get("app.kubernetes.io/instance"),
                "type": spec.get("type"),
                "ports": [
                    {"name": p.get("name"), "port": p.get("port"), "target_port": p.get("targetPort")}
                    for p in spec.get("ports") or []
                ][:20],
                "selector": spec.get("selector") or {},
                "address": ", ".join(addresses) if addresses else spec.get("clusterIP"),
            }
        )
    workloads = []
    for kind, rows in (("Deployment", deployments), ("StatefulSet", statefulsets), ("Job", jobs)):
        for row in rows:
            meta = row.get("metadata") or {}
            spec = row.get("spec") or {}
            status = row.get("status") or {}
            template = spec.get("template") or {}
            labels = (template.get("metadata") or {}).get("labels") or {}
            containers = (template.get("spec") or {}).get("containers") or []
            matched = [
                service["name"]
                for service in service_rows
                if service["selector"]
                and all(labels.get(key) == value for key, value in service["selector"].items())
            ]
            name = meta.get("name", "")
            workloads.append(
                {
                    "id": f"{NAMESPACE}:{kind}:{name}",
                    "namespace": NAMESPACE,
                    "uid": meta.get("uid"),
                    "application_id": (meta.get("labels") or {}).get("app.kubernetes.io/instance"),
                    "generation": meta.get("generation"),
                    "observed_generation": status.get("observedGeneration", 0),
                    "updated_replicas": status.get("updatedReplicas", 0),
                    "available_replicas": status.get("availableReplicas", 0),
                    "kind": kind,
                    "name": name,
                    "desired_replicas": spec.get("replicas", 1) if kind != "Job" else None,
                    "ready_replicas": status.get("readyReplicas", 0) if kind != "Job" else None,
                    "desired_completions": spec.get("completions", 1) if kind == "Job" else None,
                    "succeeded": status.get("succeeded", 0) if kind == "Job" else None,
                    "image": containers[0].get("image") if containers else None,
                    "conditions": [
                        {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
                        for c in status.get("conditions") or []
                    ][:12],
                    "services": matched,
                    "managed": (meta.get("labels") or {}).get("app.kubernetes.io/managed-by") == "deployflow",
                    "observed_at": observed_at,
                }
            )
    return {"cluster_id": "local-k3s", "namespace": NAMESPACE, "observed_at": observed_at, "workloads": workloads, "services": service_rows}


_API = {
    "ConfigMap": ("core", "patch_namespaced_config_map", "read_namespaced_config_map"),
    "Service": ("core", "patch_namespaced_service", "read_namespaced_service"),
    "Deployment": ("apps", "patch_namespaced_deployment", "read_namespaced_deployment"),
    "Ingress": ("networking", "patch_namespaced_ingress", "read_namespaced_ingress"),
}


def validate_dry_run(manifests: list[dict], application_id: str) -> dict:
    """The only validator operation; every API write is hard-coded dry-run."""
    checks = []
    try:
        with _api_client() as api:
            clients = {
                "core": client.CoreV1Api(api),
                "apps": client.AppsV1Api(api),
                "networking": client.NetworkingV1Api(api),
            }
            for item in manifests:
                kind = item["kind"]
                group, patch_method, get_method = _API[kind]
                instance = clients[group]
                name = item["metadata"]["name"]
                try:
                    existing = getattr(instance, get_method)(name, NAMESPACE, _request_timeout=8)
                except ApiException as exc:
                    if exc.status != 404:
                        raise
                    existing = None
                else:
                    labels = (existing.metadata.labels or {})
                    if labels.get("app.kubernetes.io/instance") != application_id:
                        raise ClusterError("resource_ownership_conflict")
                    if labels.get("app.kubernetes.io/managed-by") != "deployflow":
                        raise ClusterError("resource_ownership_conflict")
                patch = image_update_patch(api, item, existing) if existing is not None else None
                try:
                    getattr(instance, patch_method)(
                        name,
                        NAMESPACE,
                        patch if patch is not None else item,
                        dry_run="All",
                        field_manager=DEPLOYMENT_FIELD_MANAGER,
                        field_validation="Strict",
                        _content_type="application/json-patch+json" if patch is not None else "application/apply-patch+yaml",
                        _request_timeout=8,
                    )
                except ApiException as exc:
                    failure = _failure(exc)
                    checks.append({"kind": kind, "name": name, "code": failure.code, "details": failure.details})
                    continue
                checks.append({"kind": kind, "name": name, "code": "admission_passed", "details": []})
    except ApiException as exc:
        raise _failure(exc) from None
    except ClusterError:
        raise
    except Exception as exc:
        raise ClusterError("cluster_unavailable") from exc
    return {
        "status": "passed" if all(item["code"] == "admission_passed" for item in checks) else "failed",
        "checks": checks,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
