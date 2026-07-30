"""Offline text-to-speech.

Synthesises a real MP3 with ffmpeg rather than returning placeholder bytes. That matters
because the podcast pipeline's hardest part is the assembly step — concatenating dozens of
clips with gaps and crossfades and getting a playable file with correct duration out the other
end. Feeding it fake bytes would leave exactly that logic unverified.

The audio is a quiet tone whose length tracks the line's word count, so a generated podcast has
a believable duration and the player's seek bar behaves.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from ...errors import MediaError
from ..base import ModelInfo, TTSProvider, Voice

log = logging.getLogger(__name__)

VOICES = [
    Voice("mock-a", "Mock Host A", "Low tone"),
    Voice("mock-b", "Mock Host B", "High tone"),
]

#: Average speaking rate, used to derive a plausible clip length from the text.
WORDS_PER_SECOND = 2.6


class MockTTS(TTSProvider):
    name = "mock"
    label = "Mock (offline demo)"
    requires_api_key = False
    is_local = True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-tts", name="Mock speech")]

    async def available_voices(self) -> list[Voice]:
        return list(VOICES)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
    ) -> bytes:
        words = max(1, len(text.split()))
        duration = max(0.6, min(60.0, words / (WORDS_PER_SECOND * max(0.5, speed))))
        # Distinct pitch per host, so a two-voice podcast is audibly two voices.
        frequency = 196 if (voice or "mock-a").endswith("a") else 294

        with tempfile.TemporaryDirectory(prefix="quicklearn-tts-") as tmp:
            out = Path(tmp) / "line.mp3"
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration={duration:.2f}",
                "-af",
                "volume=0.06",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "64k",
                str(out),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0 or not out.exists():
                raise MediaError(
                    "The offline mock voice needs ffmpeg on PATH to generate audio. "
                    f"ffmpeg said: {stderr.decode(errors='replace')[:300]}"
                )
            return out.read_bytes()
