"""Offline speech-to-text.

Produces a deterministic, plausibly-shaped lecture transcript spanning the real duration of the
input file, so that timestamps line up with the audio player and click-to-source can be verified
without a paid transcription. The content is filler; the *structure* is honest.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from ..base import ModelInfo, STTProvider, Transcript, TranscriptSegment

#: Sentences that read like a real lecture, so notes/cards/quizzes built on them are legible.
_LINES = [
    "Today we're going to work through the core result and then look at why it matters.",
    "Before that, a quick administrative note: the problem set deadline moves to Friday.",
    "So the key definition is this: a linear map scales its eigenvectors by a scalar factor.",
    "That scalar is the eigenvalue, and finding it means solving the characteristic polynomial.",
    "Concretely, we set the determinant of A minus lambda I equal to zero and solve.",
    "Let me do a two-by-two example on the board so you can see the mechanics.",
    "Notice that the trace gives you the sum of eigenvalues and the determinant their product.",
    "This is worth remembering because it's a fast sanity check on your arithmetic.",
    "A symmetric matrix always has real eigenvalues, and its eigenvectors are orthogonal.",
    "That result is the spectral theorem, and it's the backbone of principal component analysis.",
    "Sorry, someone's phone is going off — if you could silence that, thanks.",
    "Where this becomes practical is dimensionality reduction on high-dimensional data.",
    "You project onto the top-k eigenvectors of the covariance matrix and keep most variance.",
    "The eigenvalues tell you exactly how much variance each direction accounts for.",
    "A common exam question asks you to show why the eigenvalues must be non-negative there.",
    "The answer is that a covariance matrix is positive semi-definite by construction.",
    "Next week we'll extend this to singular value decomposition, which generalises it.",
    "Read chapter four before then, particularly the worked examples at the end.",
]


class MockSTT(STTProvider):
    name = "mock"
    label = "Mock (offline demo)"
    requires_api_key = False
    is_local = True

    @property
    def max_file_bytes(self) -> int:
        return 2 * 1024 * 1024 * 1024

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-stt", name="Mock transcription")]

    async def transcribe(
        self,
        audio_path: Path,
        *,
        model: str = "",
        language: str | None = None,
        diarize: bool = False,
    ) -> Transcript:
        duration = await self._probe_duration(audio_path)
        seed = int(hashlib.sha256(audio_path.name.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        # One segment roughly every 8 seconds, but always at least a handful so downstream
        # stages have something to chew on even for a 3-second test tone.
        count = max(6, min(len(_LINES) * 3, int(duration // 8) or 6))
        step_ms = int((duration * 1000) / count) if duration else 8000

        segments: list[TranscriptSegment] = []
        for index in range(count):
            line = _LINES[index % len(_LINES)]
            start = index * step_ms
            segments.append(
                TranscriptSegment(
                    start_ms=start,
                    end_ms=start + max(1000, step_ms - 200),
                    text=line,
                    speaker=(rng.choice(["Speaker 0", "Speaker 1"]) if diarize else None),
                )
            )

        return Transcript(
            text=" ".join(s.text for s in segments),
            segments=segments,
            language=language or "en",
            duration_sec=duration or (count * step_ms / 1000),
            model=model or "mock-stt",
            raw={"mock": True},
        )

    @staticmethod
    async def _probe_duration(audio_path: Path) -> float:
        """Real duration via ffprobe, so mock timestamps match the actual audio."""
        import asyncio

        if not audio_path.exists():
            return 0.0
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            return float(stdout.decode().strip() or 0.0)
        except Exception:
            return 0.0
