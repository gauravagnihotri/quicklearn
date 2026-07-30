"""Shared base for Whisper-style ``/audio/transcriptions`` endpoints (OpenAI, Groq)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ...errors import ProviderResponseError
from ..base import ModelInfo, STTProvider, Transcript, TranscriptSegment
from ..http import LONG_TIMEOUT, build_client, request_json

log = logging.getLogger(__name__)

_MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


class WhisperCompatSTT(STTProvider):
    name = "whisper-compat"
    label = "Whisper-compatible"
    default_base_url = "https://api.openai.com/v1"
    default_model = "whisper-1"
    available_model_ids: tuple[str, ...] = ("whisper-1",)
    _max_file_bytes = 24 * 1024 * 1024

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=LONG_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def max_file_bytes(self) -> int:
        return self._max_file_bytes

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m) for m in self.available_model_ids]

    async def transcribe(
        self,
        audio_path: Path,
        *,
        model: str = "",
        language: str | None = None,
        diarize: bool = False,
    ) -> Transcript:
        model = model or self.default_model
        mime = _MIME_BY_SUFFIX.get(audio_path.suffix.lower(), "application/octet-stream")

        data: dict[str, str] = {
            "model": model,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        with audio_path.open("rb") as handle:
            payload = await request_json(
                self._client,
                "POST",
                "/audio/transcriptions",
                provider=self.name,
                label=self.label,
                files={"file": (audio_path.name, handle, mime)},
                data={
                    **data,
                    # Requesting segment granularity is what makes click-to-source possible;
                    # without timestamps every generated claim would be unanchored.
                    "timestamp_granularities[]": "segment",
                },
            )

        return self._parse(payload, model)

    def _parse(self, payload: dict[str, Any], model: str) -> Transcript:
        text = payload.get("text")
        if text is None:
            raise ProviderResponseError(
                f"{self.label} returned no transcript text.", provider=self.name
            )

        segments: list[TranscriptSegment] = []
        for raw in payload.get("segments") or []:
            if not isinstance(raw, dict):
                continue
            segment_text = str(raw.get("text") or "").strip()
            if not segment_text:
                continue
            segments.append(
                TranscriptSegment(
                    start_ms=int(float(raw.get("start") or 0.0) * 1000),
                    end_ms=int(float(raw.get("end") or 0.0) * 1000),
                    text=segment_text,
                )
            )

        if not segments and str(text).strip():
            # Some models ignore verbose_json and return plain text. Better a single
            # untimed segment than losing the transcription entirely.
            log.info("%s returned no segments; wrapping the full text as one", self.label)
            segments = [TranscriptSegment(start_ms=0, end_ms=0, text=str(text).strip())]

        duration = payload.get("duration")
        return Transcript(
            text=str(text),
            segments=segments,
            language=payload.get("language"),
            duration_sec=float(duration) if duration is not None else None,
            model=model,
            raw=payload,
        )
