"""Deterministic, image-only platform upgrade/rollback plans. No I/O."""

from __future__ import annotations

import copy
import hashlib
import json
import re

NS = "deployflow-system"
NS_UID = "d611539e-df7d-4826-b76d-2bd0c4d97225"
TARGET = "deployflow-test"
TARGET_UID = "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"
PART = "deployflow-platform"
HOST = "deployflow.damibegud.cloud"
SOURCE_ANNOTATION = "deployflow.io/platform-source"
ROLES = {name: "backend" for name in (
    "api", "webhook", "dispatcher", "kube-reader", "kube-validator", "worker",
    "github-worker", "delivery-worker", "kube-deployer", "external-probe", "kube-cleaner",
)} | {"frontend": "frontend", "keycloak": "keycloak"}
REQUIRED = set(ROLES) - {"kube-cleaner"}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def image_role(reference):
    match = re.fullmatch(r"ghcr\.io/damibegud/deployflow-(backend|frontend|keycloak)@sha256:[a-f0-9]{64}", reference)
    if not match:
        raise ValueError("Platform image must use its approved repository and immutable digest")
    return match[1]


def images(spec):
    return {group + "/" + item["name"]: item["image"]
            for group in ("containers", "initContainers")
            for item in spec["template"]["spec"].get(group, [])}


def contract(spec):
    """Remove only known API defaults, replica count, images and restart markers.

    Unknown live fields remain significant: removing/changing a renderer field
    must not silently become an image-only upgrade. Live replicas are preserved.
    """
    result = copy.deepcopy(spec)
    result.pop("replicas", None)
    for key, value in (("progressDeadlineSeconds", 600), ("revisionHistoryLimit", 10)):
        if result.get(key) == value:
            result.pop(key)
    template = result["template"]
    template["metadata"].pop("creationTimestamp", None)
    annotations = template["metadata"].get("annotations", {})
    for key in ("kubectl.kubernetes.io/restartedAt", "deployflow.io/capabilities-review"):
        annotations.pop(key, None)
    if not annotations:
        template["metadata"].pop("annotations", None)
    pod = template["spec"]
    if pod.get("serviceAccount") == pod.get("serviceAccountName"):
        pod.pop("serviceAccount", None)
    for key, value in (("dnsPolicy", "ClusterFirst"), ("restartPolicy", "Always"),
                       ("schedulerName", "default-scheduler"), ("enableServiceLinks", True)):
        if pod.get(key) == value:
            pod.pop(key)
    for volume in pod.get("volumes", []):
        for key in ("secret", "configMap"):
            if volume.get(key, {}).get("defaultMode") == 420:
                volume[key].pop("defaultMode")
    for group in ("containers", "initContainers"):
        for item in pod.get(group, []):
            item["image"] = "<approved immutable image>"
            if item.get("env") == []:
                item.pop("env")
            for key, value in (("terminationMessagePath", "/dev/termination-log"), ("terminationMessagePolicy", "File")):
                if item.get(key) == value:
                    item.pop(key)
            for port in item.get("ports", []):
                if port.get("protocol") == "TCP":
                    port.pop("protocol")
            for key in ("startupProbe", "readinessProbe", "livenessProbe"):
                if key not in item:
                    continue
                probe = item[key]
                for field, default in (("timeoutSeconds", 1), ("successThreshold", 1),
                                       ("failureThreshold", 3), ("initialDelaySeconds", 0)):
                    if probe.get(field) == default:
                        probe.pop(field)
                if probe.get("httpGet", {}).get("scheme") == "HTTP":
                    probe["httpGet"].pop("scheme")
    return result


def deployment_snapshot(item):
    meta, spec = item["metadata"], item["spec"]
    name = meta["name"]
    if (name not in ROLES or meta.get("namespace") != NS
            or meta.get("labels", {}).get("app.kubernetes.io/part-of") != PART):
        raise ValueError("Unknown or unowned platform Deployment")
    refs = images(spec)
    if not refs or any(image_role(ref) != ROLES[name] for ref in refs.values()):
        raise ValueError("Existing container image has unexpected repository/role")
    return {"uid": meta["uid"], "generation": meta["generation"], "spec_sha256": digest(spec),
            "annotations_sha256": digest(meta.get("annotations", {})), "replicas": spec.get("replicas", 1),
            "source_sha": meta.get("annotations", {}).get(SOURCE_ANNOTATION),
            "images": refs, "contract_sha256": digest(contract(spec))}


def make_plan(snapshot, release, *, origin=None):
    if snapshot["namespace_uid"] != NS_UID or snapshot["target_namespace_uid"] != TARGET_UID:
        raise ValueError("Namespace identity changed")
    if snapshot["schema_revision"] != release["schema_revision"]:
        raise ValueError("Schema change requires a separately approved migration; upgrade does not migrate")
    if snapshot["keycloak_version"] != release["keycloak_version"]:
        raise ValueError("Keycloak version change requires separate identity migration review")
    if not snapshot.get("keycloak_version_observed", True) and not origin:
        raise ValueError("Current Keycloak version is unavailable")
    if origin and snapshot["schema_fingerprints"] != origin["before"]["schema_fingerprints"]:
        raise ValueError("Database structure changed; image rollback requires separate recovery review")
    if any(snapshot["pending_jobs"].values()):
        raise ValueError("Pending/running/blocked work requires separate recovery or a settled maintenance window")
    deployments = snapshot["deployments"]
    if not REQUIRED <= set(deployments) or set(deployments) - set(ROLES):
        raise ValueError("Platform workload inventory changed")
    changes = []
    for name, current in sorted(deployments.items()):
        if current["contract_sha256"] != digest(release["contracts"].get(name)):
            raise ValueError("Workload configuration change requires separate review: " + name)
        if origin:
            old = origin["before"]["deployments"].get(name)
            proposed = origin["targets"].get(name)
            if not old or not proposed or old["uid"] != current["uid"]:
                raise ValueError("Rollback Deployment inventory/UID changed")
            if current["images"] not in (old["images"], proposed["images"]):
                raise ValueError("Unexpected intervening image; refusing rollback over another upgrade")
            if current["source_sha"] not in (old["source_sha"], origin["release"]["source_sha"]):
                raise ValueError("Intervening source annotation changed; refusing old rollback")
            target_images = old["images"]
            source = old["source_sha"]
        else:
            target_images = {key: release["images"][ROLES[name]] for key in current["images"]}
            source = release["source_sha"]
        if any(image_role(value) != ROLES[name] for value in target_images.values()):
            raise ValueError("Target image role differs")
        if current["images"] != target_images or current["source_sha"] != source:
            changes.append({"name": name, "uid": current["uid"], "generation": current["generation"],
                            "spec_sha256": current["spec_sha256"], "annotations_sha256": current["annotations_sha256"],
                            "replicas": current["replicas"], "from_images": current["images"],
                            "to_images": target_images, "from_source": current["source_sha"], "to_source": source})
    return {"format": 1, "operation": "rollback" if origin else "upgrade",
            "origin_receipt": origin["id"] if origin else None, "source_sha": release["source_sha"],
            "schema_revision": release["schema_revision"], "keycloak_version": release["keycloak_version"],
            "namespace": NS, "namespace_uid": NS_UID, "target_namespace_uid": TARGET_UID,
            "changes": changes, "before": snapshot,
            "effects": {"writes": "Deployment images and source annotations only",
                        "database": "Private backups of both DBs; bounded application-table SHARE locks pause writes during rollout; no migrations/restores",
                        "interruption": "Recreate rollouts interrupt API/login and workers; writes/webhooks may fail or time out during the maintenance window",
                        "preserved": "Live flags, policies, accounts, Secrets, ServiceAccounts/RBAC, Services/Ingress, StatefulSets and PVCs",
                        "failure": "Stop and retain private journal; no automatic rollback. Separately preview/approve rollback of recorded image targets only"}}


def image_patch(item, change):
    current = deployment_snapshot(item)
    for key in ("uid", "generation", "spec_sha256", "annotations_sha256"):
        if current[key] != change[key]:
            raise ValueError("Deployment changed since review: " + item["metadata"]["name"])
    patch = [{"op": "test", "path": "/metadata/uid", "value": current["uid"]},
             {"op": "test", "path": "/metadata/generation", "value": current["generation"]},
             {"op": "test", "path": "/spec", "value": item["spec"]}]
    for group in ("containers", "initContainers"):
        for index, container in enumerate(item["spec"]["template"]["spec"].get(group, [])):
            key = group + "/" + container["name"]
            if container["image"] != change["to_images"][key]:
                patch.append({"op": "replace", "path": f"/spec/template/spec/{group}/{index}/image",
                              "value": change["to_images"][key]})
    annotations = copy.deepcopy(item["metadata"].get("annotations", {}))
    if change["to_source"]:
        annotations[SOURCE_ANNOTATION] = change["to_source"]
    else:
        annotations.pop(SOURCE_ANNOTATION, None)
    if "annotations" in item["metadata"]:
        patch.append({"op": "test", "path": "/metadata/annotations", "value": item["metadata"]["annotations"]})
    patch.append({"op": "add", "path": "/metadata/annotations", "value": annotations})
    return patch


def verification_plan(current, origin):
    """Recovery verifies exactly the applied target; never writes Kubernetes."""
    if origin["status"] not in {"failed", "awaiting_public_verification"} or origin.get("reverted_by") or origin.get("recovered_by"):
        raise ValueError("Receipt is not eligible for verification recovery")
    before = origin["before"]
    for key in ("namespace_uid", "target_namespace_uid", "schema_revision", "schema_fingerprints",
                "keycloak_version", "protected", "flags", "application_policies"):
        if current[key] != before[key]:
            raise ValueError("Recovery context changed: " + key)
    if set(current["deployments"]) != set(origin["targets"]):
        raise ValueError("Recovery workload inventory changed")
    for name, target in origin["targets"].items():
        row = current["deployments"][name]
        if (row["uid"] != target["uid"] or row["images"] != target["images"]
                or row["contract_sha256"] != before["deployments"][name]["contract_sha256"]
                or row["source_sha"] != target_source(origin, name)):
            raise ValueError("Recovery target changed: " + name)
    return {"format": 1, "operation": "verify", "origin_receipt": origin["id"],
            "source_sha": origin["release"]["source_sha"], "before": current,
            "effects": "No resource patches, restarts, locks, migrations or backups; verify exact applied images and independent HTTPS, retain original failure"}


def target_source(receipt, name):
    change = next((c for c in receipt["plan"].get("changes", []) if c["name"] == name), None)
    return change["to_source"] if change else receipt["before"]["deployments"][name]["source_sha"]
