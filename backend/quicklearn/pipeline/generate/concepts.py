"""Concept extraction (PRD §6.6).

An independent pass, not a by-product of note generation. Concepts are the spine of the study
engine: cards and quiz items both generate *from concepts*, which is what makes the mastery
model coherent. A concept is the unit that can be known or not known, so a flashcard rating and
a quiz answer can move the same score.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import delete, select

from ... import prompts
from ...db.models import Concept, Segment, Source
from ...db.search import upsert_many
from ...db.session import raw_sqlite, session_scope
from ...providers import gateway
from ...providers.base import text_message
from ...settings import get_settings
from ..filter import filter_for_generation
from ..schemas import ConceptList

log = logging.getLogger(__name__)

#: Segments per extraction call. Wide enough that a concept taught across several minutes is
#: seen whole, narrow enough that the model doesn't skim.
WINDOW_SEGMENTS = 45
MIN_CONCEPTS = 3
MAX_CONCEPTS_PER_WINDOW = 12


async def generate_concepts(
    source_id: int,
    *,
    job_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[int]:
    """Extract and persist concepts. Returns the new concept ids."""
    settings = get_settings()
    threshold = settings.get_float("generation.relevance_threshold", 0.3)

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source {source_id} not found")
        title, kind = source.title, str(source.kind)
        all_segments = list(
            session.execute(
                select(Segment).where(Segment.source_id == source_id).order_by(Segment.index)
            ).scalars()
        )

    segments = filter_for_generation(all_segments, threshold)
    if not segments:
        return []

    valid_ids = {segment.id for segment in segments}
    windows = [segments[i : i + WINDOW_SEGMENTS] for i in range(0, len(segments), WINDOW_SEGMENTS)]

    extracted: list[tuple[str, str, int, list[int]]] = []
    seen_names: set[str] = set()

    for index, window in enumerate(windows):
        prompt = prompts.render(
            "concepts",
            kind=kind,
            title=title,
            segments=[{"id": s.id, "text": s.text} for s in window],
            min_concepts=MIN_CONCEPTS,
            max_concepts=MAX_CONCEPTS_PER_WINDOW,
        )
        result = await gateway.complete_structured(
            "concepts",
            [text_message("user", prompt)],
            ConceptList,
            task_class="standard",
            temperature=0.2,
            source_id=source_id,
            job_id=job_id,
        )

        for concept in result.concepts:
            name = concept.name.strip()
            if not name:
                continue
            # Windows overlap conceptually even when they don't overlap textually — the same
            # term often recurs. Merge within this source rather than creating duplicates that
            # would split the mastery score two ways.
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            extracted.append(
                (
                    name,
                    concept.definition.strip(),
                    max(1, min(5, concept.importance)),
                    [sid for sid in concept.segment_ids if sid in valid_ids],
                )
            )

        if on_progress:
            on_progress(index + 1, len(windows))

    if not extracted:
        log.warning("no concepts extracted for source %s", source_id)
        return []

    with session_scope() as session:
        # Replace wholesale — re-running must not duplicate rows (PRD §11). Cascades remove
        # the cards and quiz items built on the previous set, which is correct: they were
        # generated from concepts that no longer exist.
        session.execute(delete(Concept).where(Concept.source_id == source_id))
        session.flush()
        rows = [
            Concept(
                source_id=source_id,
                name=name,
                definition=definition,
                importance=importance,
                segment_ids=segment_ids,
                mastery=0.0,
            )
            for name, definition, importance, segment_ids in extracted
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]

    with raw_sqlite() as conn:
        conn.execute(
            "DELETE FROM search_docs WHERE kind = 'concept' AND source_id = ?", (source_id,)
        )
        upsert_many(
            conn,
            "concept",
            [
                (concept_id, name, definition, source_id)
                for concept_id, (name, definition, _, _) in zip(ids, extracted, strict=True)
            ],
        )

    log.info("extracted %d concepts for source %s", len(ids), source_id)
    return ids
