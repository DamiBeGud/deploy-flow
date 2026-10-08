"""Fail-closed checks before a stored Phase 4 bundle reaches the Kubernetes API."""

from __future__ import annotations

import hashlib

import yaml

from app.bootstrap.generator import canonical_digest

KINDS = {
    "deploy/kubernetes/configmap.yaml": ("v1", "ConfigMap"),
    "deploy/kubernetes/deployment.yaml": ("apps/v1", "Deployment"),
    "deploy/kubernetes/service.yaml": ("v1", "Service"),
    "deploy/kubernetes/ingress.yaml": ("networking.k8s.io/v1", "Ingress"),
}
REQUIRED = set(KINDS) - {"deploy/kubernetes/ingress.yaml"}


class ManifestPolicyError(ValueError):
    pass


def checked_manifests(
    files: dict[str, str], *, digest: str, namespace: str, application_id: str, registry: str
) -> list[dict]:
    """Accept only the immutable generated Kubernetes subset, never arbitrary YAML."""
    if canonical_digest(files) != digest:
        raise ManifestPolicyError("bundle_digest_mismatch")
    paths = {path for path in files if path.startswith("deploy/kubernetes/")}
    if not REQUIRED.issubset(paths) or not paths.issubset(KINDS):
        raise ManifestPolicyError("manifest_paths_denied")
    result = []
    for path in sorted(paths):
        try:
            documents = list(yaml.safe_load_all(files[path]))
        except yaml.YAMLError as exc:
            raise ManifestPolicyError("manifest_yaml_invalid") from exc
        if len(documents) != 1 or not isinstance(documents[0], dict):
            raise ManifestPolicyError("manifest_document_count")
        item = documents[0]
        if (item.get("apiVersion"), item.get("kind")) != KINDS[path]:
            raise ManifestPolicyError("manifest_kind_denied")
        meta = item.get("metadata") or {}
        labels = meta.get("labels") or {}
        name = meta.get("name")
        if (
            meta.get("namespace") != namespace
            or not isinstance(name, str)
            or not name
            or meta.get("annotations")
            or labels.get("app.kubernetes.io/managed-by") != "deployflow"
            or labels.get("app.kubernetes.io/instance") != application_id
        ):
            raise ManifestPolicyError("manifest_identity_denied")
        result.append(item)

    by_kind = {item["kind"]: item for item in result}
    deployment = by_kind["Deployment"]
    spec = deployment.get("spec") or {}
    template = spec.get("template") or {}
    pod = template.get("spec") or {}
    containers = pod.get("containers") or []
    labels = (template.get("metadata") or {}).get("labels") or {}
    if (
        (spec.get("selector") or {}).get("matchLabels") != labels
        or labels.get("app.kubernetes.io/instance") != application_id
        or pod.get("automountServiceAccountToken") is not False
        or pod.get("serviceAccountName")
        or pod.get("hostNetwork")
        or pod.get("hostPID")
        or pod.get("hostIPC")
        or pod.get("initContainers")
        or pod.get("ephemeralContainers")
        or pod.get("shareProcessNamespace")
        or pod.get("nodeName")
        or pod.get("runtimeClassName")
        or pod.get("imagePullSecrets")
        or pod.get("volumes")
        or len(containers) != 1
    ):
        raise ManifestPolicyError("deployment_policy_denied")
    pod_security = pod.get("securityContext") or {}
    container = containers[0]
    security = container.get("securityContext") or {}
    if (
        pod_security.get("runAsNonRoot") is not True
        or (pod_security.get("seccompProfile") or {}).get("type") != "RuntimeDefault"
        or security.get("allowPrivilegeEscalation") is not False
        or security.get("readOnlyRootFilesystem") is not True
        or (security.get("capabilities") or {}).get("drop") != ["ALL"]
        or security.get("privileged")
        or not str(container.get("image", "")).startswith(registry.rstrip("/") + "/")
    ):
        raise ManifestPolicyError("container_policy_denied")
    service = by_kind["Service"]
    service_spec = service.get("spec") or {}
    if (
        service_spec.get("type") != "ClusterIP"
        or service_spec.get("selector") != labels
        or service_spec.get("externalIPs")
        or service_spec.get("loadBalancerIP")
    ):
        raise ManifestPolicyError("service_selector_denied")
    ports = service_spec.get("ports") or []
    container_ports = container.get("ports") or []
    if (
        len(ports) != 1
        or len(container_ports) != 1
        or ports[0].get("targetPort") != container_ports[0].get("name")
        or ports[0].get("port") != container_ports[0].get("containerPort")
    ):
        raise ManifestPolicyError("service_port_denied")
    config = by_kind["ConfigMap"]
    if not isinstance(config.get("data"), dict):
        raise ManifestPolicyError("configmap_data_denied")
    if (container.get("envFrom") or []) != [
        {"configMapRef": {"name": config["metadata"]["name"]}}
    ]:
        raise ManifestPolicyError("configmap_reference_denied")
    if container.get("env"):
        raise ManifestPolicyError("secret_reference_denied")
    if "Ingress" in by_kind:
        ingress = by_kind["Ingress"]
        ingress_spec = ingress.get("spec") or {}
        if ingress_spec.get("ingressClassName") != "nginx" or ingress_spec.get("tls"):
            raise ManifestPolicyError("ingress_policy_denied")
        rules = ingress_spec.get("rules") or []
        if len(rules) != 1 or not str(rules[0].get("host", "")).endswith(".deployflow.local"):
            raise ManifestPolicyError("ingress_rules_denied")
        paths = (rules[0].get("http") or {}).get("paths") or []
        if (
            len(paths) != 1
            or ((paths[0].get("backend") or {}).get("service") or {}).get("name")
            != service["metadata"]["name"]
        ):
            raise ManifestPolicyError("ingress_backend_denied")
    return result


def file_hashes_match(files: list[dict]) -> bool:
    return all(
        hashlib.sha256(item["content"].encode()).hexdigest() == item["hash"]
        for item in files
    )
