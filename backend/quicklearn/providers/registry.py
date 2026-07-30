"""Provider resolution and per-task model routing.

Two responsibilities:

* **Catalogue** — what exists, what each needs, what the Settings UI should render.
* **Resolution** — turn ``routing.standard`` into a live provider object plus a model name.

PRD §4.3 routes three task classes independently because their cost/quality profiles differ by
orders of magnitude: relevance-classifying 200 segments with a frontier model costs more than
the rest of the pipeline combined and is no better at the job.

Instances are cached because each owns an HTTP connection pool. :func:`reset` is called
whenever settings change, so a newly pasted API key takes effect immediately.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..db.enums import ProviderKind, TaskClass
from ..errors import ConfigurationError
from ..settings import get_settings
from .base import EmbeddingProvider, LLMProvider, Provider, STTProvider, TTSProvider
from .embedding.fastembed import FastEmbedProvider
from .embedding.mock import MockEmbedding
from .embedding.ollama import OllamaEmbedding
from .embedding.openai import OpenAIEmbedding
from .llm.anthropic import AnthropicLLM
from .llm.google import GoogleLLM
from .llm.groq import GroqLLM
from .llm.lmstudio import LMStudioLLM
from .llm.mock import MockLLM
from .llm.ollama import OllamaLLM
from .llm.openai import OpenAILLM
from .llm.openrouter import OpenRouterLLM
from .stt.deepgram import DeepgramSTT
from .stt.groq import GroqSTT
from .stt.mock import MockSTT
from .stt.openai import OpenAISTT
from .tts.elevenlabs import ElevenLabsTTS
from .tts.mock import MockTTS
from .tts.openai import OpenAITTS

log = logging.getLogger(__name__)

LLM_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAILLM,
    "anthropic": AnthropicLLM,
    "google": GoogleLLM,
    "groq": GroqLLM,
    "openrouter": OpenRouterLLM,
    "ollama": OllamaLLM,
    "lmstudio": LMStudioLLM,
    "mock": MockLLM,
}

STT_PROVIDERS: dict[str, type[STTProvider]] = {
    "groq": GroqSTT,
    "openai": OpenAISTT,
    "deepgram": DeepgramSTT,
    "mock": MockSTT,
}

TTS_PROVIDERS: dict[str, type[TTSProvider]] = {
    "openai": OpenAITTS,
    "elevenlabs": ElevenLabsTTS,
    "mock": MockTTS,
}

EMBEDDING_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "fastembed": FastEmbedProvider,
    "openai": OpenAIEmbedding,
    "ollama": OllamaEmbedding,
    "mock": MockEmbedding,
}

_BY_KIND: dict[ProviderKind, dict[str, type[Provider]]] = {
    ProviderKind.LLM: dict(LLM_PROVIDERS),  # type: ignore[arg-type]
    ProviderKind.STT: dict(STT_PROVIDERS),  # type: ignore[arg-type]
    ProviderKind.TTS: dict(TTS_PROVIDERS),  # type: ignore[arg-type]
    ProviderKind.EMBEDDING: dict(EMBEDDING_PROVIDERS),  # type: ignore[arg-type]
}

_cache: dict[tuple[str, str, str], Provider] = {}

#: Shutdown close-tasks are held here until they finish. asyncio keeps only a weak reference to
#: a running task, so without a strong one the garbage collector can cancel a close mid-flight.
_closing: set[asyncio.Task[None]] = set()


@dataclass(slots=True)
class Resolved:
    """A provider paired with the model the caller should use."""

    provider: LLMProvider
    model: str
    provider_name: str
    task_class: TaskClass


def _build(kind: ProviderKind, name: str, *, model: str = "") -> Provider:
    catalogue = _BY_KIND[kind]
    cls = catalogue.get(name)
    if cls is None:
        raise ConfigurationError(
            f"Unknown {kind.value} provider '{name}'. Available: {', '.join(sorted(catalogue))}."
        )

    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if getattr(cls, "requires_api_key", True):
        key = settings.api_key(name)
        if not key:
            raise ConfigurationError(
                f"No API key configured for {cls.label or name}. Add one in Settings → Providers."
            )
        kwargs["api_key"] = key
    else:
        key = settings.api_key(name)
        if key:
            kwargs["api_key"] = key

    base_url = settings.base_url(name)
    if base_url:
        kwargs["base_url"] = base_url
    if kind is ProviderKind.EMBEDDING and model:
        kwargs["model"] = model

    try:
        return cls(**kwargs)  # type: ignore[call-arg]
    except TypeError:
        # Providers that take no keyword arguments (the mocks).
        return cls()  # type: ignore[call-arg]


def get_provider(kind: ProviderKind, name: str, *, model: str = "") -> Provider:
    cache_key = (kind.value, name, model)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    instance = _build(kind, name, model=model)
    _cache[cache_key] = instance
    return instance


def get_llm(task_class: TaskClass | str = TaskClass.STANDARD) -> Resolved:
    """Resolve the provider and model for a task class."""
    task = TaskClass(task_class)
    settings = get_settings()
    provider_name, model = settings.routing(task.value)

    if not provider_name:
        raise ConfigurationError(
            "No language model is configured yet. Open Settings → Providers, add an API key, "
            "then pick models under Model routing. If you just want to look around, choose the "
            "'Mock (offline demo)' provider."
        )
    if not model:
        raise ConfigurationError(
            f"No model chosen for the '{task.value}' task class. "
            f"Pick one in Settings → Model routing."
        )

    provider = get_provider(ProviderKind.LLM, provider_name)
    assert isinstance(provider, LLMProvider)
    return Resolved(provider=provider, model=model, provider_name=provider_name, task_class=task)


def get_stt() -> tuple[STTProvider, str]:
    settings = get_settings()
    name = settings.get_str("stt.provider")
    if not name:
        raise ConfigurationError(
            "No transcription provider is configured. Add one in Settings → Providers — "
            "Groq is the cheapest option and has a free tier."
        )
    provider = get_provider(ProviderKind.STT, name)
    assert isinstance(provider, STTProvider)
    model = settings.get_str("stt.model") or getattr(provider, "default_model", "")
    return provider, model


def get_tts() -> tuple[TTSProvider, str]:
    settings = get_settings()
    name = settings.get_str("tts.provider")
    if not name:
        raise ConfigurationError(
            "No text-to-speech provider is configured. Add one in Settings → Providers to "
            "generate podcasts."
        )
    provider = get_provider(ProviderKind.TTS, name)
    assert isinstance(provider, TTSProvider)
    return provider, settings.get_str("tts.model") or getattr(provider, "default_model", "")


def get_embedding() -> tuple[EmbeddingProvider, str]:
    settings = get_settings()
    name = settings.get_str("embedding.provider", "fastembed") or "fastembed"
    model = settings.get_str("embedding.model")
    provider = get_provider(ProviderKind.EMBEDDING, name, model=model)
    assert isinstance(provider, EmbeddingProvider)
    return provider, model


def has_llm() -> bool:
    try:
        get_llm(TaskClass.STANDARD)
        return True
    except ConfigurationError:
        return False


def has_stt() -> bool:
    try:
        get_stt()
        return True
    except ConfigurationError:
        return False


def has_tts() -> bool:
    try:
        get_tts()
        return True
    except ConfigurationError:
        return False


def catalogue() -> list[dict[str, Any]]:
    """Everything the Settings UI needs to render the provider list."""
    settings = get_settings()
    seen: dict[str, dict[str, Any]] = {}

    for kind, providers in _BY_KIND.items():
        for name, cls in providers.items():
            entry = seen.setdefault(
                name,
                {
                    "name": name,
                    "label": getattr(cls, "label", name),
                    "kinds": [],
                    "requires_api_key": getattr(cls, "requires_api_key", True),
                    "is_local": getattr(cls, "is_local", False),
                    "default_base_url": getattr(cls, "default_base_url", ""),
                    "has_key": bool(settings.api_key(name)),
                    "base_url": settings.base_url(name),
                },
            )
            entry["kinds"].append(kind.value)

    for entry in seen.values():
        entry["kinds"] = sorted(set(entry["kinds"]))
        entry["configured"] = (
            entry["has_key"] or not entry["requires_api_key"] or bool(entry["base_url"])
        )
    return sorted(seen.values(), key=lambda e: (not e["configured"], e["name"]))


def build_for_test(kind: ProviderKind, name: str, api_key: str, base_url: str = "") -> Provider:
    """Construct a provider from credentials that have not been saved yet.

    The Settings UI tests a key *before* storing it, so this deliberately bypasses the settings
    store rather than requiring the user to save something that might be wrong.
    """
    catalogue_for_kind = _BY_KIND[kind]
    cls = catalogue_for_kind.get(name)
    if cls is None:
        raise ConfigurationError(f"Unknown {kind.value} provider '{name}'.")

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    try:
        return cls(**kwargs)  # type: ignore[call-arg]
    except TypeError:
        return cls()  # type: ignore[call-arg]


def reset() -> None:
    """Drop cached instances so changed settings take effect at once.

    HTTP clients are closed on a best-effort basis; a leaked connection pool during a settings
    save is not worth blocking the request for.
    """
    instances = list(_cache.values())
    _cache.clear()
    if not instances:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for instance in instances:
        task = loop.create_task(_safe_close(instance))
        _closing.add(task)
        task.add_done_callback(_closing.discard)


async def _safe_close(provider: Provider) -> None:
    try:
        await provider.aclose()
    except Exception:  # pragma: no cover - shutdown noise is not actionable
        log.debug("error closing provider %s", getattr(provider, "name", "?"), exc_info=True)


async def close_all() -> None:
    instances = list(_cache.values())
    _cache.clear()
    for instance in instances:
        await _safe_close(instance)
