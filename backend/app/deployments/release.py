"""Bind a verified CI image to the exact reviewed Kubernetes manifests.

This module does not mutate GitHub or Kubernetes resources. Manifest policy
can read the approved managed binding catalog. The final bytes are retained for
dry-run and the immutable release record.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from app.bootstrap.contracts import IMAGE_BINDING_NAME
from app.bootstrap.generator import MANAGED_PATHS, canonical_digest
from app.kubernetes.policy import KINDS, checked_manifests

DEPLOYMENT_PATH = "deploy/kubernetes/deployment.yaml"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseSpecification:
    files: dict[str, str]
    digest: str
    image_reference: str
    source_bundle_digest: str


def bind_verified_image(
    *,
    bundle_files: dict[str, str],
    bundle_digest: str,
    image_binding: dict,
    image_repository: str,
    image_digest: str,
    profile: dict,
    application_id: str,
) -> ReleaseSpecification:
    """Change only the declared Deployment image from a tag to a digest."""
    if not bundle_files or set(bundle_files) - MANAGED_PATHS or canonical_digest(bundle_files) != bundle_digest:
        raise ReleaseError("reviewed_bundle_mismatch")
    if not isinstance(image_binding, dict) or (
        image_binding.get("name") != IMAGE_BINDING_NAME
        or image_binding.get("repository") != image_repository
        or image_binding.get("status") != "provisional"
        or image_binding.get("final_reference_kind") != "digest"
    ):
        raise ReleaseError("image_binding_mismatch")
    if (
        not isinstance(image_repository, str)
        or not image_repository.startswith(profile["registry"].rstrip("/") + "/")
        or not isinstance(image_digest, str)
        or not _DIGEST.fullmatch(image_digest)
    ):
        raise ReleaseError("verified_image_invalid")
    preview = image_binding.get("preview_reference")
    if not isinstance(preview, str) or not re.fullmatch(re.escape(image_repository) + r":[0-9a-f]{40}", preview):
        raise ReleaseError("image_binding_mismatch")

    original = checked_manifests(
        bundle_files,
        digest=bundle_digest,
        namespace=profile["namespace"],
        application_id=application_id,
        registry=profile["registry"],
    )
    deployment = next(item for item in original if item["kind"] == "Deployment")
    containers = deployment["spec"]["template"]["spec"]["containers"]
    source = bundle_files[DEPLOYMENT_PATH]
    if any(container["image"] != preview for container in containers) or source.count(preview) != len(containers):
        raise ReleaseError("image_binding_mismatch")

    image_reference = f"{image_repository}@{image_digest}"
    files = {path: content for path, content in bundle_files.items() if path in KINDS}
    files[DEPLOYMENT_PATH] = source.replace(preview, image_reference, len(containers))
    digest = canonical_digest(files)
    final = checked_manifests(
        files,
        digest=digest,
        namespace=profile["namespace"],
        application_id=application_id,
        registry=profile["registry"],
    )
    expected = copy.deepcopy(original)
    expected_deployment = next(item for item in expected if item["kind"] == "Deployment")
    for container in expected_deployment["spec"]["template"]["spec"]["containers"]:
        container["image"] = image_reference
    if final != expected:
        raise ReleaseError("release_manifest_changed")
    return ReleaseSpecification(files=files, digest=digest, image_reference=image_reference, source_bundle_digest=bundle_digest)
