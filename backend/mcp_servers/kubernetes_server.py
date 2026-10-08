"""Separate reader and validator MCP processes; never expose arbitrary kubectl."""

import os

from mcp.server import MCPServer

from app.kubernetes.client import ClusterError, list_observations, validate_dry_run
from app.kubernetes.policy import ManifestPolicyError, checked_manifests

ROLE = os.environ.get("DF_KUBE_MCP_ROLE")
if ROLE not in {"reader", "validator"}:
    raise RuntimeError("DF_KUBE_MCP_ROLE must be reader or validator")
mcp = MCPServer(f"deployflow-kubernetes-{ROLE}")


if ROLE == "reader":

    @mcp.tool(name="kube.list_workloads")
    def list_workloads() -> dict:
        return list_observations()

    @mcp.tool(name="kube.list_services")
    def list_services() -> dict:
        snapshot = list_observations()
        return {key: snapshot[key] for key in ("cluster_id", "namespace", "observed_at", "services")}

    @mcp.tool(name="kube.get_rollout_status")
    def get_rollout_status(workload_id: str) -> dict:
        if not workload_id.startswith("deployflow-test:"):
            return {"error": "workload_denied"}
        snapshot = list_observations()
        return next((row for row in snapshot["workloads"] if row["id"] == workload_id), {"error": "workload_not_found"})

    @mcp.tool(name="kube.get_capabilities")
    def get_capabilities() -> dict:
        return {
            "cluster_id": "local-k3s",
            "namespaces": ["deployflow-test"],
            "ingress_classes": ["nginx"],
            "supported_kinds": ["ConfigMap", "Deployment", "Service", "Ingress"],
        }

else:

    @mcp.tool(name="kube.validate_manifests")
    def validate_manifests(files: dict[str, str], digest: str, application_id: str, registry: str) -> dict:
        try:
            if registry != "ghcr.io":
                raise ManifestPolicyError("registry_denied")
            manifests = checked_manifests(
                files,
                digest=digest,
                namespace="deployflow-test",
                application_id=application_id,
                registry=registry,
            )
            return validate_dry_run(manifests, application_id)
        except ManifestPolicyError as exc:
            return {"status": "failed", "checks": [{"code": str(exc)}]}
        except ClusterError as exc:
            status = "failed" if exc.code in {"resource_ownership_conflict", "cluster_admission_rejected"} else "unavailable"
            return {"status": status, "checks": [{"code": exc.code, "details": exc.details}]}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("DF_KUBE_MCP_PORT", "8001")),
        stateless_http=True,
        json_response=True,
    )
