"""Credential-free HTTP/TLS probe for an approved external application host."""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.deployflow\.local$")


class ProbeRequest(BaseModel):
    hostname: str
    path: str
    tls: bool = False


@app.post("/probe")
def probe(body: ProbeRequest):
    if (
        len(body.hostname) > 253 or not _HOST.fullmatch(body.hostname)
        or len(body.path) > 256 or not body.path.startswith("/")
        or ".." in body.path or "?" in body.path or "#" in body.path
        or any(char.isspace() for char in body.path)
    ):
        raise HTTPException(400, "External probe target is not allowed")
    scheme = "https" if body.tls else "http"
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", f"{scheme}://{body.hostname}{body.path}") as response:
                status = response.status_code
        if 200 <= status < 300:
            return {"status": "passed", "hostname": body.hostname, "path": body.path,
                    "scheme": scheme}
        return {"status": "pending", "code": "external_http_unhealthy",
                "hostname": body.hostname, "path": body.path, "scheme": scheme}
    except httpx.ConnectError:
        return {"status": "pending", "code": "external_dns_or_connect_failed",
                "hostname": body.hostname, "path": body.path, "scheme": scheme}
    except httpx.TimeoutException:
        return {"status": "pending", "code": "external_timeout",
                "hostname": body.hostname, "path": body.path, "scheme": scheme}
    except httpx.HTTPError:
        return {"status": "pending", "code": "external_transport_failed",
                "hostname": body.hostname, "path": body.path, "scheme": scheme}
