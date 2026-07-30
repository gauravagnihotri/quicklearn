"""Deepgram speech-to-text.

The only cloud STT provider here that does speaker diarization, which matters because PRD §6.4
uses speaker labels to help the relevance pass separate lecture content from student
cross-talk. With the local ``pyannote`` path out of scope for v1, this is the diarization
option.

Also accepts far larger files than the Whisper endpoints, so long recordings usually avoid the
split-and-stitch path entirely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import ProviderResponseError
from ..base import ModelInfo, STTProvider, Transcript, TranscriptSegment
from ..http import LONG_TIMEOUT, build_client, request_json

MODELS = ("nova-3", "nova-2", "nova-2-medical", "enhanced", "base")


class DeepgramSTT(STTProvider):
    name = "deepgram"
    label = "Deepgram"
    default_base_url = "https://api.deepgram.com/v1"
    default_model = "nova-2"

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            # Deepgram uses `Token`, not `Bearer`.
            headers={"Authorization": f"Token {api_key}"} if api_key else {},
            timeout=LONG_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def max_file_bytes(self) -> int:
        return 2 * 1024 * 1024 * 1024

    @property
    def supports_diarization(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        # Deepgram has no public model-listing endpoint, so validate the key with a cheap
        # authenticated call and return the documented catalogue.
        await request_json(
            self._client,
            "GET",
            "/projects",
            provider=self.name,
            label=self.label,
        )
        return [ModelInfo(id=m) for m in MODELS]

    async def transcribe(
        self,
        audio_path: Path,
        *,
        model: str = "",
        language: str | None = None,
        diarize: bool = False,
    ) -> Transcript:
        params: dict[str, str] = {
            "model": model or self.default_model,
            "smart_format": "true",
            "punctuate": "true",
            # Utterances give sentence-level timing, which is what the segmenter wants.
            "utterances": "true",
        }
        if diarize:
            params["diarize"] = "true"
        if language:
            params["language"] = language

        content = audio_path.read_bytes()
        suffix = audio_path.suffix.lower()
        content_type = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".webm": "audio/webm",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
        }.get(suffix, "application/octet-stream")

        payload = await request_json(
            self._client,
            "POST",
            "/listen",
            provider=self.name,
            label=self.label,
            params=params,
            content=content,
            headers={"Content-Type": content_type},
        )
        return self._parse(payload, params["model"])

    def _parse(self, payload: dict[str, Any], model: str) -> Transcript:
        results = payload.get("results") or {}
        channels = results.get("channels") or []
        if not channels:
            raise ProviderResponseError(
                "Deepgram returned no transcription channels.", provider=self.name
            )

        alternatives = channels[0].get("alternatives") or []
        full_text = str(alternatives[0].get("transcript") or "") if alternatives else ""

        segments: list[TranscriptSegment] = []
        for utterance in results.get("utterances") or []:
            if not isinstance(utterance, dict):
                continue
            text = str(utterance.get("transcript") or "").strip()
            if not text:
                continue
            speaker = utterance.get("speaker")
            segments.append(
                TranscriptSegment(
                    start_ms=int(float(utterance.get("start") or 0.0) * 1000),
                    end_ms=int(float(utterance.get("end") or 0.0) * 1000),
                    text=text,
                    speaker=f"Speaker {speaker}" if speaker is not None else None,
                )
            )

        if not segments and full_text:
            segments = [TranscriptSegment(start_ms=0, end_ms=0, text=full_text)]

        metadata = payload.get("metadata") or {}
        duration = metadata.get("duration")
        return Transcript(
            text=full_text,
            segments=segments,
            language=None,
            duration_sec=float(duration) if duration is not None else None,
            model=model,
            raw=payload,
        )
