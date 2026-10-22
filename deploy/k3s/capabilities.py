"""Prepare an existing platform's execution/cleanup activation for scoped review.

Offline only: this never connects to Kubernetes, changes a policy, or executes a
release. Fresh-install defaults remain disabled. The saved snapshot must be
rechecked immediately before a separately approved live operation.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
from pathlib import Path

from bundle import digest
from render import IMAGE_REPOSITORIES, NAMESPACE, REVISION, TARGET, resource, runtime

RESTART = ("api", "dispatcher", "delivery-worker", "kube-deployer")
FLAGS = ("DF_DEPLOYMENT_EXECUTION_ENABLED", "DF_DEPLOYMENT_CLEANUP_ENABLED")


def _object(snapshot, kind, name):
    rows = [row for row in snapshot["objects"][kind] if row["metadata"]["name"] == name]
    if len(rows) != 1:
        raise ValueError("Expected exactly one current " + name)
    value = rows[0]
    if value["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") != "deployflow-platform":
        raise ValueError("Resource ownership differs: " + name)
    return value


def _patch(value, operations):
    meta = value["metadata"]
    return {"kind": value["kind"], "namespace": meta["namespace"], "name": meta["name"],
            "json_patch": [
                {"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
                {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
                *operations,
            ]}


def prepare(snapshot):
    if any(row.get("enabled") for row in snapshot["policies"]):
        raise ValueError("Enabled application policies require a separate activation scope")
    for row in snapshot["jobs"]:
        if row["kind"] == "execution" and row["count"]:
            raise ValueError("Existing execution/cleanup jobs require a separate recovery scope")
        if row["kind"] in {"github", "delivery"} and row["status"] not in {"completed", "failed"} and row["count"]:
            raise ValueError("Pending release verification/preparation must settle before activation")
        if row["kind"] == "analysis_pending" and row["count"]:
            raise ValueError("Pending analysis must settle before activation")
    for kind in ("deployments", "serviceaccounts"):
        if any(row["metadata"]["name"] == "kube-cleaner" for row in snapshot["objects"][kind]):
            raise ValueError("Cleaner already exists; review its current configuration separately")
    cleaner_role = next(row for row in snapshot["target_roles"] if row["metadata"]["name"] == "deployflow-cleaner")
    if not cleaner_role.get("absent"):
        raise ValueError("Existing cleaner Role must be reviewed separately; no overwrite/adoption")

    config = _object(snapshot, "configmaps", "deployflow-config")
    if any(config["data"].get(flag) != "false" for flag in FLAGS):
        raise ValueError("Expected both feature gates disabled before first activation")
    deployer = _object(snapshot, "deployments", "kube-deployer")
    image = deployer["spec"]["template"]["spec"]["containers"][0]["image"]
    if not re.fullmatch(re.escape(IMAGE_REPOSITORIES["backend"]) + r"@sha256:[0-9a-f]{64}", image):
        raise ValueError("Cleaner requires the existing approved immutable backend image")
    cleaner = runtime("kube-cleaner", image,
                      ["dramatiq", "app.worker", "--processes", "1", "--threads", "1", "--queues", "cleanup"],
                      identity=True)
    creates = [
        resource("ServiceAccount", "kube-cleaner", automountServiceAccountToken=False),
        resource("Role", "deployflow-cleaner", namespace=TARGET, api_version="rbac.authorization.k8s.io/v1", rules=[
            {"apiGroups": [""], "resources": ["configmaps", "services"], "verbs": ["get", "delete"]},
            {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "delete"]},
            {"apiGroups": ["networking.k8s.io"], "resources": ["ingresses"], "verbs": ["get", "delete"]},
        ]),
        resource("RoleBinding", "deployflow-platform-cleaner", namespace=TARGET,
                 api_version="rbac.authorization.k8s.io/v1",
                 subjects=[{"kind": "ServiceAccount", "name": "kube-cleaner", "namespace": NAMESPACE}],
                 roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "deployflow-cleaner"}),
    ]
    networks = []
    peer = {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "kube-cleaner"}}}
    for name in ("allow-postgres", "allow-redis"):
        policy = _object(snapshot, "networkpolicies", name)
        if peer in policy["spec"]["ingress"][0]["from"]:
            raise ValueError("Cleaner network permission already exists; review separately")
        networks.append(_patch(policy, [{"op": "add", "path": "/spec/ingress/0/from/-", "value": peer}]))
    config_patch = _patch(config, [
        *[{"op": "test", "path": "/data/" + flag, "value": "false"} for flag in FLAGS],
        *[{"op": "replace", "path": "/data/" + flag, "value": "true"} for flag in FLAGS],
    ])
    restarts = []
    for name in RESTART:
        current = _object(snapshot, "deployments", name)
        annotations = copy.deepcopy(current["spec"]["template"]["metadata"].get("annotations", {}))
        annotations["deployflow.io/capabilities-review"] = snapshot["observed_at"]
        restarts.append(_patch(current, [{"op": "add", "path": "/spec/template/metadata/annotations", "value": annotations}]))
    return {
        "format": 1, "operation": "enable-execution-and-cleanup-capabilities",
        "snapshot_observed_at": snapshot["observed_at"], "schema_revision": REVISION,
        "preconditions": {
            "server": "homeserver1", "platform_namespace": NAMESPACE, "target_namespace": TARGET,
            "platform_namespace_uid": "d611539e-df7d-4826-b76d-2bd0c4d97225",
            "target_namespace_uid": "84a720fd-dc36-4592-a2bb-12c8f54ddc7c",
            "enabled_policies": 0, "execution_and_cleanup_jobs": 0,
            "pending_analysis_github_delivery_jobs": 0,
            "new_resource_names_must_be_absent": True,
            "private_current_database_backups_verified": True,
            "recheck_live_state_immediately_before_mutation": True,
        },
        "steps": [
            {"operation": "create-only", "resources": creates},
            {"operation": "json-patch", "resources": networks},
            {"operation": "json-patch", "resources": [config_patch]},
            {"operation": "create-only", "resources": [cleaner]},
            {"operation": "json-patch", "resources": restarts},
        ],
        "application_policy_changes": [], "application_execution_requests": [], "cleanup_requests": [],
        "waiting_releases": [{"deployment_id": row["id"], "release_id": row["release_id"],
                              "status": row["status"]} for row in snapshot["runs"]],
        "expected_effect": "Expose execution/cleanup controls; no waiting release executes without its separate application policy and execution authorization",
        "interruption": "API and three relevant worker Deployments use Recreate; brief API interruption and worker pause expected",
        "reversal": "Separately disable both ConfigMap flags and restart API/dispatcher/delivery-worker/deployer/cleaner; inspect any newly queued work first. Retain data and identity/RBAC; deletion requires separate approval",
        "approval": "Required for this exact plan; offline preparation grants no operational authority",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = prepare(json.loads(args.snapshot.read_text()))
    # A new file prevents accidentally replacing a previously reviewed plan.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    print("REVIEW ONLY. No cluster changes, policy writes, deployments or cleanup.")
    print("Plan SHA256:", digest(plan))


if __name__ == "__main__":
    main()
