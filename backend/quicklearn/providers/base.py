"""The four provider interfaces.

PRD §4: "This is the most important abstraction in the codebase. Everything else is a consumer
of it." Nothing above this layer may reference a vendor by name — the pipeline asks for the
``standard`` model, not for GPT-4o.

Every provider also implements :meth:`Provider.test_connection` and
:meth:`Provider.list_models`, because PRD §4.3 requires failing loudly and specifically at
*config* time rather than halfway through a 90-minute transcription.
"""

from __future__ import annotations

import abc
import dataclasses
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict

Role = Literal["system", "user", "assistant"]


class TextPart(TypedDict):
    type: Literal["text"]
    text: str


class ImagePart(TypedDict):
    """Base64 image, for the VLM path that reads images and scanned PDFs."""

    type: Literal["image"]
    media_type: str
    data: str


ContentPart = TextPart | ImagePart


class Message(TypedDict):
    role: Role
    #: Plain text, or a list of parts when the message carries images.
    content: str | list[ContentPart]


def text_message(role: Role, text: str) -> Message:
    return {"role": role, "content": text}


@dataclasses.dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """Whether the model stopped because it hit the token limit.

        Worth checking explicitly: a truncated JSON response fails to parse, and the useful
        error is "raise max_tokens", not "the model returned invalid JSON".
        """
        return self.finish_reason in {"length", "max_tokens", "MAX_TOKENS"}


@dataclasses.dataclass(slots=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    speaker: str | None = None

    def shifted(self, offset_ms: int) -> TranscriptSegment:
        """Copy shifted in time, for reassembling chunks split off a long file."""
        return TranscriptSegment(
            start_ms=self.start_ms + offset_ms,
            end_ms=self.end_ms + offset_ms,
            text=self.text,
            speaker=self.speaker,
        )


@dataclasses.dataclass(slots=True)
class Transcript:
    text: str
    segments: list[TranscriptSegment] = dataclasses.field(default_factory=list)
    language: str | None = None
    duration_sec: float | None = None
    model: str = ""
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True)
class Voice:
    id: str
    name: str
    description: str = ""


@dataclasses.dataclass(slots=True)
class ModelInfo:
    id: str
    name: str = ""
    context_window: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.id


@dataclasses.dataclass(slots=True)
class ConnectionResult:
    ok: bool
    message: str
    models: list[ModelInfo] = dataclasses.field(default_factory=list)


class Provider(abc.ABC):
    """Shared surface: identity, credential validation, model discovery."""

    #: Registry key. Also the settings namespace, e.g. ``providers.groq.api_key``.
    name: str = ""
    #: Human-readable label for the Settings UI.
    label: str = ""
    #: Whether an API key is required. False for Ollama, LM Studio and the mock.
    requires_api_key: bool = True
    #: True when no request leaves the machine, so the UI can show $0.00 and "local".
    is_local: bool = False

    @abc.abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Models available with the current credentials."""

    async def test_connection(self) -> ConnectionResult:
        """Validate configuration and enumerate models.

        Deliberately catches provider errors and returns them as data: this powers a UI button
        whose entire job is reporting *why* a key doesn't work.
        """
        from ..errors import QuicklearnError

        try:
            models = await self.list_models()
        except QuicklearnError as exc:
            return ConnectionResult(ok=False, message=exc.message)
        except Exception as exc:
            return ConnectionResult(ok=False, message=f"{type(exc).__name__}: {exc}")

        if not models:
            return ConnectionResult(
                ok=True,
                message=f"Connected to {self.label}, but it reported no available models.",
                models=[],
            )
        return ConnectionResult(
            ok=True,
            message=f"Connected to {self.label} — {len(models)} model(s) available.",
            models=models,
        )

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not every provider holds resources
        """Release network resources. Overridden by HTTP-backed providers."""


class LLMProvider(Provider):
    kind = "llm"

    @abc.abstractmethod
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Single completion. When ``json_schema`` is given and the provider supports
        structured output, the response is constrained to it."""

    @abc.abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Used for chat and progressive note rendering."""

    @property
    def context_window(self) -> int:
        return 128_000

    @property
    def supports_structured_output(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False


class STTProvider(Provider):
    kind = "stt"

    @abc.abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        *,
        model: str = "",
        language: str | None = None,
        diarize: bool = False,
    ) -> Transcript: ...

    @property
    def max_file_bytes(self) -> int:
        """Upload ceiling. Files above this are split on silence and stitched (PRD §6.3)."""
        return 24 * 1024 * 1024

    @property
    def supports_diarization(self) -> bool:
        return False


class TTSProvider(Provider):
    kind = "tts"

    @abc.abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
    ) -> bytes:
        """Audio bytes for one line of dialogue."""

    @abc.abstractmethod
    async def available_voices(self) -> list[Voice]: ...

    @property
    def audio_format(self) -> str:
        """Container of the returned bytes, so ffmpeg knows how to read it."""
        return "mp3"


class EmbeddingProvider(Provider):
    kind = "embedding"

    @abc.abstractmethod
    async def embed(self, texts: Sequence[str], *, model: str = "") -> list[list[float]]: ...

    @property
    @abc.abstractmethod
    def dimensions(self) -> int:
        """Vector width. The sqlite-vec table is declared with this, and a change forces a
        reindex, so it must be reported accurately."""

    @property
    def max_batch(self) -> int:
        return 64
