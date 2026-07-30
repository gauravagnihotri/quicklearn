"""The provider layer — the abstraction everything else consumes.

Nothing above this package should import a vendor module directly. Ask
:mod:`quicklearn.providers.registry` for a task class, or call
:mod:`quicklearn.providers.gateway` to do the work.
"""

from .base import (
    ConnectionResult,
    EmbeddingProvider,
    LLMProvider,
    LLMResponse,
    Message,
    ModelInfo,
    Provider,
    STTProvider,
    Transcript,
    TranscriptSegment,
    TTSProvider,
    Voice,
    text_message,
)

__all__ = [
    "ConnectionResult",
    "EmbeddingProvider",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "ModelInfo",
    "Provider",
    "STTProvider",
    "TTSProvider",
    "Transcript",
    "TranscriptSegment",
    "Voice",
    "text_message",
]
