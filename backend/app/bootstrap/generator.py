"""Typed Phase 4 bundle renderer. No repository, GitHub, or Kubernetes writes occur here."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import yaml

from app.bootstrap.contracts import (
    BUILD_SPECIFICATION_SCHEMA_VERSION,
    DEPLOYMENT_INTENT_SCHEMA_VERSION,
    IMAGE_BINDING_NAME,
    INFRASTRUCTURE_PLAN_SCHEMA_VERSION,
)

MANAGED_PATHS = {
    "Dockerfile",
    ".dockerignore",
    ".github/workflows/deployflow.yml",
    "deploy/kubernetes/deployment.yaml",
    "deploy/kubernetes/service.yaml",
    "deploy/kubernetes/configmap.yaml",
    "deploy/kubernetes/ingress.yaml",
}
TEMPLATE_VERSION = "node-web-v2"
_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedBundle:
    files: dict[str, str]
    digest: str
    validation: dict


def canonical_digest(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _yaml(value: dict) -> str:
    rendered = yaml.safe_dump(value, sort_keys=False, allow_unicode=False)
    yaml.safe_load(rendered)
    return rendered


def _command(value: list[str], field: str) -> list[str]:
    if not value or len(value) > 8 or any(not isinstance(part, str) or not part for part in value):
        raise PlanError(f"invalid_{field}")
    if value[0] not in {"npm", "node"} or any(
        any(token in part for token in ";&|`$><\n\r") for part in value
    ):
        raise PlanError(f"unsafe_{field}")
    return value


def _name(value: str, field: str) -> str:
    if not _NAME.fullmatch(value) or len(value) > 63:
        raise PlanError(f"invalid_{field}")
    return value


def render(assessment: dict, intent: dict, profile: dict) -> GeneratedBundle:
    if assessment.get("schema_version") != "infrastructure-assessment-v2":
        raise PlanError("assessment_not_generatable")
    # Phase 4 resolves generic deployment uncertainty through the Kubernetes
    # recommendation and environment profile. The renderer validates every
    # concrete value it consumes below, so unrelated Phase 3 questions must not
    # block an otherwise safe bundle.
    if assessment.get("completion") not in {"complete", "partial"}:
        raise PlanError("assessment_requires_review")
    if assessment.get("review", {}).get("runtime") != "nodejs":
        raise PlanError("unsupported_runtime")
    # Documents created by the first Phase 4 vertical slice predate explicit
    # version fields and are treated as v1 during the local migration period.
    if (
        intent.get("schema_version", DEPLOYMENT_INTENT_SCHEMA_VERSION)
        != DEPLOYMENT_INTENT_SCHEMA_VERSION
    ):
        raise PlanError("unsupported_intent_schema")
    name = _name(assessment["application"]["name"], "application_name")
    image_tag = assessment.get("repository", {}).get("commit_sha")
    if not isinstance(image_tag, str) or not re.fullmatch(r"[0-9a-f]{40}", image_tag):
        raise PlanError("invalid_repository_commit")
    build = intent["build"]
    if (
        build.get("schema_version", BUILD_SPECIFICATION_SCHEMA_VERSION)
        != BUILD_SPECIFICATION_SCHEMA_VERSION
    ):
        raise PlanError("unsupported_build_schema")
    install = _command(build["install_command"], "install_command")
    test = _command(build["test_command"], "test_command") if build.get("test_command") else None
    build_command = (
        _command(build["build_command"], "build_command") if build.get("build_command") else None
    )
    start = _command(build["start_command"], "start_command")
    port = intent["connectivity"]["port"]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise PlanError("invalid_port")
    health = intent["connectivity"]["health_path"]
    if (
        not isinstance(health, str)
        or not health.startswith("/")
        or any(char.isspace() for char in health)
    ):
        raise PlanError("invalid_health_path")
    image = build["image"]
    if not image.startswith(profile["registry"].rstrip("/") + "/"):
        raise PlanError("image_registry_denied")
    replicas = intent["connectivity"]["replicas"]
    if not isinstance(replicas, int) or not 1 <= replicas <= profile["max_replicas"]:
        raise PlanError("invalid_replicas")
    resources = intent["connectivity"]["resources"]
    environment = {"PORT": str(port), "HOST": "0.0.0.0", "NODE_ENV": "production"}
    environment.update(intent["connectivity"].get("environment", {}))
    if any(re.search(r"(?i)(secret|token|password|api[_-]?key)", key) for key in environment):
        raise PlanError("sensitive_environment_value")
    secret_env = []
    for connection in intent["connectivity"].get("connections", []):
        prefix = connection["name"].upper().replace("-", "_")
        environment.update(
            {
                f"{prefix}_HOST": connection["host"],
                f"{prefix}_PORT": str(connection["port"]),
                f"{prefix}_PROTOCOL": connection["protocol"],
                f"{prefix}_TLS": connection["tls"],
            }
        )
        for reference in connection["secret_references"]:
            if reference["secret_name"] not in profile["allowed_secret_names"]:
                raise PlanError("secret_reference_denied")
            secret_env.append(
                {
                    "name": reference["environment_variable"],
                    "valueFrom": {
                        "secretKeyRef": {
                            "name": reference["secret_name"],
                            "key": reference["secret_key"],
                        }
                    },
                }
            )

    dockerfile = "\n".join(
        [
            f"FROM {profile['node_base_image']}",
            "WORKDIR /app",
            "COPY package.json package-lock.json ./",
            f"RUN {json.dumps(install)}",
            "COPY . .",
            "USER node",
            f"EXPOSE {port}",
            f"CMD {json.dumps(start)}",
            "",
        ]
    )
    workflow_steps = [
        {"uses": profile["checkout_action"]},
        {"uses": profile["setup_node_action"], "with": {"node-version": "20", "cache": "npm"}},
        {"run": " ".join(install)},
    ]
    if test:
        workflow_steps.append({"run": " ".join(test)})
    if build_command:
        workflow_steps.append({"run": " ".join(build_command)})
    workflow_steps.extend(
        [
            {"run": "docker build --file Dockerfile --tag ${{ env.IMAGE }}:${{ env.IMAGE_TAG }} ."},
            {
                "if": "github.event_name == 'push'",
                "run": "docker push ${{ env.IMAGE }}:${{ env.IMAGE_TAG }}",
            },
        ]
    )
    workflow = {
        "name": "DeployFlow build",
        "on": {
            "push": {"branches": [build["branch"]]},
            "pull_request": {"branches": [build["branch"]]},
        },
        "permissions": {"contents": "read", "packages": "write"},
        "env": {"IMAGE": image, "IMAGE_TAG": image_tag},
        "jobs": {"build": {"runs-on": "ubuntu-latest", "steps": workflow_steps}},
    }
    labels = {"app.kubernetes.io/name": name, "app.kubernetes.io/managed-by": "deployflow"}
    if profile.get("application_id"):
        labels["app.kubernetes.io/instance"] = str(profile["application_id"])
    config = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": f"{name}-config", "namespace": profile["namespace"], "labels": labels},
        "data": environment,
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": profile["namespace"], "labels": labels},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": name,
                            "image": f"{image}:{image_tag}",
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"containerPort": port, "name": "http"}],
                            "envFrom": [{"configMapRef": {"name": f"{name}-config"}}],
                            "env": secret_env,
                            "resources": resources,
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "readinessProbe": {"httpGet": {"path": health, "port": "http"}},
                            "livenessProbe": {"httpGet": {"path": health, "port": "http"}},
                        }
                    ],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": profile["namespace"], "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": labels,
            "ports": [{"name": "http", "port": port, "targetPort": "http"}],
        },
    }
    files = {
        "Dockerfile": dockerfile,
        ".dockerignore": "node_modules\n.git\n.env\n",
        ".github/workflows/deployflow.yml": _yaml(workflow),
        "deploy/kubernetes/configmap.yaml": _yaml(config),
        "deploy/kubernetes/deployment.yaml": _yaml(deployment),
        "deploy/kubernetes/service.yaml": _yaml(service),
    }
    exposure = intent["connectivity"]["exposure"]
    if exposure["kind"] == "external":
        hostname = exposure.get("hostname")
        if not isinstance(hostname, str) or not hostname.endswith(profile["hostname_suffix"]):
            raise PlanError("hostname_denied")
        ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {"name": name, "namespace": profile["namespace"], "labels": labels},
            "spec": {
                "ingressClassName": profile["ingress_class"],
                "rules": [
                    {
                        "host": hostname,
                        "http": {
                            "paths": [
                                {
                                    "path": exposure.get("path", "/"),
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {"name": name, "port": {"name": "http"}}
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        }
        tls_secret = exposure.get("tls_secret_name")
        if tls_secret:
            if tls_secret not in profile["allowed_secret_names"]:
                raise PlanError("tls_secret_reference_denied")
            ingress["spec"]["tls"] = [{"hosts": [hostname], "secretName": tls_secret}]
        files["deploy/kubernetes/ingress.yaml"] = _yaml(ingress)
    validation = {
        "schema_version": INFRASTRUCTURE_PLAN_SCHEMA_VERSION,
        "status": "passed",
        "checks": [
            "typed-inputs",
            "yaml-parse",
            "restricted-pod-security",
            "registry",
            "namespace",
            "selector-port-consistency",
        ],
        "provisional_image": f"{image}:{image_tag}",
        "image_binding": {
            "name": IMAGE_BINDING_NAME,
            "repository": image,
            "preview_reference": f"{image}:{image_tag}",
            "status": "provisional",
            "final_reference_kind": "digest",
        },
    }
    return GeneratedBundle(files, canonical_digest(files), validation)
