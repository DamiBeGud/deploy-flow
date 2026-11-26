"""Small, reviewed local target policy. It intentionally performs no cluster discovery."""

from __future__ import annotations

import hashlib
import json

from app.config import settings

LOCAL_PROFILE_ID = "local-k3s"
LOCAL_PROFILE_NAMESPACE = "deployflow-test"


def local_k3s_profile() -> dict:
    config = settings()
    registry = config.ghcr_registry
    image = config.ghcr_image
    if not registry or not image:
        raise ValueError("environment_profile_unconfigured")
    profile = {
        "id": LOCAL_PROFILE_ID,
        "namespace": LOCAL_PROFILE_NAMESPACE,
        "registry": registry,
        "default_image": image,
        "ingress_class": "nginx",
        "hostname_suffix": ".deployflow.local",
        "max_replicas": 3,
        "allowed_secret_names": [],
        "node_base_image": config.node_base_image,
        "checkout_action": "actions/checkout@v4",
        "setup_node_action": "actions/setup-node@v4",
    }
    profile["hash"] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    return profile
