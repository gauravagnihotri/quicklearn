"""Anthropic Claude models.

Anthropic's Messages API differs from the OpenAI dialect in three ways that matter here: the
system prompt is a top-level field rather than a message, image parts use a ``source`` object,
and there is no ``response_format``.

Structured output is therefore implemented with **forced tool use** — the schema is declared as
a tool and ``tool_choice`` requires it. That yields schema-conformant output by construction,
which is a materially better guarantee than asking for JSON in the prompt and hoping. Given
Claude is a likely pick for the ``standard`` and ``deep`` classes, it's worth the extra code.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ...errors import ProviderResponseError
from ..base import ContentPart, LLMProvider, LLMResponse, Message, ModelInfo
from ..http import build_client, request_json, stream_sse

log = logging.getLogger(__name__)

API_VERSION = "2023-06-01"
STRUCTURED_TOOL_NAME = "emit_result"

#: Used only if the models endpoint is unavailable, so the picker is never empty.
FALLBACK_MODELS = [
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4-1",
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
]


def _convert_content(content: str | list[ContentPart]) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if part["type"] == "text":
            parts.append({"type": "text", "text": part["text"]})
        else:
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part["media_type"],
                        "data": part["data"],
                    },
                }
            )
    return parts


class AnthropicLLM(LLMProvider):
    name = "anthropic"
    label = "Anthropic"
    default_base_url = "https://api.anthropic.com"

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_structured_output(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return True

    def _split_system(self, messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Hoist system messages out; Anthropic takes them as a top-level parameter."""
        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []
        for message in messages:
            if message["role"] == "system":
                content = message["content"]
                system_parts.append(
                    content
                    if isinstance(content, str)
                    else " ".join(p["text"] for p in content if p["type"] == "text")
                )
            else:
                turns.append(
                    {"role": message["role"], "content": _convert_content(message["content"])}
                )
        # The API requires the conversation to open with a user turn.
        if not turns:
            turns = [{"role": "user", "content": "Continue."}]
        return "\n\n".join(system_parts), turns

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        system, turns = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        if json_schema is not None:
            payload["tools"] = [
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the result in the required structure.",
                    "input_schema": json_schema,
                }
            ]
            payload["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL_NAME}

        data = await request_json(
            self._client,
            "POST",
            "/v1/messages",
            provider=self.name,
            label=self.label,
            json=payload,
        )

        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderResponseError("Anthropic returned no content blocks.", provider=self.name)

        text_chunks: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_chunks.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use" and block.get("name") == STRUCTURED_TOOL_NAME:
                # Already a validated object; re-serialise so callers see JSON text uniformly.
                text_chunks.append(json.dumps(block.get("input") or {}))

        usage = data.get("usage") or {}
        return LLMResponse(
            text="".join(text_chunks),
            model=str(data.get("model") or model),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=str(data.get("stop_reason") or "stop"),
            raw=data,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        system, turns = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system

        async for event in stream_sse(
            self._client,
            "POST",
            "/v1/messages",
            provider=self.name,
            label=self.label,
            json=payload,
        ):
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if text:
                        yield str(text)

    async def list_models(self) -> list[ModelInfo]:
        try:
            data = await request_json(
                self._client, "GET", "/v1/models", provider=self.name, label=self.label
            )
        except Exception:
            # The key may still be valid even if this endpoint isn't reachable, so degrade to
            # a static list rather than presenting the user an empty model picker.
            log.debug("anthropic /v1/models unavailable; using fallback list")
            return [ModelInfo(id=m) for m in FALLBACK_MODELS]

        items = data.get("data")
        if not isinstance(items, list) or not items:
            return [ModelInfo(id=m) for m in FALLBACK_MODELS]
        return [
            ModelInfo(
                id=str(item["id"]),
                name=str(item.get("display_name") or item["id"]),
                context_window=200_000,
            )
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
