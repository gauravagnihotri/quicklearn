"""Note generation — map-reduce in three passes (PRD §6.5).

Never single-shot. A 90-minute lecture is 15k+ tokens of transcript, and single-pass generation
degrades badly past roughly 3k tokens of *output* no matter how large the context window is.
The failure mode is specific and recognisable: the first third is detailed, the middle thins
out, and the last third becomes a bulleted list of topic names.

* **Pass 1** plans an outline over the whole filtered transcript.
* **Pass 2** expands each section independently and in parallel, seeing only its own segments
  plus the outline for context — so each call writes 400 focused tokens, not 4000 sprawling ones.
* **Pass 3** assembles the blocks into a TipTap document and writes ``NoteBlock`` rows.

Sections stream to the UI as they finish, so the user watches notes build rather than staring
at a spinner.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import delete, select

from ... import prompts
from ...config import get_config
from ...db.enums import BlockType
from ...db.models import Note, NoteBlock, Segment, Source
from ...db.search import upsert as search_upsert
from ...db.session import raw_sqlite, session_scope
from ...jobs.events import emit
from ...providers import gateway
from ...providers.base import text_message
from ...settings import get_settings
from .. import tiptap
from ..filter import filter_for_generation
from ..schemas import NoteBlockOut, Outline, OutlineSection, SectionBlocks
from ..segment import format_timestamp

log = logging.getLogger(__name__)

#: Fraction of the model's context window the outline pass may fill with transcript. The rest
#: is instructions plus room for the response.
CONTEXT_BUDGET = 0.45
MIN_SECTIONS = 3
MAX_SECTIONS = 14


def _segment_payload(segments: Sequence[Segment]) -> list[dict[str, Any]]:
    return [
        {
            "id": segment.id,
            "text": segment.text,
            "timestamp": format_timestamp(segment.start_ms)
            if segment.start_ms is not None
            else (f"p.{segment.page_no}" if segment.page_no else ""),
        }
        for segment in segments
    ]


def _windows(segments: list[Segment], budget_chars: int) -> list[list[Segment]]:
    """Split segments into context-sized windows.

    Used only when a transcript is too long to outline in one call. Outlining each window and
    concatenating is preferred over a rolling summary because a summary loses the segment ids,
    and without those the expansion pass has nothing to anchor to.
    """
    windows: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0
    for segment in segments:
        length = len(segment.text) + 24  # id and timestamp overhead
        if current and size + length > budget_chars:
            windows.append(current)
            current, size = [], 0
        current.append(segment)
        size += length
    if current:
        windows.append(current)
    return windows


async def build_outline(
    segments: list[Segment],
    *,
    kind: str,
    title: str,
    style: str,
    source_id: int,
    job_id: int | None = None,
) -> list[OutlineSection]:
    from ...providers import registry

    resolved = registry.get_llm("standard")
    budget_chars = int(resolved.provider.context_window * CONTEXT_BUDGET * 4)
    windows = _windows(segments, budget_chars)

    if len(windows) > 1:
        log.info(
            "transcript exceeds the outline context budget; outlining in %d windows",
            len(windows),
        )

    sections: list[OutlineSection] = []
    per_window = max(2, MAX_SECTIONS // max(1, len(windows)))

    for window in windows:
        prompt = prompts.render(
            "note_outline",
            kind=kind,
            title=title,
            style=style,
            segments=_segment_payload(window),
            min_sections=min(MIN_SECTIONS, per_window),
            max_sections=per_window,
        )
        result = await gateway.complete_structured(
            "note outline",
            [text_message("user", prompt)],
            Outline,
            task_class="standard",
            temperature=0.2,
            source_id=source_id,
            job_id=job_id,
        )
        sections.extend(result.sections)

    valid_ids = {segment.id for segment in segments}
    ordered = {segment.id: segment.index for segment in segments}

    cleaned: list[OutlineSection] = []
    for section in sections:
        # Clamp to real ids. Models invent segment ids, and an anchor pointing at a
        # non-existent segment breaks click-to-source silently.
        kept = [sid for sid in section.segment_ids if sid in valid_ids]
        if not kept:
            continue
        section.segment_ids = sorted(set(kept), key=lambda sid: ordered[sid])
        cleaned.append(section)

    if not cleaned:
        # The model gave us nothing usable. Fall back to even slices so the user still gets
        # notes rather than an error.
        log.warning("outline had no valid segment references; falling back to even slices")
        slice_count = min(MAX_SECTIONS, max(1, len(segments) // 8))
        size = max(1, len(segments) // slice_count)
        for index in range(0, len(segments), size):
            chunk = segments[index : index + size]
            cleaned.append(
                OutlineSection(
                    title=f"Part {len(cleaned) + 1}",
                    thesis="",
                    segment_ids=[segment.id for segment in chunk],
                )
            )

    return cleaned[:MAX_SECTIONS]


async def expand_section(
    section: OutlineSection,
    outline: list[OutlineSection],
    segments_by_id: dict[int, Segment],
    *,
    style: str,
    source_id: int,
    job_id: int | None = None,
) -> list[NoteBlockOut]:
    section_segments = [segments_by_id[sid] for sid in section.segment_ids if sid in segments_by_id]
    if not section_segments:
        return []

    prompt = prompts.render(
        "note_section",
        section={"title": section.title, "thesis": section.thesis},
        outline=[{"title": item.title} for item in outline],
        segments=_segment_payload(section_segments),
        style=style,
        heading_level=2,
    )
    result = await gateway.complete_structured(
        f"section '{section.title}'",
        [text_message("user", prompt)],
        SectionBlocks,
        task_class="standard",
        temperature=0.3,
        max_tokens=4096,
        source_id=source_id,
        job_id=job_id,
    )

    allowed = {segment.id for segment in section_segments}
    fallback = sorted(allowed)[:1]
    blocks: list[NoteBlockOut] = []

    for block in result.blocks:
        # Hallucination defence: an anchor must point at a segment actually shown to the model
        # for this section. Anything else is discarded and replaced with the section's own
        # first segment, so the block stays traceable to somewhere real.
        kept = [sid for sid in block.segment_ids if sid in allowed]
        block.segment_ids = kept or fallback

        if block.type == "mermaid" and not tiptap.looks_like_mermaid(block.text):
            # A broken diagram renders as a red error box mid-note, which reads as more broken
            # than simply showing the source.
            log.debug("demoting invalid mermaid block to code in section '%s'", section.title)
            block.type = "code"
            block.language = "text"

        if block.type == "heading" and not block.text.strip():
            # Local patch: models often omit the heading text because the section title was in
            # the prompt; without this the heading is dropped and the note loses its structure.
            block.text = section.title
        if block.type == "table" and not block.rows:
            continue
        if not block.text.strip() and not block.items and not block.rows:
            continue

        blocks.append(block)

    return blocks


async def generate_notes(
    source_id: int,
    *,
    style: str = "",
    job_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Run all three passes and persist the note. Returns the note id."""
    settings = get_settings()
    style = style or settings.get_str("generation.note_style", "detailed")
    threshold = settings.get_float("generation.relevance_threshold", 0.3)

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source {source_id} not found")
        title = source.title
        kind = str(source.kind)
        all_segments = list(
            session.execute(
                select(Segment).where(Segment.source_id == source_id).order_by(Segment.index)
            ).scalars()
        )

    segments = filter_for_generation(all_segments, threshold)
    if not segments:
        raise ValueError("No content available to generate notes from.")

    log.info(
        "generating notes for source %s: %d/%d segments after relevance filtering",
        source_id,
        len(segments),
        len(all_segments),
    )

    # --- Pass 1: outline
    emit("job.progress", job_id=job_id, progress=5, stage="Planning structure")
    outline = await build_outline(
        segments, kind=kind, title=title, style=style, source_id=source_id, job_id=job_id
    )
    if not outline:
        raise ValueError("Could not derive an outline from this material.")

    segments_by_id = {segment.id: segment for segment in segments}

    # --- Pass 2: parallel expansion, bounded
    concurrency = max(1, get_config().max_parallel_llm)
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    lock = asyncio.Lock()

    async def run(index: int, section: OutlineSection) -> tuple[int, list[NoteBlockOut]]:
        nonlocal completed
        async with semaphore:
            try:
                blocks = await expand_section(
                    section,
                    outline,
                    segments_by_id,
                    style=style,
                    source_id=source_id,
                    job_id=job_id,
                )
            except Exception as exc:
                # One failed section must not lose the other eleven.
                log.warning("section '%s' failed to expand: %s", section.title, exc)
                blocks = [
                    NoteBlockOut(
                        type="heading",
                        level=2,
                        text=section.title,
                        segment_ids=section.segment_ids[:1],
                    ),
                    NoteBlockOut(
                        type="callout",
                        variant="warning",
                        text=f"This section could not be generated: {exc}",
                        segment_ids=section.segment_ids[:1],
                    ),
                ]

        async with lock:
            completed += 1
            if on_progress:
                on_progress(completed, len(outline))
            emit(
                "note.section",
                source_id=source_id,
                job_id=job_id,
                section=section.title,
                done=completed,
                total=len(outline),
            )
        return index, blocks

    results = await asyncio.gather(*(run(i, s) for i, s in enumerate(outline)))

    # --- Pass 3: assemble in outline order
    blocks: list[NoteBlockOut] = []
    for _, section_blocks in sorted(results, key=lambda pair: pair[0]):
        blocks.extend(section_blocks)

    if not blocks:
        raise ValueError("Note generation produced no content.")

    return persist_note(source_id, title, blocks, style=style)


def persist_note(
    source_id: int, title: str, blocks: list[NoteBlockOut], *, style: str = "detailed"
) -> int:
    """Write the document and its denormalised blocks, replacing any previous generation."""
    doc = tiptap.blocks_to_doc(blocks)
    outline = tiptap.outline_from_blocks(blocks)

    with session_scope() as session:
        existing = (
            session.execute(select(Note).where(Note.source_id == source_id).order_by(Note.id))
            .scalars()
            .first()
        )

        if existing is None:
            note = Note(source_id=source_id, title=title, doc=doc, outline=outline, style=style)
            session.add(note)
            session.flush()
        else:
            note = existing
            note.title = title
            note.doc = doc
            note.outline = outline
            note.style = style
            note.version += 1
            note.is_dirty = False
            session.execute(delete(NoteBlock).where(NoteBlock.note_id == note.id))
            session.flush()

        session.add_all(
            [
                NoteBlock(
                    note_id=note.id,
                    index=index,
                    type=BlockType(block.type),
                    text=block.text[:20000],
                    segment_ids=block.segment_ids,
                )
                for index, block in enumerate(blocks)
            ]
        )
        note_id = note.id

    with raw_sqlite() as conn:
        search_upsert(
            conn,
            "note",
            note_id,
            title=title,
            body="\n".join(block.text for block in blocks),
            source_id=source_id,
        )

    log.info("persisted note %s for source %s (%d blocks)", note_id, source_id, len(blocks))
    return note_id


def reindex_note(note_id: int) -> None:
    """Refresh ``NoteBlock`` rows and the search index after a user edit.

    Called on autosave. Without it, a user's own writing would be invisible to search — which
    would be a strange thing for a tool built around finding your own material.
    """
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note is None:
            return
        blocks = tiptap.doc_to_blocks(note.doc or tiptap.empty_doc())
        note.outline = tiptap.outline_from_blocks(blocks)
        session.execute(delete(NoteBlock).where(NoteBlock.note_id == note_id))
        session.flush()
        session.add_all(
            [
                NoteBlock(
                    note_id=note_id,
                    index=index,
                    type=BlockType(block.type),
                    text=block.text[:20000],
                    segment_ids=block.segment_ids,
                )
                for index, block in enumerate(blocks)
            ]
        )
        title = note.title
        source_id = note.source_id
        body = "\n".join(block.text for block in blocks)

    with raw_sqlite() as conn:
        search_upsert(conn, "note", note_id, title=title, body=body, source_id=source_id)
