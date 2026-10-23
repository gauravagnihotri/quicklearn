"""Provider implementations against recorded HTTP responses.

Each provider is exercised with an ``httpx.MockTransport`` standing in for the network, so the
suite runs offline and deterministically. This verifies wire-format handling and, just as
importantly, that failures become the *specific, actionable* errors PRD §11 asks for rather
than a generic 500.

What this does not prove is that the live services still speak these shapes. That needs real
keys, and is called out as unverified rather than implied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from quicklearn.errors import (
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from quicklearn.providers.base import text_message
from quicklearn.providers.embedding.mock import MockEmbedding
from quicklearn.providers.llm.anthropic import AnthropicLLM
from quicklearn.providers.llm.google import GoogleLLM, sanitise_schema
from quicklearn.providers.llm.groq import GroqLLM
from quicklearn.providers.llm.mock import MockLLM
from quicklearn.providers.llm.openai import OpenAILLM
from quicklearn.providers.stt.deepgram import DeepgramSTT
from quicklearn.providers.stt.groq import GroqSTT


def mount(provider: Any, handler: Any) -> Any:
    """Swap a provider's HTTP client for one backed by a mock transport."""
    provider._client = httpx.AsyncClient(
        base_url=provider.base_url,
        transport=httpx.MockTransport(handler),
        headers=dict(provider._client.headers),
    )
    return provider


def json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --------------------------------------------------------------------------- OpenAI-compatible

OPENAI_COMPLETION = {
    "id": "chatcmpl-1",
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Eigenvalues scale."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 42, "completion_tokens": 7},
}


async def test_openai_completion_is_parsed() -> None:
    provider = mount(OpenAILLM(api_key="sk-test"), lambda r: json_response(OPENAI_COMPLETION))
    response = await provider.complete(
        [text_message("user", "hi")], model="gpt-4o-mini", max_tokens=100
    )
    assert response.text == "Eigenvalues scale."
    assert response.prompt_tokens == 42
    assert response.completion_tokens == 7
    assert response.truncated is False


async def test_truncation_is_detected() -> None:
    payload = {
        **OPENAI_COMPLETION,
        "choices": [{**OPENAI_COMPLETION["choices"][0], "finish_reason": "length"}],
    }
    provider = mount(OpenAILLM(api_key="sk-test"), lambda r: json_response(payload))
    response = await provider.complete([text_message("user", "hi")], model="gpt-4o-mini")
    assert response.truncated is True


async def test_openai_sends_json_schema_when_asked() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response(OPENAI_COMPLETION)

    provider = mount(OpenAILLM(api_key="sk-test"), handler)
    await provider.complete(
        [text_message("user", "hi")],
        model="gpt-4o-mini",
        json_schema={"title": "Outline", "type": "object", "properties": {}},
    )
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["name"] == "Outline"


async def test_groq_uses_json_object_mode() -> None:
    """Groq honours `json_object` across its whole catalogue but `json_schema` only on some
    models, so the safer mode keeps every model in the picker usable."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response(OPENAI_COMPLETION)

    provider = mount(GroqLLM(api_key="gsk-test"), handler)
    await provider.complete(
        [text_message("user", "hi")],
        model="llama-3.3-70b-versatile",
        json_schema={"type": "object", "properties": {}},
    )
    assert seen["response_format"] == {"type": "json_object"}


async def test_streaming_yields_deltas() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"Eigen"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"values"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    provider = mount(
        OpenAILLM(api_key="sk-test"),
        lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}),
    )
    chunks = [c async for c in provider.stream([text_message("user", "hi")], model="gpt-4o-mini")]
    assert "".join(chunks) == "Eigenvalues"


async def test_model_listing_is_sorted() -> None:
    payload = {"data": [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]}
    provider = mount(OpenAILLM(api_key="sk-test"), lambda r: json_response(payload))
    models = await provider.list_models()
    assert [m.id for m in models] == ["gpt-3.5-turbo", "gpt-4o"]


# --------------------------------------------------------------------------- error translation


@pytest.mark.parametrize(
    ("status", "payload", "expected", "must_mention"),
    [
        (401, {"error": {"message": "Incorrect API key"}}, ProviderAuthError, "Settings"),
        (429, {"error": {"message": "Rate limit reached"}}, ProviderRateLimitError, "rate limit"),
        (500, {"error": {"message": "server go boom"}}, ProviderUnavailableError, "unavailable"),
        (503, {"error": {"message": "overloaded"}}, ProviderUnavailableError, "unavailable"),
        (402, {"error": {"message": "insufficient_quota"}}, ProviderQuotaError, "credit"),
    ],
)
async def test_http_errors_become_actionable(
    status: int, payload: dict[str, Any], expected: type[Exception], must_mention: str
) -> None:
    provider = mount(OpenAILLM(api_key="sk-test"), lambda r: json_response(payload, status))
    with pytest.raises(expected) as exc:
        await provider.complete([text_message("user", "hi")], model="gpt-4o-mini")

    message = str(exc.value)
    assert "OpenAI" in message, "the message must name the provider"
    assert must_mention.lower() in message.lower()


async def test_auth_errors_are_not_retryable() -> None:
    """A wrong key must not burn three attempts — it will not fix itself."""
    provider = mount(OpenAILLM(api_key="bad"), lambda r: json_response({"error": "nope"}, 401))
    with pytest.raises(ProviderAuthError) as exc:
        await provider.complete([text_message("user", "hi")], model="gpt-4o-mini")
    assert exc.value.retryable is False


async def test_rate_limit_is_retryable_and_keeps_retry_after() -> None:
    provider = mount(
        OpenAILLM(api_key="sk-test"),
        lambda r: httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "30"}),
    )
    with pytest.raises(ProviderRateLimitError) as exc:
        await provider.complete([text_message("user", "hi")], model="gpt-4o-mini")
    assert exc.value.retryable is True
    assert exc.value.retry_after == 30.0


async def test_connection_failure_is_translated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = mount(OpenAILLM(api_key="sk-test"), handler)
    with pytest.raises(ProviderUnavailableError) as exc:
        await provider.complete([text_message("user", "hi")], model="gpt-4o-mini")
    assert "Could not reach" in str(exc.value)


async def test_test_connection_reports_failure_as_data() -> None:
    """The Settings button needs a reason, not an exception."""
    provider = mount(
        OpenAILLM(api_key="bad"), lambda r: json_response({"error": "Incorrect API key"}, 401)
    )
    result = await provider.test_connection()
    assert result.ok is False
    assert "Settings" in result.message


async def test_test_connection_reports_success_with_models() -> None:
    provider = mount(
        OpenAILLM(api_key="sk-test"), lambda r: json_response({"data": [{"id": "gpt-4o"}]})
    )
    result = await provider.test_connection()
    assert result.ok is True
    assert [m.id for m in result.models] == ["gpt-4o"]


# --------------------------------------------------------------------------- Anthropic


async def test_anthropic_hoists_system_and_parses_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response(
            {
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "Hello."}],
                "usage": {"input_tokens": 10, "output_tokens": 3},
                "stop_reason": "end_turn",
            }
        )

    provider = mount(AnthropicLLM(api_key="sk-ant"), handler)
    response = await provider.complete(
        [text_message("system", "Be terse."), text_message("user", "Hi")],
        model="claude-sonnet-4-5",
    )
    assert seen["system"] == "Be terse."
    assert [m["role"] for m in seen["messages"]] == ["user"]
    assert response.text == "Hello."
    assert response.prompt_tokens == 10


async def test_anthropic_structured_output_uses_forced_tool_use() -> None:
    """Schema conformance by construction beats asking politely for JSON."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response(
            {
                "model": "claude-sonnet-4-5",
                "content": [
                    {"type": "tool_use", "name": "emit_result", "input": {"sections": ["a"]}}
                ],
                "usage": {"input_tokens": 5, "output_tokens": 5},
            }
        )

    provider = mount(AnthropicLLM(api_key="sk-ant"), handler)
    response = await provider.complete(
        [text_message("user", "outline this")],
        model="claude-sonnet-4-5",
        json_schema={"type": "object", "properties": {"sections": {"type": "array"}}},
    )
    assert seen["tool_choice"] == {"type": "tool", "name": "emit_result"}
    assert json.loads(response.text) == {"sections": ["a"]}


async def test_anthropic_falls_back_to_a_static_model_list() -> None:
    """A key can be valid even when the models endpoint isn't reachable; an empty picker
    would wrongly look like a broken key."""
    provider = mount(AnthropicLLM(api_key="sk-ant"), lambda r: json_response({}, 404))
    models = await provider.list_models()
    assert models, "should degrade to the bundled list rather than returning nothing"


# --------------------------------------------------------------------------- Google


def test_gemini_schema_sanitiser_inlines_refs_and_drops_unsupported_keys() -> None:
    """Pydantic emits $defs/$ref/additionalProperties for any nested model; Gemini rejects
    all three, so an unsanitised schema 400s on exactly the nested outputs notes depend on."""
    schema = {
        "type": "object",
        "title": "Outline",
        "additionalProperties": False,
        "properties": {
            "sections": {"type": "array", "items": {"$ref": "#/$defs/Section"}},
            "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["sections"],
        "$defs": {
            "Section": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"title": {"type": "string", "default": "x"}},
                "required": ["title"],
            }
        },
    }
    cleaned = sanitise_schema(schema)
    rendered = json.dumps(cleaned)

    assert "$ref" not in rendered
    assert "$defs" not in rendered
    assert "additionalProperties" not in rendered
    assert "anyOf" not in rendered
    assert cleaned["properties"]["sections"]["items"]["properties"]["title"]["type"] == "string"
    assert cleaned["properties"]["note"] == {"type": "string", "nullable": True}
    assert cleaned["required"] == ["sections"]


async def test_google_maps_roles_and_extracts_text() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return json_response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Answer."}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 2},
            }
        )

    provider = mount(GoogleLLM(api_key="AIza"), handler)
    response = await provider.complete(
        [
            text_message("system", "Be brief."),
            text_message("assistant", "ok"),
            text_message("user", "Go"),
        ],
        model="gemini-2.0-flash",
    )
    assert seen["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert [c["role"] for c in seen["contents"]] == ["model", "user"]
    assert response.text == "Answer."


async def test_google_api_key_is_sent_as_a_header_not_a_query_param() -> None:
    """Keys in URLs end up in logs and proxy history."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["header"] = request.headers.get("x-goog-api-key")
        return json_response({"models": []})

    provider = mount(GoogleLLM(api_key="AIza-secret"), handler)
    await provider.list_models()
    assert captured["header"] == "AIza-secret"
    assert "AIza-secret" not in captured["url"]


async def test_google_safety_block_is_explained() -> None:
    provider = mount(
        GoogleLLM(api_key="AIza"),
        lambda r: json_response({"promptFeedback": {"blockReason": "SAFETY"}}),
    )
    with pytest.raises(ProviderResponseError) as exc:
        await provider.complete([text_message("user", "x")], model="gemini-2.0-flash")
    assert "SAFETY" in str(exc.value)


# --------------------------------------------------------------------------- STT


async def test_groq_stt_parses_verbose_json_segments(wav_file: Path) -> None:
    payload = {
        "text": "Hello there. Second part.",
        "language": "en",
        "duration": 12.5,
        "segments": [
            {"start": 0.0, "end": 4.25, "text": " Hello there."},
            {"start": 4.25, "end": 12.5, "text": " Second part."},
        ],
    }
    provider = mount(GroqSTT(api_key="gsk"), lambda r: json_response(payload))
    result = await provider.transcribe(wav_file)

    assert result.duration_sec == 12.5
    assert result.language == "en"
    assert len(result.segments) == 2
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 4250
    assert result.segments[1].text == "Second part."


async def test_stt_wraps_plain_text_when_segments_are_missing(wav_file: Path) -> None:
    """Losing the transcription entirely is worse than losing the timestamps."""
    provider = mount(GroqSTT(api_key="gsk"), lambda r: json_response({"text": "No segments here."}))
    result = await provider.transcribe(wav_file)
    assert len(result.segments) == 1
    assert result.segments[0].text == "No segments here."


async def test_deepgram_parses_utterances_with_speakers(wav_file: Path) -> None:
    payload = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "A. B."}]}],
            "utterances": [
                {"start": 0.0, "end": 2.0, "transcript": "A.", "speaker": 0},
                {"start": 2.0, "end": 4.0, "transcript": "B.", "speaker": 1},
            ],
        },
        "metadata": {"duration": 4.0},
    }
    provider = mount(DeepgramSTT(api_key="dg"), lambda r: json_response(payload))
    result = await provider.transcribe(wav_file, diarize=True)

    assert [s.speaker for s in result.segments] == ["Speaker 0", "Speaker 1"]
    assert result.duration_sec == 4.0


async def test_deepgram_uses_token_auth_scheme(wav_file: Path) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return json_response({"results": {"channels": [{"alternatives": [{"transcript": "x"}]}]}})

    provider = mount(DeepgramSTT(api_key="dg-key"), handler)
    await provider.transcribe(wav_file)
    assert captured["auth"] == "Token dg-key", "Deepgram uses Token, not Bearer"


# --------------------------------------------------------------------------- offline providers


async def test_mock_llm_fills_any_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "front": {"type": "string"},
                        "back": {"type": "string"},
                        "type": {"type": "string", "enum": ["basic", "cloze"]},
                    },
                    "required": ["front", "back", "type"],
                },
            }
        },
        "required": ["cards"],
    }
    provider = MockLLM()
    response = await provider.complete(
        [text_message("user", "Eigenvalues scale eigenvectors by a scalar factor.")],
        model="mock-standard",
        json_schema=schema,
    )
    payload = json.loads(response.text)
    assert len(payload["cards"]) >= 3
    for card in payload["cards"]:
        assert card["type"] in {"basic", "cloze"}
        assert isinstance(card["front"], str) and card["front"]


async def test_mock_llm_is_deterministic() -> None:
    provider = MockLLM()
    messages = [text_message("user", "Same input every time.")]
    first = await provider.complete(messages, model="mock-standard")
    second = await provider.complete(messages, model="mock-standard")
    assert first.text == second.text


async def test_mock_embeddings_are_normalised_and_semantically_ordered() -> None:
    """Retrieval tests need embeddings that actually rank related text higher, or they assert
    nothing beyond 'a list came back'."""
    provider = MockEmbedding()
    vectors = await provider.embed(
        [
            "eigenvalues and eigenvectors of a matrix",
            "eigenvalue decomposition of matrices",
            "the cafeteria menu changes on Tuesday",
        ]
    )
    assert all(len(v) == provider.dimensions for v in vectors)
    for vector in vectors:
        assert abs(sum(x * x for x in vector) - 1.0) < 1e-6, "should be unit length"

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    related = cosine(vectors[0], vectors[1])
    unrelated = cosine(vectors[0], vectors[2])
    assert related > unrelated


async def test_mock_stt_timestamps_span_the_real_audio(wav_file: Path) -> None:
    from quicklearn.providers.stt.mock import MockSTT

    result = await MockSTT().transcribe(wav_file)
    assert result.segments
    assert result.segments[0].start_ms == 0
    # The fixture is a 3-second tone; segments must not run past it or the player desyncs.
    assert result.segments[-1].end_ms <= 3_500


def test_parse_json_keeps_latex_commands_the_model_left_unescaped() -> None:
    """Local patch: `\\times` written raw in JSON would decode as a tab plus "imes"."""
    from quicklearn.providers.llm.openai_compat import OpenAICompatLLM

    raw = (
        r'{"text": "$3 \times 2$, $\frac{a}{b}$, $\beta$, $\rho$, $x \neq y$, $\to$, '
        r'$O(\text{log } n)$, $\nabla f$; kept: $\\theta$, line\nnext, a\tb"}'
    )
    assert OpenAICompatLLM.parse_json_text(raw)["text"] == (
        "$3 \\times 2$, $\\frac{a}{b}$, $\\beta$, $\\rho$, $x \\neq y$, $\\to$, "
        "$O(\\text{log } n)$, $\\nabla f$; kept: $\\theta$, line\nnext, a\tb"
    )


def test_parse_json_leaves_real_newlines_before_words() -> None:
    from quicklearn.providers.llm.openai_compat import OpenAICompatLLM

    raw = r'{"text": "one\ne.g. two\ni.e. three\nUse four\tab\noted"}'
    assert OpenAICompatLLM.parse_json_text(raw)["text"] == (
        "one\ne.g. two\ni.e. three\nUse four\tab\noted"
    )


class _ScriptedLLM:
    """Returns canned responses in order, for exercising the gateway's retry rules."""

    supports_structured_output = True

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.responses.pop(0)


def _route_to(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    from quicklearn.db.enums import TaskClass
    from quicklearn.providers import gateway, registry

    monkeypatch.setattr(
        gateway.registry,
        "get_llm",
        lambda task_class=None: registry.Resolved(provider, "m", "fake", TaskClass.STANDARD),
    )


async def test_structured_output_retries_once_after_hitting_the_token_limit(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import BaseModel

    from quicklearn.providers import gateway
    from quicklearn.providers.base import LLMResponse

    class Answer(BaseModel):
        text: str

    padded = LLMResponse(text='{"text": "the "' + " " * 50, model="m", finish_reason="length")
    good = LLMResponse(text='{"text": "the fixed-size problem"}', model="m")
    provider = _ScriptedLLM(padded, good)
    _route_to(monkeypatch, provider)

    result = await gateway.complete_structured("t", [text_message("user", "x")], Answer)
    assert result.text == "the fixed-size problem"
    assert provider.calls == 2


async def test_structured_output_gives_up_after_two_token_limit_hits(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import BaseModel

    from quicklearn.providers import gateway
    from quicklearn.providers.base import LLMResponse

    class Answer(BaseModel):
        text: str

    padded = LLMResponse(text='{"text": "the "', model="m", finish_reason="length")
    provider = _ScriptedLLM(padded, padded)
    _route_to(monkeypatch, provider)

    with pytest.raises(ProviderResponseError, match="output token limit"):
        await gateway.complete_structured("t", [text_message("user", "x")], Answer)
    assert provider.calls == 2


def test_lmstudio_context_window_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from quicklearn.providers.llm.lmstudio import LMStudioLLM

    provider = LMStudioLLM(api_key="", base_url="http://localhost:1234/v1")
    assert provider.context_window == 32_768
    monkeypatch.setenv("QUICKLEARN_LMSTUDIO_CONTEXT_WINDOW", "131072")
    assert provider.context_window == 131_072
