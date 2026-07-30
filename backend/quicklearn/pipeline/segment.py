"""Merge raw extraction units into ``Segment`` rows.

PRD §6.4: audio uses STT timestamps merged into ~30-second semantic chunks on sentence
boundaries; documents split on structural boundaries with page and bbox retained.

Chunk size is a real trade-off. Too small and a retrieved segment lacks the context to be
meaningful on its own; too large and a citation points at a wall of text rather than the
sentence that supports the claim. ~30 seconds of speech is roughly a paragraph, which is the
unit a person would actually quote.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import delete, select

from ..db.models import Segment, Source
from ..db.search import upsert_many
from ..db.session import raw_sqlite, session_scope
from .extract.base import RawUnit

log = logging.getLogger(__name__)

DEFAULT_TARGET_SEC = 30.0
#: Hard ceiling — merge no further even mid-sentence, or one unpunctuated monologue becomes a
#: single ten-minute segment that is useless as a citation target.
MAX_SEGMENT_SEC = 75.0
MAX_DOC_CHARS = 1400
MIN_DOC_CHARS = 220

_SENTENCE_END = re.compile(r'[.!?][")\']?\s*$')


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


@dataclass(slots=True)
class PendingSegment:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    bbox: list[float] | None = None
    speaker: str | None = None
    is_marked: bool = False


def merge_timed(
    units: list[RawUnit], *, target_sec: float = DEFAULT_TARGET_SEC
) -> list[PendingSegment]:
    """Merge timestamped units into ~``target_sec`` chunks, preferring sentence boundaries."""
    segments: list[PendingSegment] = []
    buffer: list[RawUnit] = []

    def flush() -> None:
        if not buffer:
            return
        speakers = {u.speaker for u in buffer if u.speaker}
        segments.append(
            PendingSegment(
                text=" ".join(u.text.strip() for u in buffer if u.text.strip()).strip(),
                start_ms=buffer[0].start_ms,
                end_ms=buffer[-1].end_ms,
                # Only claim a speaker when the whole chunk is one person; a mixed chunk
                # labelled with one name would mislead the relevance pass.
                speaker=next(iter(speakers)) if len(speakers) == 1 else None,
                is_marked=any(u.is_marked for u in buffer),
            )
        )
        buffer.clear()

    for unit in units:
        if not unit.text.strip():
            continue
        buffer.append(unit)

        start = buffer[0].start_ms or 0
        end = unit.end_ms or unit.start_ms or start
        span = (end - start) / 1000.0

        # A speaker change is a natural boundary and usually more meaningful than the clock.
        next_is_new_speaker = (
            len(buffer) > 1 and unit.speaker is not None and unit.speaker != buffer[0].speaker
        )

        if (
            span >= MAX_SEGMENT_SEC
            or next_is_new_speaker
            or (span >= target_sec and _ends_sentence(unit.text))
        ):
            flush()

    flush()
    return [s for s in segments if s.text]


def merge_document(units: list[RawUnit]) -> list[PendingSegment]:
    """Merge document units, never crossing a page boundary.

    Keeping segments within a page means the bbox stays meaningful and click-to-source can
    highlight an actual region rather than gesturing at two pages at once.
    """
    segments: list[PendingSegment] = []
    buffer: list[RawUnit] = []

    def flush() -> None:
        if not buffer:
            return
        boxes = [u.bbox for u in buffer if u.bbox]
        union: list[float] | None = None
        if boxes:
            union = [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ]
        segments.append(
            PendingSegment(
                text="\n".join(u.text.strip() for u in buffer if u.text.strip()).strip(),
                page_no=buffer[0].page_no,
                bbox=union,
            )
        )
        buffer.clear()

    current_page: int | None = None
    length = 0

    for unit in units:
        text = unit.text.strip()
        if not text:
            continue

        page_changed = current_page is not None and unit.page_no != current_page
        # A markdown heading marks a new topic; break before it rather than after.
        is_heading = text.startswith("#")

        if buffer and (
            page_changed or length >= MAX_DOC_CHARS or (is_heading and length >= MIN_DOC_CHARS)
        ):
            flush()
            length = 0

        buffer.append(unit)
        current_page = unit.page_no
        length += len(text)

    flush()
    return [s for s in segments if s.text]


def build_segments(
    units: list[RawUnit], *, timed: bool, target_sec: float = DEFAULT_TARGET_SEC
) -> list[PendingSegment]:
    if timed and any(u.start_ms is not None for u in units):
        return merge_timed(units, target_sec=target_sec)
    return merge_document(units)


def persist_segments(
    source_id: int,
    pending: list[PendingSegment],
    *,
    transcript_id: int | None = None,
) -> list[int]:
    """Replace this source's segments and reindex them for search.

    Idempotent by deletion-then-insert (PRD §11): re-running segmentation must not duplicate
    rows, and segment ids are referenced by generated artifacts, so a clean replace also
    invalidates anything that pointed at the old ids.
    """
    with session_scope() as session:
        session.execute(delete(Segment).where(Segment.source_id == source_id))
        session.flush()

        rows = [
            Segment(
                source_id=source_id,
                transcript_id=transcript_id,
                index=index,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                page_no=item.page_no,
                bbox=item.bbox,
                speaker=item.speaker,
                text=item.text,
                is_marked=item.is_marked,
                relevance=1.0,
            )
            for index, item in enumerate(pending)
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]

        source = session.get(Source, source_id)
        title = source.title if source else ""

    with raw_sqlite() as conn:
        conn.execute(
            "DELETE FROM search_docs WHERE kind = 'segment' AND source_id = ?", (source_id,)
        )
        upsert_many(
            conn,
            "segment",
            [
                (seg_id, title, item.text, source_id)
                for seg_id, item in zip(ids, pending, strict=True)
            ],
        )

    log.info("stored %d segments for source %s", len(ids), source_id)
    return ids


def load_segments(source_id: int, *, min_relevance: float | None = None) -> list[Segment]:
    with session_scope() as session:
        stmt = select(Segment).where(Segment.source_id == source_id)
        if min_relevance is not None:
            stmt = stmt.where(Segment.relevance >= min_relevance)
        return list(session.execute(stmt.order_by(Segment.index)).scalars())


def format_timestamp(start_ms: int | None) -> str:
    if start_ms is None:
        return ""
    total = start_ms // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
