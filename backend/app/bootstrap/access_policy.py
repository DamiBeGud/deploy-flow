"""Application access approvals independent of runtime and dependency bindings."""

import os
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class AccessPolicyError(ValueError):
    pass


class AccessPolicyBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    application_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    repository: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*$")
    source_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    namespace: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    exposure: Literal["internal"]
    authentication: Literal["application_bearer"]
    probe_authentication: Literal["none"]
    readiness_path: str = Field(pattern=r"^/[A-Za-z0-9/_-]*$", max_length=255)
    liveness_path: str = Field(pattern=r"^/[A-Za-z0-9/_-]*$", max_length=255)

    def public_policy(self) -> dict[str, str]:
        return self.model_dump(include={
            "exposure", "authentication", "probe_authentication", "readiness_path", "liveness_path",
        })


_CATALOG = TypeAdapter(list[AccessPolicyBinding])


def access_policy_for_source(
    assessment: dict, application_id: str, *, profile_id: str, namespace: str,
) -> AccessPolicyBinding | None:
    if not application_id:
        return None
    try:
        catalog = _CATALOG.validate_json(os.getenv("DF_APPROVED_ACCESS_POLICIES") or "[]")
    except ValidationError:
        raise AccessPolicyError("access_policy_catalog_invalid") from None
    assignments = [row for row in catalog if str(row.application_id) == application_id]
    if not assignments:
        return None
    matches = [row for row in assignments if row.profile_id == profile_id and row.namespace == namespace]
    if len(matches) != 1:
        raise AccessPolicyError("access_policy_target_mismatch")
    binding = matches[0]
    source = assessment.get("repository", {})
    slug = f"{source.get('owner', '')}/{source.get('name', '')}".lower()
    if (
        slug != binding.repository
        or source.get("commit_sha") != binding.source_sha
        or assessment.get("application", {}).get("name") != binding.application_name
        or "API_TOKEN" not in assessment.get("configuration", {}).get("required_variables", [])
    ):
        raise AccessPolicyError("access_policy_source_mismatch")
    return binding
