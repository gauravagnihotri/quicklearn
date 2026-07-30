"""Speech-to-text extraction, including the split-and-stitch path for long files.

PRD §6.3: files over the provider's size limit are split on silence boundaries with a 2-second
overlap, transcribed in parallel, then stitched with overlap deduplication. The deduplication
is the fiddly part — without it every seam produces a repeated sentence, which then shows up as
a duplicated bullet in the notes.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from ...providers import gateway
from ...providers.base import TranscriptSegment
from .. import media
from .base import ExtractResult, RawUnit

log = logging.getLogger(__name__)

#: Bound on concurrent chunk transcriptions — enough to be fast, low enough not to trip
#: provider rate limits on a free tier.
MAX_PARALLEL_CHUNKS = 3


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def stitch(
    batches: list[list[TranscriptSegment]], *, overlap_ms: int = 2_000
) -> list[TranscriptSegment]:
    """Merge per-chunk transcripts, dropping segments duplicated across an overlap.

    Segments arrive already shifted onto the original timeline. A segment from a later chunk is
    dropped when it starts inside the overlap window *and* closely matches text already
    accepted — matching on text as well as timing, because a provider can place the same
    sentence a few hundred milliseconds apart in two chunks.
    """
    merged: list[TranscriptSegment] = []
    for batch in batches:
        for segment in batch:
            if not segment.text.strip():
                continue
            duplicate = False
            for existing in reversed(merged):
                if existing.end_ms < segment.start_ms - overlap_ms:
                    break  # too far back for an overlap collision
                if _similar(existing.text, segment.text) > 0.82:
                    duplicate = True
                    break
            if not duplicate:
                merged.append(segment)

    merged.sort(key=lambda s: (s.start_ms, s.end_ms))
    return merged


async def extract_audio(
    audio_path: Path,
    *,
    language: str | None = None,
    diarize: bool = False,
    source_id: int | None = None,
    job_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ExtractResult:
    """Transcribe an audio file, splitting it first if the provider cannot take it whole."""
    from ...providers import registry

    provider, model = registry.get_stt()
    size = audio_path.stat().st_size

    if size <= provider.max_file_bytes:
        transcript = await gateway.transcribe(
            audio_path,
            language=language,
            diarize=diarize and provider.supports_diarization,
            source_id=source_id,
            job_id=job_id,
        )
        if on_progress:
            on_progress(1, 1)
        return _to_result(transcript.segments, transcript, provider.name, model)

    with tempfile.TemporaryDirectory(prefix="quicklearn-split-") as tmp:
        chunks = await media.split_audio(audio_path, Path(tmp), max_bytes=provider.max_file_bytes)
        log.info(
            "transcribing %s in %d chunks (%.1f MB > %.1f MB limit)",
            audio_path.name,
            len(chunks),
            size / 1e6,
            provider.max_file_bytes / 1e6,
        )

        semaphore = asyncio.Semaphore(MAX_PARALLEL_CHUNKS)
        completed = 0
        lock = asyncio.Lock()

        async def run(index: int, chunk: media.AudioChunk) -> tuple[int, list[TranscriptSegment]]:
            nonlocal completed
            async with semaphore:
                transcript = await gateway.transcribe(
                    chunk.path,
                    language=language,
                    diarize=diarize and provider.supports_diarization,
                    source_id=source_id,
                    job_id=job_id,
                )
            offset_ms = int(chunk.offset_sec * 1000)
            shifted = [s.shifted(offset_ms) for s in transcript.segments]
            async with lock:
                completed += 1
                if on_progress:
                    on_progress(completed, len(chunks))
            return index, shifted

        results = await asyncio.gather(*(run(i, c) for i, c in enumerate(chunks)))

    ordered = [segments for _, segments in sorted(results, key=lambda pair: pair[0])]
    stitched = stitch(ordered)

    total_duration = await media.probe(audio_path)
    return ExtractResult(
        units=[_unit(s) for s in stitched],
        language=language,
        duration_sec=total_duration.duration_sec,
        provider=provider.name,
        model=model,
        raw={"chunks": len(ordered), "stitched_segments": len(stitched)},
    )


def _unit(segment: TranscriptSegment) -> RawUnit:
    return RawUnit(
        text=segment.text.strip(),
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        speaker=segment.speaker,
    )


def _to_result(
    segments: list[TranscriptSegment], transcript: object, provider: str, model: str
) -> ExtractResult:
    return ExtractResult(
        units=[_unit(s) for s in segments if s.text.strip()],
        language=getattr(transcript, "language", None),
        duration_sec=getattr(transcript, "duration_sec", None),
        provider=provider,
        model=model,
        raw=getattr(transcript, "raw", {}) or {},
    )
