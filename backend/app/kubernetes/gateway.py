"""Host-side MCP client; the API holds no Kubernetes credential."""

from __future__ import annotations

import json

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import settings


class GatewayError(Exception):
    pass


async def call_kubernetes(role: str, tool: str, arguments: dict) -> dict:
    urls = {
        "reader": settings().kube_reader_mcp_url,
        "validator": settings().kube_validator_mcp_url,
    }
    allowed = {
        "reader": {"kube.list_workloads", "kube.list_services", "kube.get_rollout_status", "kube.get_capabilities"},
        "validator": {"kube.validate_manifests"},
    }
    if role not in urls or tool not in allowed[role]:
        raise GatewayError("kubernetes_tool_denied")
    url = urls[role]
    if not url:
        raise GatewayError("cluster_access_unconfigured")
    try:
        async with streamable_http_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        if result.is_error:
            raise GatewayError("kubernetes_tool_failed")
        value = result.structured_content
        if value is None and len(result.content) == 1 and result.content[0].type == "text":
            value = json.loads(result.content[0].text)
        if not isinstance(value, dict):
            raise GatewayError("kubernetes_tool_invalid_result")
        return value
    except GatewayError:
        raise
    except Exception as exc:
        raise GatewayError("cluster_unavailable") from exc
