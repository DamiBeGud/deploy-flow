"""DeepSeek Responses adapter. Provider payloads do not escape this module."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx


class ModelProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    returned_model: str | None = None


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
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": history,
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
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
            raise ModelProviderError("model_request_failed")
        body = response.json()
        if body.get("status") in {"failed", "incomplete"}:
            raise ModelProviderError("model_incomplete")
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
        usage = body.get("usage") or {}
        return ModelAction(
            calls=calls,
            text=text,
            history_items=_history_items(output),
            usage=ModelUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                latency_ms=int((time.monotonic() - started) * 1000),
                returned_model=body.get("model"),
            ),
        )


class ScriptedProvider:
    """Test-only deterministic provider that returns prebuilt model actions."""

    def __init__(self, actions: list[ModelAction]):
        self.actions = list(actions)

    async def respond(self, **_kwargs) -> ModelAction:
        if not self.actions:
            raise ModelProviderError("model_script_exhausted")
        return self.actions.pop(0)
