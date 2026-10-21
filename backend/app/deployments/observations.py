"""Current read-only cluster observations, separate from deployment history."""
import asyncio
import time
from datetime import datetime, timezone

from app.kubernetes.gateway import GatewayError, call_kubernetes

_lock = asyncio.Lock()
_cached = None
_expires = 0.0


async def cluster_snapshot():
    global _cached, _expires
    async with _lock:
        if _cached is not None and time.monotonic() < _expires:
            return _cached
        attempted = datetime.now(timezone.utc).isoformat()
        try:
            result = await asyncio.wait_for(call_kubernetes("reader", "kube.list_workloads", {}), timeout=12)
            if "workloads" not in result or not result.get("observed_at"):
                raise GatewayError(result.get("error_code") or result.get("code") or "cluster_observation_unavailable")
            _cached = {**result, "attempted_at": attempted}
            _expires = time.monotonic() + 5
        except (GatewayError, TimeoutError) as exc:
            _cached = {"status": "unavailable", "code": str(exc) or "cluster_unavailable", "attempted_at": attempted, "observed_at": None}
            _expires = time.monotonic() + 15
        return _cached


def application_observation(snapshot, *, application_id, resource, release, service=None):
    if snapshot.get("status") == "unavailable":
        return snapshot
    base = {"observed_at": snapshot["observed_at"], "attempted_at": snapshot["attempted_at"], "health": "not_checked"}
    if resource is None:
        return {**base, "status": "not_deployed"}
    rows = [row for row in snapshot["workloads"] if row.get("kind") == "Deployment" and row.get("name") == resource.name]
    if not rows:
        return {**base, "status": "missing", "code": "deployment_absent"}
    row = rows[0]
    if service is not None:
        found = next((item for item in snapshot.get("services", []) if item["name"] == service.name), None)
        if found is None:
            return {**base, "status": "missing", "code": "service_absent"}
        if found.get("uid") != service.uid or found.get("application_id") != str(application_id):
            return {**base, "status": "drifted", "code": "service_identity_changed"}
    # Old readers lack identity fields: never infer ownership from a name.
    if any(row.get(key) is None for key in ("uid", "application_id", "generation", "observed_generation", "desired_replicas", "ready_replicas", "available_replicas", "updated_replicas")):
        return {**base, "status": "unavailable", "code": "reader_upgrade_required"}
    drift = (row["uid"] != resource.uid or row["application_id"] != str(application_id)
             or not row.get("managed") or row.get("image") != release.image_reference
             or row["generation"] != resource.generation)
    ready = (row["observed_generation"] >= row["generation"]
             and all(row[key] >= row["desired_replicas"] for key in ("updated_replicas", "ready_replicas", "available_replicas")))
    failed = any(c.get("type") == "Progressing" and c.get("status") == "False" for c in row.get("conditions", []))
    return {**base, "status": "drifted" if drift else "failed" if failed else "ready" if ready else "pending",
            **{key: row.get(key) for key in ("image", "generation", "observed_generation", "desired_replicas", "updated_replicas", "ready_replicas", "available_replicas", "conditions")}}
