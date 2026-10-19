"""Credential-free, namespace-bound HTTP check run inside the cluster."""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_SERVICE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


@app.get("/ready")
def ready():
    return {"status": "ready"}


@app.get("/probe/{service}/{port}/{path:path}")
def probe(service: str, port: int, path: str):
    if (
        len(service) > 63 or not _SERVICE.fullmatch(service)
        or port < 1 or port > 65535 or len(path) > 255
        or ".." in path or "?" in path or "#" in path
        or any(char.isspace() for char in path)
    ):
        raise HTTPException(400, "Health target is not allowed")
    try:
        url = f"http://{service}.deployflow-test.svc.cluster.local:{port}/{path}"
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url) as response:
                code = response.status_code
        if 200 <= code < 300:
            return {"status": "passed", "service": service, "path": f"/{path}"}
        return {"status": "pending", "service": service, "path": f"/{path}",
                "code": "application_unhealthy"}
    except httpx.HTTPError:
        return {"status": "pending", "service": service, "path": f"/{path}",
                "code": "service_unreachable"}
