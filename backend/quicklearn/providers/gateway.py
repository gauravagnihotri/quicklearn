"""The single entry point pipeline code uses to reach a provider.

Everything above this module asks for *work*, never for a vendor. Centralising the calls here is
what makes three of PRD §11's engineering standards enforceable rather than aspirational:

* **Structured output** — schema-enforced where the provider supports it, Pydantic-validated
  with exactly one retry everywhere else. Never regex-parsed.
* **Every LLM call is logged** to ``llm_calls`` with prompt hash, tokens, latency and cost.
* **Actionable provider errors**, translated once in :mod:`quicklearn.providers.http`.

If a call bypasses this module it is invisible to the cost dashboard, which is a silent way for
the trust feature in §4.4 to become a lie.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..db.enums import TaskClass
from ..db.models import LlmCall
from ..db.session import session_scope
from ..errors import ProviderResponseError
from . import pricing, registry
from .base import Message, Transcript, text_message
from .llm.openai_compat import OpenAICompatLLM

log = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

#: Characters per token. Rough, but only used for pre-flight estimates, where being within
#: ~20% is the difference between a useful warning and no warning at all.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count.

    Uses tiktoken when available for accuracy, falling back to a character ratio. The fallback
    exists because tiktoken downloads its encoding files on first use, and a cost *estimate*
    must never be the thing that requires network access.
    """
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // CHARS_PER_TOKEN)


def _prompt_hash(messages: Sequence[Message]) -> str:
    payload = json.dumps(
        [{"role": m["role"], "content": m["content"]} for m in messages],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def log_call(
    *,
    provider: str,
    model: str,
    purpose: str,
    task_class: str = "standard",
    prompt_hash: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    characters: int = 0,
    cost: pricing.CostEstimate | None = None,
    latency_ms: int = 0,
    source_id: int | None = None,
    job_id: int | None = None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """Record one provider call. Never raises — telemetry must not break the pipeline."""
    try:
        with session_scope() as session:
            session.add(
                LlmCall(
                    provider=provider,
                    model=model,
                    purpose=purpose,
                    task_class=task_class,
                    prompt_hash=prompt_hash,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    audio_seconds=audio_seconds,
                    characters=characters,
                    cost_usd=cost.usd if cost else 0.0,
                    latency_ms=latency_ms,
                    source_id=source_id,
                    job_id=job_id,
                    ok=ok,
                    error=error[:2000] if error else None,
                )
            )
    except Exception:  # pragma: no cover - logging must never be load-bearing
        log.debug("failed to record llm_call for %s/%s", provider, model, exc_info=True)


async def complete_text(
    purpose: str,
    messages: Sequence[Message],
    *,
    task_class: TaskClass | str = TaskClass.STANDARD,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    source_id: int | None = None,
    job_id: int | None = None,
) -> str:
    resolved = registry.get_llm(task_class)
    started = time.perf_counter()
    try:
        response = await resolved.provider.complete(
            messages,
            model=resolved.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log_call(
            provider=resolved.provider_name,
            model=resolved.model,
            purpose=purpose,
            task_class=resolved.task_class.value,
            prompt_hash=_prompt_hash(messages),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source_id=source_id,
            job_id=job_id,
            ok=False,
            error=str(exc),
        )
        raise

    log_call(
        provider=resolved.provider_name,
        model=resolved.model,
        purpose=purpose,
        task_class=resolved.task_class.value,
        prompt_hash=_prompt_hash(messages),
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        cost=pricing.llm_cost(
            resolved.provider_name,
            resolved.model,
            response.prompt_tokens,
            response.completion_tokens,
        ),
        latency_ms=int((time.perf_counter() - started) * 1000),
        source_id=source_id,
        job_id=job_id,
    )
    return response.text


async def complete_structured(
    purpose: str,
    messages: Sequence[Message],
    schema: type[TModel],
    *,
    task_class: TaskClass | str = TaskClass.STANDARD,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    source_id: int | None = None,
    job_id: int | None = None,
) -> TModel:
    """Structured completion validated against a Pydantic model.

    On a parse or validation failure the model gets exactly one more attempt, with the specific
    error fed back to it. One retry rather than a loop: a second failure means the prompt or the
    model is wrong, and retrying indefinitely just spends the user's money proving that.
    """
    resolved = registry.get_llm(task_class)
    json_schema = schema.model_json_schema()
    supports_native = resolved.provider.supports_structured_output

    attempts: list[Message] = list(messages)
    if not supports_native:
        # Without schema enforcement, the instruction has to live in the prompt.
        attempts.append(
            text_message(
                "user",
                "Respond with a single JSON object matching this schema exactly. "
                "Output only JSON — no markdown fence, no commentary.\n\n"
                + json.dumps(json_schema, indent=2),
            )
        )

    last_error = ""
    for attempt in range(2):
        started = time.perf_counter()
        try:
            response = await resolved.provider.complete(
                attempts,
                model=resolved.model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_schema=json_schema if supports_native else None,
            )
        except Exception as exc:
            log_call(
                provider=resolved.provider_name,
                model=resolved.model,
                purpose=purpose,
                task_class=resolved.task_class.value,
                prompt_hash=_prompt_hash(attempts),
                latency_ms=int((time.perf_counter() - started) * 1000),
                source_id=source_id,
                job_id=job_id,
                ok=False,
                error=str(exc),
            )
            raise

        log_call(
            provider=resolved.provider_name,
            model=resolved.model,
            purpose=purpose,
            task_class=resolved.task_class.value,
            prompt_hash=_prompt_hash(attempts),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            cost=pricing.llm_cost(
                resolved.provider_name,
                resolved.model,
                response.prompt_tokens,
                response.completion_tokens,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source_id=source_id,
            job_id=job_id,
        )

        if response.truncated:
            # Distinguish "ran out of room" from "produced nonsense" — the fixes differ, and
            # "invalid JSON" would send someone debugging the wrong thing.
            raise ProviderResponseError(
                f"{resolved.provider_name} hit the output token limit while generating "
                f"{purpose}. Try a shorter source section, or a model with more output room.",
                provider=resolved.provider_name,
            )

        try:
            payload = OpenAICompatLLM.parse_json_text(response.text)
            return schema.model_validate(payload)
        except (ProviderResponseError, PydanticValidationError, ValueError) as exc:
            last_error = str(exc)[:800]
            log.info(
                "structured output for %s failed validation on attempt %d: %s",
                purpose,
                attempt + 1,
                last_error,
            )
            if attempt == 0:
                attempts = [
                    *messages,
                    text_message("assistant", response.text[:4000]),
                    text_message(
                        "user",
                        "That response could not be parsed against the required schema.\n"
                        f"Error: {last_error}\n\n"
                        "Return only a corrected JSON object matching this schema. "
                        "No markdown fence, no commentary.\n\n" + json.dumps(json_schema, indent=2),
                    ),
                ]

    raise ProviderResponseError(
        f"{resolved.provider_name} could not produce valid {purpose} output after two "
        f"attempts. Last error: {last_error}. Try a more capable model for the "
        f"'{resolved.task_class.value}' task class in Settings → Model routing.",
        provider=resolved.provider_name,
    )


async def stream_text(
    purpose: str,
    messages: Sequence[Message],
    *,
    task_class: TaskClass | str = TaskClass.STANDARD,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    source_id: int | None = None,
) -> AsyncIterator[str]:
    resolved = registry.get_llm(task_class)
    started = time.perf_counter()
    collected: list[str] = []
    try:
        async for delta in resolved.provider.stream(
            messages,
            model=resolved.model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            collected.append(delta)
            yield delta
    finally:
        # Streaming responses rarely carry usage data, so tokens are estimated. Logged either
        # way — an unlogged call is an invisible cost.
        text = "".join(collected)
        prompt_tokens = estimate_tokens(
            " ".join(m["content"] if isinstance(m["content"], str) else "" for m in messages)
        )
        completion_tokens = estimate_tokens(text)
        log_call(
            provider=resolved.provider_name,
            model=resolved.model,
            purpose=purpose,
            task_class=resolved.task_class.value,
            prompt_hash=_prompt_hash(messages),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=pricing.llm_cost(
                resolved.provider_name, resolved.model, prompt_tokens, completion_tokens
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source_id=source_id,
        )


async def transcribe(
    audio_path: Path,
    *,
    language: str | None = None,
    diarize: bool = False,
    source_id: int | None = None,
    job_id: int | None = None,
) -> Transcript:
    provider, model = registry.get_stt()
    started = time.perf_counter()
    try:
        result = await provider.transcribe(
            audio_path, model=model, language=language, diarize=diarize
        )
    except Exception as exc:
        log_call(
            provider=provider.name,
            model=model,
            purpose="transcribe",
            latency_ms=int((time.perf_counter() - started) * 1000),
            source_id=source_id,
            job_id=job_id,
            ok=False,
            error=str(exc),
        )
        raise

    seconds = result.duration_sec or 0.0
    log_call(
        provider=provider.name,
        model=model,
        purpose="transcribe",
        audio_seconds=seconds,
        cost=pricing.stt_cost(provider.name, model, seconds),
        latency_ms=int((time.perf_counter() - started) * 1000),
        source_id=source_id,
        job_id=job_id,
    )
    return result


async def synthesize(
    text: str,
    *,
    voice: str = "",
    speed: float = 1.0,
    source_id: int | None = None,
    job_id: int | None = None,
) -> bytes:
    provider, model = registry.get_tts()
    started = time.perf_counter()
    try:
        audio = await provider.synthesize(text, voice=voice, model=model, speed=speed)
    except Exception as exc:
        log_call(
            provider=provider.name,
            model=model,
            purpose="tts",
            characters=len(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source_id=source_id,
            job_id=job_id,
            ok=False,
            error=str(exc),
        )
        raise

    log_call(
        provider=provider.name,
        model=model,
        purpose="tts",
        characters=len(text),
        cost=pricing.tts_cost(provider.name, model, len(text)),
        latency_ms=int((time.perf_counter() - started) * 1000),
        source_id=source_id,
        job_id=job_id,
    )
    return audio


async def embed(
    texts: Sequence[str],
    *,
    source_id: int | None = None,
    job_id: int | None = None,
) -> list[list[float]]:
    provider, model = registry.get_embedding()
    if not texts:
        return []
    started = time.perf_counter()
    vectors = await provider.embed(texts, model=model)
    tokens = sum(estimate_tokens(t) for t in texts)
    log_call(
        provider=provider.name,
        model=model or getattr(provider, "model_name", ""),
        purpose="embed",
        prompt_tokens=tokens,
        cost=pricing.embedding_cost(provider.name, model, tokens),
        latency_ms=int((time.perf_counter() - started) * 1000),
        source_id=source_id,
        job_id=job_id,
    )
    return vectors


def total_cost() -> dict[str, Any]:
    """Aggregate spend for the dashboard."""
    from sqlalchemy import func, select

    with session_scope() as session:
        total, calls = session.execute(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0), func.count(LlmCall.id))
        ).one()
        by_provider = session.execute(
            select(
                LlmCall.provider,
                func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
                func.count(LlmCall.id),
            ).group_by(LlmCall.provider)
        ).all()
        tokens_in, tokens_out = session.execute(
            select(
                func.coalesce(func.sum(LlmCall.prompt_tokens), 0),
                func.coalesce(func.sum(LlmCall.completion_tokens), 0),
            )
        ).one()

    return {
        "total_usd": round(float(total), 6),
        "calls": int(calls),
        "prompt_tokens": int(tokens_in),
        "completion_tokens": int(tokens_out),
        "by_provider": [
            {"provider": p, "usd": round(float(c), 6), "calls": int(n)} for p, c, n in by_provider
        ],
        "prices_last_reviewed": pricing.PRICES_LAST_REVIEWED,
    }
