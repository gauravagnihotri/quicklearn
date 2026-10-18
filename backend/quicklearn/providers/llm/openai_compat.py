"""Base class for every provider speaking the OpenAI chat-completions dialect.

Covers OpenAI, Groq, OpenRouter, Ollama and LM Studio. PRD §4.2 notes the last two "share an
OpenAI-compatible base class"; in practice all five do, and collapsing them here means one
implementation of streaming, structured output and usage accounting rather than five that drift
apart.

Structured output is the one genuine capability difference, so each subclass declares its mode
rather than the base guessing.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from ...errors import ProviderResponseError
from ..base import ContentPart, LLMProvider, LLMResponse, Message, ModelInfo
from ..http import DEFAULT_TIMEOUT, LONG_TIMEOUT, build_client, request_json, stream_sse

log = logging.getLogger(__name__)

StructuredMode = Literal["json_schema", "json_object", "none"]


def _convert_content(content: str | list[ContentPart]) -> Any:
    """Translate internal message content into the OpenAI wire shape."""
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if part["type"] == "text":
            parts.append({"type": "text", "text": part["text"]})
        else:
            data_url = f"data:{part['media_type']};base64,{part['data']}"
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


# Local patch: models write LaTeX inside JSON strings without doubling the backslash, so
# `\times` decodes as a tab plus "imes", `\frac` as a form feed, `\beta` as a backspace.
# Only commands that start with a JSON escape letter are at risk; these are the ones that
# turn up in notes. A command must not run on into more letters, so "\nUse" or "\noted"
# (a real newline before a word) are left alone.
_LATEX_AT_RISK = re.compile(
    r"(?<!\\)\\("
    r"t(?:imes|ext(?:bf|it|rm)?|o|heta|au|an|frac|ilde|riangle)"
    r"|f(?:rac|orall)"
    r"|b(?:eta|ar|inom|oldsymbol|egin|ig|ot|mod)"
    r"|r(?:ho|ight(?:arrow)?|angle|ceil|floor|m)"
    r"|n(?:u|eq|abla|eg|ot|otin|ewline)"
    r")(?![A-Za-z])"
)


def _escape_latex(text: str) -> str:
    return _LATEX_AT_RISK.sub(r"\\\\\1", text)


class OpenAICompatLLM(LLMProvider):
    name = "openai-compat"
    label = "OpenAI-compatible"
    default_base_url = "https://api.openai.com/v1"
    structured_mode: StructuredMode = "none"
    _context_window = 128_000
    _supports_vision = False
    #: Some gateways reject an explicit temperature on reasoning models.
    send_temperature = True

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(extra_headers or {})
        # Local patch: a thinking model on a shared local GPU can take over 5 minutes per call.
        long_timeout = bool(os.environ.get("QUICKLEARN_LLM_LONG_TIMEOUT"))
        self._client = build_client(
            base_url=self.base_url,
            headers=headers,
            timeout=LONG_TIMEOUT if long_timeout else DEFAULT_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def supports_structured_output(self) -> bool:
        return self.structured_mode != "none"

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision

    # --- request building ------------------------------------------------------------
    def _response_format(self, json_schema: dict[str, Any] | None) -> dict[str, Any] | None:
        if json_schema is None or self.structured_mode == "none":
            return None
        if self.structured_mode == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": json_schema.get("title", "response"),
                "schema": json_schema,
                "strict": False,
            },
        }

    def _payload(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": m["role"], "content": _convert_content(m["content"])} for m in messages
            ],
            # Local patch: reasoning models spend output tokens thinking before the answer,
            # so give every call headroom on top of the caller's answer budget.
            "max_tokens": max_tokens + int(os.environ.get("QUICKLEARN_REASONING_HEADROOM", "0")),
        }
        # Local patch: let the server apply the model's recommended sampling instead; low
        # temperatures make reasoning models think much longer.
        if self.send_temperature and not os.environ.get("QUICKLEARN_USE_MODEL_SAMPLING"):
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        response_format = self._response_format(json_schema)
        if response_format:
            payload["response_format"] = response_format
        return payload

    # --- interface ------------------------------------------------------------------
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
            "/chat/completions",
            provider=self.name,
            label=self.label,
            json=self._payload(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema,
            ),
        )
        return self._parse_completion(data, model)

    def _parse_completion(self, data: dict[str, Any], model: str) -> LLMResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError(
                f"{self.label} returned no completion choices.", provider=self.name
            )
        message = choices[0].get("message") or {}
        text = message.get("content")
        if text is None:
            # Some gateways put an empty content with a refusal field instead.
            text = message.get("refusal") or ""
        usage = data.get("usage") or {}
        return LLMResponse(
            text=str(text),
            model=str(data.get("model") or model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=str(choices[0].get("finish_reason") or "stop"),
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
        payload = self._payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream_sse(
            self._client,
            "POST",
            "/chat/completions",
            provider=self.name,
            label=self.label,
            json=payload,
        ):
            for choice in chunk.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content")
                if delta:
                    yield str(delta)

    async def list_models(self) -> list[ModelInfo]:
        data = await request_json(
            self._client, "GET", "/models", provider=self.name, label=self.label
        )
        items = data.get("data")
        if not isinstance(items, list):
            return []
        models = []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=str(model_id),
                    name=str(item.get("name") or model_id),
                    context_window=int(item.get("context_length") or 0),
                )
            )
        return sorted(models, key=lambda m: m.id)

    @staticmethod
    def parse_json_text(text: str) -> Any:
        """Tolerant JSON parse for providers without schema enforcement.

        Handles the two things weak models reliably do wrong: wrapping JSON in a markdown fence,
        and adding a sentence of preamble. This is *not* regex-parsing model output into a data
        structure — the result still goes through Pydantic validation. It only strips packaging.
        """
        cleaned = _escape_latex(text.strip())
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Fall back to the outermost brace/bracket pair.
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = cleaned.find(opener), cleaned.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise ProviderResponseError("Model did not return parseable JSON.")
