"""Reference-only catalog and Secret reconciliation for a preapproved Rails pilot."""

from __future__ import annotations

import base64
import os
import secrets
from contextlib import contextmanager
from uuid import UUID, uuid4

from kubernetes import client
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from urllib3.exceptions import HTTPError

from app.bootstrap.postgres import PostgresBinding
from app.kubernetes.client import NAMESPACE, ClusterError, _api_client

CATALOG_NAMESPACE = "deployflow-system"
CATALOG_NAME = "deployflow-rails-bindings"
SECRET_NAMES = tuple(f"deployflow-rails-slot-{index}" for index in range(1, 5))
SOURCE_MARKER = "DEPLOYFLOW_RAILS_SOURCE_SHA"
TIMEOUT = (2, 5)


class ManagedRailsError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.retryable = retryable


class RailsProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str = Field(min_length=1, max_length=128)
    organization_id: str = Field(min_length=1, max_length=128)
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    revision: str = Field(min_length=1, max_length=255)
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


def managed_profile() -> RailsProfile | None:
    raw = os.environ.get("DF_MANAGED_RAILS_PROFILE")
    if not raw:
        return None
    try:
        return RailsProfile.model_validate_json(raw)
    except ValidationError:
        raise ManagedRailsError("rails_profile_invalid") from None


class Reservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lease: UUID
    binding: PostgresBinding
    ready: bool = False


def catalog_key(application_id: UUID, source_sha: str) -> str:
    return f"{application_id}.{source_sha}"


def _entries(config_map) -> dict[str, Reservation]:
    entries = {}
    slots = set()
    try:
        for key, record in (config_map.data or {}).items():
            entry = Reservation.model_validate_json(record)
            binding = entry.binding
            if (
                not binding.managed or binding.runtime != "ruby" or not binding.ephemeral
                or binding.api_token or binding.migration_module
                or key != catalog_key(binding.application_id, binding.source_sha)
                or binding.secret_name not in SECRET_NAMES or binding.secret_name in slots
            ):
                raise ManagedRailsError("rails_catalog_invalid")
            slots.add(binding.secret_name)
            entries[key] = entry
    except ValidationError:
        raise ManagedRailsError("rails_catalog_invalid") from None
    return entries


@contextmanager
def _catalog_api():
    # Only projected, workload-specific credentials are accepted here.
    if os.environ.get("DF_KUBE_ACCESS_MODE") != "incluster":
        raise ManagedRailsError("rails_cluster_identity_required")
    try:
        with _api_client() as api:
            yield client.CoreV1Api(api)
    except ClusterError:
        raise ManagedRailsError("rails_cluster_access_unavailable", retryable=True) from None
    except ApiException as exc:
        if exc.status in {401, 403}:
            raise ManagedRailsError("rails_cluster_access_denied") from None
        if exc.status == 422:
            raise ManagedRailsError("rails_admission_denied") from None
        raise ManagedRailsError("rails_cluster_unavailable", retryable=True) from None
    except (HTTPError, OSError):
        raise ManagedRailsError("rails_cluster_unavailable", retryable=True) from None


def _read_catalog(api):
    return api.read_namespaced_config_map(CATALOG_NAME, CATALOG_NAMESPACE, _request_timeout=TIMEOUT)


def _set_entry(api, catalog, key: str, entry: Reservation):
    # Optimistic concurrency prevents another worker from allocating the same slot.
    body = {
        "metadata": {"resourceVersion": catalog.metadata.resource_version},
        "data": {key: entry.model_dump_json()},
    }
    api.patch_namespaced_config_map(
        CATALOG_NAME, CATALOG_NAMESPACE, body,
        _content_type="application/merge-patch+json", _request_timeout=TIMEOUT,
    )


def managed_binding(application_id: str, source_sha: str) -> PostgresBinding | None:
    try:
        key = catalog_key(UUID(application_id), source_sha)
    except ValueError:
        return None
    with _catalog_api() as api:
        entry = _entries(_read_catalog(api)).get(key)
        return entry.binding if entry and entry.ready else None


def _ensure_secret(api, reservation: Reservation, *, allow_create: bool):
    binding = reservation.binding
    annotations = {
        "deployflow.io/rails-reservation": str(reservation.lease),
        "deployflow.io/application-id": str(binding.application_id),
        "deployflow.io/source-sha": binding.source_sha,
    }
    try:
        current = api.read_namespaced_secret(binding.secret_name, NAMESPACE, _request_timeout=TIMEOUT)
    except ApiException as exc:
        if exc.status != 404:
            raise
        if not allow_create:
            raise ManagedRailsError("rails_ready_secret_missing") from None
        api.create_namespaced_secret(NAMESPACE, {
            "metadata": {"name": binding.secret_name, "annotations": annotations},
            "type": "Opaque",
            "immutable": True,
            "data": {
                "password": base64.b64encode(secrets.token_urlsafe(32).encode()).decode(),
                "SECRET_KEY_BASE": base64.b64encode(secrets.token_hex(64).encode()).decode(),
            },
        }, _request_timeout=TIMEOUT)
        return
    if (
        current.metadata.deletion_timestamp or not current.immutable or current.type != "Opaque"
        or current.metadata.annotations != annotations
        or set(current.data or {}) != {"password", "SECRET_KEY_BASE"}
        or not all(current.data.values())
    ):
        raise ManagedRailsError("rails_secret_ownership_conflict")


def provision_rails(binding: PostgresBinding):
    """Reserve before creating. Reconcile interrupted creates via the reservation ID."""
    key = catalog_key(binding.application_id, binding.source_sha)
    with _catalog_api() as api:
        catalog = _read_catalog(api)
        entries = _entries(catalog)
        entry = entries.get(key)
        if not entry:
            used = {item.binding.secret_name for item in entries.values()}
            slot = next((name for name in SECRET_NAMES if name not in used), None)
            if slot is None:
                raise ManagedRailsError("rails_slots_exhausted")
            entry = Reservation(lease=uuid4(), binding=binding.model_copy(update={"secret_name": slot}))
            _set_entry(api, catalog, key, entry)
            catalog = _read_catalog(api)
            if _entries(catalog).get(key) != entry:
                raise ManagedRailsError("rails_binding_ownership_conflict")
        if entry.binding != binding.model_copy(update={"secret_name": entry.binding.secret_name}):
            raise ManagedRailsError("rails_binding_ownership_conflict")
        _ensure_secret(api, entry, allow_create=not entry.ready)
        if not entry.ready:
            _set_entry(api, catalog, key, entry.model_copy(update={"ready": True}))
