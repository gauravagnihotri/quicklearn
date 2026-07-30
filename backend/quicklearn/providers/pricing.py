"""Bundled per-provider pricing, for cost estimates and the running total.

PRD §4.4 calls this a trust feature: "cost anxiety is the primary blocker to using BYO-key
products." A user who cannot see what a job will cost before running it will not run it.

**These figures are estimates.** Providers change prices without notice and this table is a
snapshot, so the UI labels totals as estimates rather than invoices. Matching is by longest
prefix, so ``gpt-4o-mini-2024-07-18`` resolves via ``gpt-4o-mini`` and a newly released point
version degrades to a sensible neighbour instead of reporting zero.

Units:
  * LLM  — USD per 1M tokens, ``(input, output)``
  * STT  — USD per audio minute
  * TTS  — USD per 1000 characters
  * Embedding — USD per 1M tokens
"""

from __future__ import annotations

import dataclasses
from typing import TypeVar

PRICES_LAST_REVIEWED = "2026-07"

LLM_PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "openai": {
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "o4-mini": (1.10, 4.40),
        "o3-mini": (1.10, 4.40),
        "o3": (2.00, 8.00),
        "gpt-3.5-turbo": (0.50, 1.50),
    },
    "anthropic": {
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-sonnet-4-5": (3.00, 15.00),
        "claude-sonnet-4": (3.00, 15.00),
        "claude-opus-4": (15.00, 75.00),
        "claude-3-5-haiku": (0.80, 4.00),
        "claude-3-5-sonnet": (3.00, 15.00),
        "claude-3-haiku": (0.25, 1.25),
        "claude-3-opus": (15.00, 75.00),
    },
    "google": {
        "gemini-2.5-pro": (1.25, 10.00),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.0-flash-lite": (0.075, 0.30),
        "gemini-2.0-flash": (0.10, 0.40),
        "gemini-1.5-flash-8b": (0.0375, 0.15),
        "gemini-1.5-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 5.00),
    },
    "groq": {
        "llama-3.1-8b-instant": (0.05, 0.08),
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "llama-3.3-70b": (0.59, 0.79),
        "llama3-70b": (0.59, 0.79),
        "llama3-8b": (0.05, 0.08),
        "mixtral-8x7b": (0.24, 0.24),
        "gemma2-9b": (0.20, 0.20),
        "qwen": (0.29, 0.39),
        "deepseek-r1-distill-llama-70b": (0.75, 0.99),
    },
    # OpenRouter proxies hundreds of models at pass-through prices; a bundled table would be
    # wrong more often than right, so cost is reported as unknown rather than guessed.
    "openrouter": {},
    "ollama": {},
    "lmstudio": {},
    "mock": {},
}

STT_PRICES_PER_MINUTE: dict[str, dict[str, float]] = {
    "groq": {
        "whisper-large-v3-turbo": 0.00067,  # ~$0.04/hour
        "whisper-large-v3": 0.00185,  # ~$0.111/hour
        "distil-whisper": 0.00033,
    },
    "openai": {
        "whisper-1": 0.006,
        "gpt-4o-mini-transcribe": 0.003,
        "gpt-4o-transcribe": 0.006,
    },
    "deepgram": {
        "nova-3": 0.0043,
        "nova-2": 0.0043,
        "nova": 0.0043,
        "enhanced": 0.0145,
        "base": 0.0125,
    },
    "mock": {},
}

TTS_PRICES_PER_1K_CHARS: dict[str, dict[str, float]] = {
    "openai": {
        "gpt-4o-mini-tts": 0.012,
        "tts-1-hd": 0.030,
        "tts-1": 0.015,
    },
    # ElevenLabs bills in credits against a monthly plan rather than per character, so this is
    # a rough mid-tier equivalent. Treated as an estimate like everything else here.
    "elevenlabs": {"": 0.18},
    "mock": {},
}

EMBEDDING_PRICES_PER_1M: dict[str, dict[str, float]] = {
    "openai": {
        "text-embedding-3-small": 0.02,
        "text-embedding-3-large": 0.13,
        "text-embedding-ada-002": 0.10,
    },
    "ollama": {},
    "fastembed": {},
    "mock": {},
}

#: Providers that never make a billable network call.
FREE_PROVIDERS = frozenset({"ollama", "lmstudio", "fastembed", "mock"})


_T = TypeVar("_T")


def _match_prefix(table: dict[str, _T], model: str) -> _T | None:
    """Longest-prefix match, so specific entries win over general ones."""
    model = (model or "").lower()
    best: tuple[int, _T] | None = None
    for prefix, value in table.items():
        if prefix == "":
            if best is None:
                best = (0, value)
            continue
        if model.startswith(prefix.lower()) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), value)
    return None if best is None else best[1]


@dataclasses.dataclass(slots=True)
class CostEstimate:
    usd: float
    #: False when no price is known — the UI shows "unknown", never a misleading $0.00.
    known: bool = True

    def __add__(self, other: CostEstimate) -> CostEstimate:
        return CostEstimate(self.usd + other.usd, self.known and other.known)


ZERO = CostEstimate(0.0, True)


def llm_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> CostEstimate:
    if provider in FREE_PROVIDERS:
        return ZERO
    prices = _match_prefix(LLM_PRICES.get(provider, {}), model)
    if prices is None:
        return CostEstimate(0.0, known=False)
    in_rate, out_rate = prices
    usd = (prompt_tokens / 1_000_000) * in_rate + (completion_tokens / 1_000_000) * out_rate
    return CostEstimate(usd)


def stt_cost(provider: str, model: str, audio_seconds: float) -> CostEstimate:
    if provider in FREE_PROVIDERS:
        return ZERO
    rate = _match_prefix(STT_PRICES_PER_MINUTE.get(provider, {}), model)
    if rate is None:
        return CostEstimate(0.0, known=False)
    return CostEstimate((audio_seconds / 60.0) * rate)


def tts_cost(provider: str, model: str, characters: int) -> CostEstimate:
    if provider in FREE_PROVIDERS:
        return ZERO
    rate = _match_prefix(TTS_PRICES_PER_1K_CHARS.get(provider, {}), model)
    if rate is None:
        return CostEstimate(0.0, known=False)
    return CostEstimate((characters / 1000.0) * rate)


def embedding_cost(provider: str, model: str, tokens: int) -> CostEstimate:
    if provider in FREE_PROVIDERS:
        return ZERO
    rate = _match_prefix(EMBEDDING_PRICES_PER_1M.get(provider, {}), model)
    if rate is None:
        return CostEstimate(0.0, known=False)
    return CostEstimate((tokens / 1_000_000) * rate)


def known_models(provider: str) -> list[str]:
    """Models with a bundled price. Used to annotate the model picker."""
    return sorted(LLM_PRICES.get(provider, {}))
