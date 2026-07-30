"""Stage functions — the pipeline's actual work, independent of the job queue.

Keeping these callable on their own means the same code path serves a full ingest, a single
retried stage, and a regeneration request, so there is one implementation of "transcribe this
source" rather than three that drift.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..config import get_config
from ..db.enums import SourceKind, SourceStatus
from ..db.models import Segment, Source, Transcript
from ..db.session import session_scope
from ..errors import PipelineError, ValidationError
from ..settings import get_settings
from . import media
from .acquire import is_timed_kind
from .extract import audio as audio_extract
from .extract import document as doc_extract
from .extract import image as image_extract
from .extract import web as web_extract
from .extract.base import ExtractResult
from .filter import classify_relevance
from .segment import build_segments, persist_segments

log = logging.getLogger(__name__)

Progress = Callable[[int, int], None] | None


def _source_snapshot(source_id: int) -> dict[str, object]:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValidationError(f"Source {source_id} no longer exists.")
        return {
            "id": source.id,
            "title": source.title,
            "kind": source.kind,
            "origin": source.origin,
            "media_path": source.media_path,
            "audio_path": source.audio_path,
            "meta": dict(source.meta or {}),
        }


def set_status(source_id: int, status: SourceStatus, error: str | None = None) -> None:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.status = status
            source.error = error


# --------------------------------------------------------------------------- normalize


async def normalize(source_id: int) -> None:
    """Stage 2 — transcode audio/video to 16 kHz mono WAV (PRD §6.2)."""
    snapshot = _source_snapshot(source_id)
    kind = snapshot["kind"]
    if kind not in {SourceKind.AUDIO, SourceKind.VIDEO}:
        return

    media_path = snapshot["media_path"]
    if not media_path or not Path(str(media_path)).exists():
        raise PipelineError("The uploaded media file is missing from disk.")

    destination = get_config().media_dir_for(source_id) / "audio.wav"
    info = await media.normalize_audio(Path(str(media_path)), destination)

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.audio_path = str(destination)
            source.duration_sec = info.duration_sec


# --------------------------------------------------------------------------- extract


async def extract(
    source_id: int, *, job_id: int | None = None, on_progress: Progress = None
) -> None:
    """Stage 3 — produce raw text with positional metadata (PRD §6.3)."""
    snapshot = _source_snapshot(source_id)
    kind = snapshot["kind"]
    settings = get_settings()

    result: ExtractResult

    if kind in {SourceKind.AUDIO, SourceKind.VIDEO}:
        audio_path = snapshot["audio_path"] or snapshot["media_path"]
        if not audio_path:
            raise PipelineError("No audio available to transcribe.")
        result = await audio_extract.extract_audio(
            Path(str(audio_path)),
            language=settings.get_str("stt.language") or None,
            diarize=settings.get_bool("stt.diarize", False),
            source_id=source_id,
            job_id=job_id,
            on_progress=on_progress,
        )
        _apply_recording_markers(source_id, result, dict(snapshot["meta"]))  # type: ignore[arg-type]

    elif kind is SourceKind.YOUTUBE:
        result = await _extract_youtube(source_id, snapshot, job_id, on_progress)

    elif kind is SourceKind.WEB:
        url = str(dict(snapshot["meta"]).get("url") or "")  # type: ignore[arg-type]
        if not url:
            raise PipelineError("This source has no URL recorded.")
        result, metadata = await web_extract.extract_article(url)
        _merge_metadata(source_id, metadata)

    elif kind is SourceKind.PDF:
        result = await doc_extract.extract_pdf(
            Path(str(snapshot["media_path"])), source_id=source_id, job_id=job_id
        )
    elif kind is SourceKind.DOCX:
        result = await doc_extract.extract_docx(Path(str(snapshot["media_path"])))
    elif kind is SourceKind.PPTX:
        result = await doc_extract.extract_pptx(Path(str(snapshot["media_path"])))
    elif kind is SourceKind.IMAGE:
        result = await image_extract.extract_image(
            Path(str(snapshot["media_path"])), source_id=source_id, job_id=job_id
        )
    elif kind is SourceKind.TEXT:
        path = Path(str(snapshot["media_path"]))
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        result = doc_extract.extract_text(content)
    else:
        raise PipelineError(f"Don't know how to extract content from a '{kind}' source.")

    if not result.units:
        raise PipelineError("No readable content was found in this source.")

    _persist_transcript(source_id, result)
    _persist_extraction(source_id, result, timed=is_timed_kind(kind))  # type: ignore[arg-type]


async def _extract_youtube(
    source_id: int, snapshot: dict[str, object], job_id: int | None, on_progress: Progress
) -> ExtractResult:
    url = str(dict(snapshot["meta"]).get("url") or "")  # type: ignore[arg-type]
    if not url:
        raise PipelineError("This source has no URL recorded.")

    media_dir = get_config().media_dir_for(source_id)
    captions, metadata, audio_path = await web_extract.extract_youtube(url, media_dir)
    _merge_metadata(source_id, metadata)

    if captions is not None:
        # Subtitle fast path — free, instant, and more accurate than transcription (PRD §6.1).
        return captions

    if audio_path is None:
        raise PipelineError("Could not obtain captions or audio for that video.")

    destination = media_dir / "audio.wav"
    info = await media.normalize_audio(audio_path, destination)
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.media_path = str(audio_path)
            source.audio_path = str(destination)
            source.duration_sec = info.duration_sec

    settings = get_settings()
    return await audio_extract.extract_audio(
        destination,
        language=settings.get_str("stt.language") or None,
        diarize=settings.get_bool("stt.diarize", False),
        source_id=source_id,
        job_id=job_id,
        on_progress=on_progress,
    )


def _apply_recording_markers(
    source_id: int, result: ExtractResult, meta: dict[str, object]
) -> None:
    """Flag units overlapping a marker the user tapped during recording (PRD §7.1)."""
    markers = [int(m) for m in (meta.get("markers") or []) if isinstance(m, int | float)]
    if not markers:
        return
    #: A marker is tapped *after* the important thing was said, so the window reaches backwards.
    window_before, window_after = 20_000, 5_000
    for unit in result.units:
        if unit.start_ms is None:
            continue
        end = unit.end_ms if unit.end_ms is not None else unit.start_ms
        if any(unit.start_ms - window_after <= m <= end + window_before for m in markers):
            unit.is_marked = True


def _merge_metadata(source_id: int, metadata: dict[str, object]) -> None:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            return
        merged = dict(source.meta or {})
        merged.update({k: v for k, v in metadata.items() if v not in (None, "")})
        source.meta = merged
        # Prefer the real title over a filename or the raw URL.
        title = str(metadata.get("title") or "")
        if title and (not source.title or source.title.startswith("http")):
            source.title = title[:400]
        duration = metadata.get("duration_sec")
        if isinstance(duration, int | float) and duration > 0 and not source.duration_sec:
            source.duration_sec = float(duration)


def _persist_transcript(source_id: int, result: ExtractResult) -> None:
    from sqlalchemy import delete

    with session_scope() as session:
        session.execute(delete(Transcript).where(Transcript.source_id == source_id))
        session.flush()
        session.add(
            Transcript(
                source_id=source_id,
                language=result.language,
                provider=result.provider or "unknown",
                model=result.model or "",
                # The provider's response is kept verbatim; source data is never destroyed.
                raw=result.raw if isinstance(result.raw, dict) else {},
            )
        )
        source = session.get(Source, source_id)
        if source is not None:
            if result.duration_sec:
                source.duration_sec = result.duration_sec
            if result.page_count:
                source.page_count = result.page_count


def _persist_extraction(source_id: int, result: ExtractResult, *, timed: bool) -> None:
    settings = get_settings()
    target = float(settings.get_int("generation.chunk_target_sec", 30))
    pending = build_segments(result.units, timed=timed, target_sec=target)
    if not pending:
        raise PipelineError("Extraction produced no usable segments.")

    with session_scope() as session:
        from sqlalchemy import select

        transcript = session.execute(
            select(Transcript).where(Transcript.source_id == source_id)
        ).scalar_one_or_none()
        transcript_id = transcript.id if transcript else None

    persist_segments(source_id, pending, transcript_id=transcript_id)


# --------------------------------------------------------------------------- filter


async def relevance(
    source_id: int, *, job_id: int | None = None, on_progress: Progress = None
) -> None:
    """Stage 5 — score instructional value (PRD §6.4)."""
    snapshot = _source_snapshot(source_id)
    await classify_relevance(
        source_id,
        kind=str(snapshot["kind"]),
        job_id=job_id,
        on_progress=on_progress,
    )


# --------------------------------------------------------------------------- helpers


def segment_count(source_id: int) -> int:
    from sqlalchemy import func, select

    with session_scope() as session:
        return int(
            session.execute(
                select(func.count()).select_from(Segment).where(Segment.source_id == source_id)
            ).scalar_one()
        )


def first_text(source_id: int, limit: int = 1200) -> str:
    from sqlalchemy import select

    with session_scope() as session:
        rows = list(
            session.execute(
                select(Segment.text)
                .where(Segment.source_id == source_id)
                .order_by(Segment.index)
                .limit(12)
            ).scalars()
        )
    return " ".join(rows)[:limit]


async def autotitle(source_id: int) -> None:
    """Give a source a real title when its filename was uninformative.

    A library full of "recording-2026-07-30" is unusable, and this costs one `fast` call.
    """
    from .. import prompts
    from ..providers import gateway
    from ..providers.base import text_message
    from .schemas import TitleSuggestion

    snapshot = _source_snapshot(source_id)
    current = str(snapshot["title"])
    generic = (
        not current
        or current.lower().startswith(("recording", "untitled", "upload", "http", "original"))
        or len(current) < 4
    )
    if not generic:
        return

    excerpt = first_text(source_id)
    if not excerpt.strip():
        return

    try:
        result = await gateway.complete_structured(
            "title",
            [text_message("user", prompts.render("title", excerpt=excerpt))],
            TitleSuggestion,
            task_class="fast",
            temperature=0.3,
            max_tokens=200,
            source_id=source_id,
        )
    except Exception as exc:
        log.debug("auto-title failed for source %s: %s", source_id, exc)
        return

    title = result.title.strip().strip('"')
    if not title:
        return

    from ..db.search import upsert as search_upsert
    from ..db.session import raw_sqlite

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.title = title[:400]
    with raw_sqlite() as conn:
        search_upsert(conn, "source", source_id, title=title, source_id=source_id)
