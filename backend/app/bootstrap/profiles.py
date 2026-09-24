"""Small, reviewed local target policy. It intentionally performs no cluster discovery."""

from __future__ import annotations

import hashlib
import json

from app.config import settings


def local_k3s_profile() -> dict:
    config = settings()
    registry = config.ghcr_registry
    image = config.ghcr_image
    if not registry or not image:
        raise ValueError("environment_profile_unconfigured")
    profile = {
        "id": "local-k3s",
        "namespace": "deployflow-test",
        "registry": registry,
        "default_image": image,
        "ingress_class": "traefik",
        "hostname_suffix": ".deployflow.local",
        "max_replicas": 3,
        "allowed_secret_names": [],
        "node_base_image": config.node_base_image,
        "checkout_action": "actions/checkout@v4",
        "setup_node_action": "actions/setup-node@v4",
    }
    profile["hash"] = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
    return profile
