"""Google Gemini models.

Three adaptations from the common shape:

* Roles are ``user``/``model``, not ``user``/``assistant``.
* The system prompt is ``systemInstruction``.
* ``responseSchema`` accepts only a restricted subset of JSON Schema — no ``$ref``/``$defs``,
  no ``additionalProperties``, no ``anyOf`` unions. Pydantic emits all of those for any nested
  model, so schemas are flattened before being sent. Without that, structured output fails with
  an opaque 400 on exactly the nested outputs the note pipeline depends on.

The API key travels in the ``x-goog-api-key`` header rather than the documented ``?key=``
query parameter, to keep credentials out of URLs (and therefore out of logs and proxies).
"""

from __future__ import annotations

import copy
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ...errors import ProviderResponseError
from ..base import ContentPart, LLMProvider, LLMResponse, Message, ModelInfo
from ..http import build_client, request_json, stream_sse

log = logging.getLogger(__name__)

#: Keys Gemini's schema dialect accepts. Everything else is dropped.
_ALLOWED_SCHEMA_KEYS = {
    "type",
    "format",
    "description",
    "nullable",
    "enum",
    "items",
    "properties",
    "required",
    "propertyOrdering",
}


def sanitise_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten a JSON Schema into Gemini's restricted dialect.

    Inlines ``$ref`` against ``$defs``, drops unsupported keywords, and collapses
    ``anyOf: [X, null]`` — Pydantic's rendering of ``X | None`` — into ``X`` with
    ``nullable: true``.
    """
    defs: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        found = schema.get(key)
        if isinstance(found, dict):
            defs.update(found)

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 12 or not isinstance(node, dict):
            return node if not isinstance(node, list) else [resolve(i, depth + 1) for i in node]

        # $ref → inline the referenced definition.
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            name = ref.rsplit("/", 1)[-1]
            target = defs.get(name)
            if isinstance(target, dict):
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**copy.deepcopy(target), **merged}, depth + 1)
            return {"type": "string"}

        # anyOf/oneOf — pick the first non-null branch, mark nullable if null was an option.
        for union_key in ("anyOf", "oneOf"):
            options = node.get(union_key)
            if isinstance(options, list) and options:
                non_null = [
                    o for o in options if not (isinstance(o, dict) and o.get("type") == "null")
                ]
                nullable = len(non_null) != len(options)
                chosen = resolve(non_null[0] if non_null else {"type": "string"}, depth + 1)
                if isinstance(chosen, dict) and nullable:
                    chosen["nullable"] = True
                return chosen

        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key not in _ALLOWED_SCHEMA_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {k: resolve(v, depth + 1) for k, v in value.items()}
            elif key == "items":
                cleaned[key] = resolve(value, depth + 1)
            else:
                cleaned[key] = value

        if cleaned.get("type") == "object" and not cleaned.get("properties"):
            # Gemini rejects an object with no declared properties.
            cleaned["properties"] = {"value": {"type": "string"}}
        return cleaned

    result = resolve(schema)
    return result if isinstance(result, dict) else {"type": "object", "properties": {}}


def _convert_parts(content: str | list[ContentPart]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    for part in content:
        if part["type"] == "text":
            parts.append({"text": part["text"]})
        else:
            parts.append({"inline_data": {"mime_type": part["media_type"], "data": part["data"]}})
    return parts


class GoogleLLM(LLMProvider):
    name = "google"
    label = "Google Gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def context_window(self) -> int:
        return 1_000_000

    @property
    def supports_structured_output(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return True

    def _payload(
        self,
        messages: Sequence[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        contents: list[dict[str, Any]] = []
        system_parts: list[str] = []
        for message in messages:
            if message["role"] == "system":
                content = message["content"]
                system_parts.append(
                    content
                    if isinstance(content, str)
                    else " ".join(p["text"] for p in content if p["type"] == "text")
                )
                continue
            contents.append(
                {
                    "role": "model" if message["role"] == "assistant" else "user",
                    "parts": _convert_parts(message["content"]),
                }
            )
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Continue."}]}]

        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if json_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = sanitise_schema(json_schema)

        payload: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return payload

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> tuple[str, str]:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            # A prompt blocked by safety filters comes back with no candidates at all.
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason")
            if reason:
                raise ProviderResponseError(
                    f"Gemini blocked the request ({reason}). Try a different model, or "
                    "adjust the source material.",
                    provider="google",
                )
            raise ProviderResponseError("Gemini returned no candidates.", provider="google")

        candidate = candidates[0]
        finish = str(candidate.get("finishReason") or "stop")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        return text, finish

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        data = await request_json(
            self._client,
            "POST",
            f"/models/{model}:generateContent",
            provider=self.name,
            label=self.label,
            json=self._payload(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema,
            ),
        )
        text, finish = self._extract_text(data)
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=model,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
            finish_reason=finish,
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
        async for chunk in stream_sse(
            self._client,
            "POST",
            f"/models/{model}:streamGenerateContent",
            provider=self.name,
            label=self.label,
            params={"alt": "sse"},
            json=self._payload(messages, temperature=temperature, max_tokens=max_tokens),
        ):
            for candidate in chunk.get("candidates") or []:
                for part in (candidate.get("content") or {}).get("parts") or []:
                    text = part.get("text") if isinstance(part, dict) else None
                    if text:
                        yield str(text)

    async def list_models(self) -> list[ModelInfo]:
        data = await request_json(
            self._client,
            "GET",
            "/models",
            provider=self.name,
            label=self.label,
            params={"pageSize": 200},
        )
        models = []
        for item in data.get("models") or []:
            if not isinstance(item, dict):
                continue
            methods = item.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            raw_name = str(item.get("name") or "")
            model_id = raw_name.removeprefix("models/")
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    name=str(item.get("displayName") or model_id),
                    context_window=int(item.get("inputTokenLimit") or 0),
                )
            )
        return sorted(models, key=lambda m: m.id)
