"""Flashcard generation from concepts (PRD §7.3)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import delete, select

from ... import prompts
from ...db.enums import CardState, CardType
from ...db.models import Card, Concept, Source
from ...db.search import upsert_many
from ...db.session import raw_sqlite, session_scope
from ...db.types import utcnow
from ...providers import gateway
from ...providers.base import text_message
from ...settings import get_settings
from ..schemas import CardList

log = logging.getLogger(__name__)

CONCEPTS_PER_CALL = 12


async def generate_cards(
    source_id: int,
    *,
    max_cards: int | None = None,
    job_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[int]:
    """Generate cards for every concept on a source. Returns the new card ids."""
    settings = get_settings()
    cap = max_cards or settings.get_int("generation.max_cards_per_source", 50)

    with session_scope() as session:
        source = session.get(Source, source_id)
        title = source.title if source else ""
        concepts = list(
            session.execute(
                select(Concept)
                .where(Concept.source_id == source_id)
                .order_by(Concept.importance.desc(), Concept.id)
            ).scalars()
        )
        concept_data = [
            {
                "id": c.id,
                "name": c.name,
                "definition": c.definition,
                "importance": c.importance,
                "segment_ids": c.segment_ids,
            }
            for c in concepts
        ]

    if not concept_data:
        log.info("no concepts on source %s; nothing to make cards from", source_id)
        return []

    valid_concept_ids = {c["id"] for c in concept_data}
    # Aim for roughly one card per concept, but never exceed the user's cap. Importance
    # ordering above means the cap trims the least important material first.
    per_concept = 2 if len(concept_data) * 2 <= cap else 1

    batches = [
        concept_data[i : i + CONCEPTS_PER_CALL]
        for i in range(0, len(concept_data), CONCEPTS_PER_CALL)
    ]
    collected: list[tuple[CardType, str, str, int | None, list[int]]] = []

    for index, batch in enumerate(batches):
        prompt = prompts.render("cards", title=title, concepts=batch, max_per_concept=per_concept)
        result = await gateway.complete_structured(
            "flashcards",
            [text_message("user", prompt)],
            CardList,
            task_class="standard",
            temperature=0.3,
            source_id=source_id,
            job_id=job_id,
        )

        by_id = {c["id"]: c for c in batch}
        for card in result.cards:
            front, back = card.front.strip(), card.back.strip()
            if not front or not back:
                continue
            concept_id = card.concept_id if card.concept_id in valid_concept_ids else None
            if concept_id is None and len(batch) == 1:
                concept_id = batch[0]["id"]
            segment_ids = card.segment_ids or (
                list(by_id.get(concept_id, {}).get("segment_ids", [])) if concept_id else []
            )
            collected.append((CardType(card.type), front, back, concept_id, segment_ids))

        if on_progress:
            on_progress(index + 1, len(batches))

    if len(collected) > cap:
        log.info("trimming %d generated cards to the cap of %d", len(collected), cap)
        collected = collected[:cap]

    if not collected:
        return []

    now = utcnow()
    with session_scope() as session:
        session.execute(delete(Card).where(Card.source_id == source_id))
        session.flush()
        rows = [
            Card(
                source_id=source_id,
                concept_id=concept_id,
                type=card_type,
                front=front,
                back=back,
                segment_ids=segment_ids,
                # A new card is due immediately; FSRS takes over from the first review.
                due=now,
                state=CardState.NEW,
                step=0,
            )
            for card_type, front, back, concept_id, segment_ids in collected
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]

    with raw_sqlite() as conn:
        conn.execute("DELETE FROM search_docs WHERE kind = 'card' AND source_id = ?", (source_id,))
        upsert_many(
            conn,
            "card",
            [
                (card_id, front, back, source_id)
                for card_id, (_, front, back, _, _) in zip(ids, collected, strict=True)
            ],
        )

    log.info("generated %d cards for source %s", len(ids), source_id)
    return ids
