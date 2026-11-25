"""DeepSeek Responses adapter. Provider payloads do not escape this module."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol

import httpx


class ModelProviderError(Exception):
    def __init__(
        self, code: str, *, retryable: bool = False, diagnostic: str | None = None, usage: ModelUsage | None = None,
    ):
        self.code = code
        self.retryable = retryable
        self.diagnostic = diagnostic
        self.usage = usage
        super().__init__(code)


@dataclass
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    returned_model: str | None = None
    response_id: str | None = None


@dataclass
class ModelAction:
    calls: list[dict]
    text: str | None
    history_items: list[dict]
    usage: ModelUsage


class ModelProvider(Protocol):
    async def respond(
        self,
        *,
        instructions: str,
        history: list[dict],
        tools: list[dict],
        tool_choice: str,
        output_schema: dict,
    ) -> ModelAction: ...


def _history_items(output: list[dict]) -> list[dict]:
    items = []
    for item in output:
        kind = item.get("type")
        if kind == "function_call":
            items.append(
                {key: item[key] for key in ("type", "call_id", "name", "arguments") if key in item}
            )
        elif kind == "reasoning":
            items.append({"type": "reasoning", "content": item.get("content", [])})
        elif kind == "message":
            items.append({"role": "assistant", "content": item.get("content", [])})
    return items


def _provider_diagnostic(response: httpx.Response) -> str:
    """Return a short, secret-redacted provider diagnostic suitable for worker logs."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "unknown")
        message = str(error.get("message") or error.get("detail") or "")
    elif isinstance(body, dict):
        code = str(body.get("code") or "unknown")
        message = str(body.get("message") or body.get("detail") or "")
    else:
        code, message = "unknown", ""
    message = re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;]+)",
        r"\1[REDACTED]",
        message,
    )
    message = " ".join(message.split())[:500]
    return f"http_status={response.status_code} provider_code={code} message={message or '[none]'}"


def provider_label(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:+/-]{1,180}", value):
        return value
    return None


def reported_token_count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


class DeepSeekResponsesProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: int,
        max_output_tokens: int,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    @property
    def generation_settings(self) -> dict:
        return {"reasoning_effort": self.reasoning_effort, "max_output_tokens": self.max_output_tokens}

    async def respond(
        self,
        *,
        instructions: str,
        history: list[dict],
        tools: list[dict],
        tool_choice: str,
        output_schema: dict,
    ) -> ModelAction:
        if not self.api_key:
            raise ModelProviderError("model_auth_unconfigured")
        generation = self.generation_settings
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": history,
            "reasoning": {"effort": generation["reasoning_effort"]},
            "max_output_tokens": generation["max_output_tokens"],
            "tools": tools,
            "tool_choice": tool_choice,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "assessment_draft",
                    "schema": output_schema,
                }
            },
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ModelProviderError("model_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ModelProviderError("model_unavailable", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise ModelProviderError("model_auth_failed")
        if response.status_code == 429 or response.status_code >= 500:
            raise ModelProviderError("model_unavailable", retryable=True)
        if response.status_code >= 400:
            raise ModelProviderError(
                "model_request_failed", diagnostic=_provider_diagnostic(response)
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ModelProviderError("model_invalid_response", retryable=True) from exc
        if not isinstance(body, dict):
            raise ModelProviderError("model_invalid_response", retryable=True)
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        metadata = ModelUsage(
            input_tokens=reported_token_count(usage.get("input_tokens")),
            output_tokens=reported_token_count(usage.get("output_tokens")),
            latency_ms=int((time.monotonic() - started) * 1000),
            returned_model=provider_label(body.get("model")) if body.get("model") != self.api_key else None,
            response_id=provider_label(body.get("id")) if body.get("id") != self.api_key else None,
        )
        if body.get("status") in {"failed", "incomplete"}:
            raise ModelProviderError("model_incomplete", usage=metadata)
        output = body.get("output") or []
        calls = [item for item in output if item.get("type") == "function_call"]
        text = None
        for item in output:
            if item.get("type") == "message":
                text = "".join(
                    part.get("text", "")
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                )
        return ModelAction(calls=calls, text=text, history_items=_history_items(output), usage=metadata)


class ScriptedProvider:
    """Test-only deterministic provider that returns prebuilt model actions."""

    def __init__(self, actions: list[ModelAction]):
        self.actions = list(actions)

    async def respond(self, **_kwargs) -> ModelAction:
        if not self.actions:
            raise ModelProviderError("model_script_exhausted")
        return self.actions.pop(0)
