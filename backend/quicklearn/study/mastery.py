"""The concept mastery model — "practice until it clicks" (PRD §7.4).

The update rule is asymmetric on purpose:

    correct:   mastery += (1 - mastery) * 0.3
    incorrect: mastery *= 0.5

Gains decelerate as mastery approaches 1.0, so reaching confidence takes repeated success
rather than one lucky answer, while a single wrong answer halves the score. That asymmetry is
what makes the session exit condition meaningful — with symmetric updates a student could
alternate right and wrong indefinitely and still drift upward.

Five consecutive correct answers from zero reach ~0.83, just past the 0.8 threshold. That is
the intended pacing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select

from ..db.models import Concept, QuizAttempt, QuizItem, Source
from ..db.session import session_scope

log = logging.getLogger(__name__)

CORRECT_GAIN = 0.3
INCORRECT_FACTOR = 0.5
MASTERY_THRESHOLD = 0.8


def next_mastery(current: float, *, correct: bool, score: float | None = None) -> float:
    """Compute the updated mastery value.

    A partially correct free-response answer moves the score proportionally rather than being
    treated as a full success or a full failure — grading those as binary would make the
    mastery model noticeably unfair on exactly the questions that test real understanding.
    """
    current = max(0.0, min(1.0, current))
    if correct:
        gain = CORRECT_GAIN if score is None else CORRECT_GAIN * max(0.0, min(1.0, score))
        return round(min(1.0, current + (1.0 - current) * gain), 4)

    if score is not None and score > 0.0:
        # Partial credit softens the penalty, but never turns a wrong answer into progress.
        factor = INCORRECT_FACTOR + (1.0 - INCORRECT_FACTOR) * min(1.0, score) * 0.5
        return round(current * factor, 4)
    return round(current * INCORRECT_FACTOR, 4)


def record_attempt(concept_id: int | None, *, correct: bool, score: float | None = None) -> float:
    """Apply the update for one answer. Returns the new mastery."""
    if concept_id is None:
        return 0.0
    with session_scope() as session:
        concept = session.get(Concept, concept_id)
        if concept is None:
            return 0.0
        concept.mastery = next_mastery(concept.mastery, correct=correct, score=score)
        return concept.mastery


@dataclass(slots=True)
class WeakConcept:
    id: int
    name: str
    definition: str
    mastery: float
    source_id: int
    source_title: str
    segment_ids: list[int]


def weak_concepts(
    *,
    source_ids: list[int] | None = None,
    folder_id: int | None = None,
    limit: int = 10,
    threshold: float = MASTERY_THRESHOLD,
) -> list[WeakConcept]:
    """Lowest-mastery concepts, for the end-of-session report and the dashboard."""
    with session_scope() as session:
        stmt = (
            select(Concept, Source.title)
            .join(Source, Source.id == Concept.source_id)
            .where(Concept.mastery < threshold)
        )
        if source_ids:
            stmt = stmt.where(Concept.source_id.in_(source_ids))
        if folder_id is not None:
            stmt = stmt.where(Source.folder_id == folder_id)

        rows = session.execute(
            stmt.order_by(Concept.mastery, Concept.importance.desc()).limit(limit)
        ).all()

    return [
        WeakConcept(
            id=concept.id,
            name=concept.name,
            definition=concept.definition,
            mastery=concept.mastery,
            source_id=concept.source_id,
            source_title=title,
            segment_ids=list(concept.segment_ids or []),
        )
        for concept, title in rows
    ]


def sample_next_concepts(
    *,
    source_ids: list[int] | None = None,
    folder_id: int | None = None,
    count: int = 1,
    threshold: float = MASTERY_THRESHOLD,
) -> list[int]:
    """Pick which concepts practice mode should test next — always the weakest."""
    concepts = weak_concepts(
        source_ids=source_ids, folder_id=folder_id, limit=max(count, 5), threshold=threshold
    )
    return [concept.id for concept in concepts[:count]]


def session_complete(*, source_ids: list[int] | None = None, folder_id: int | None = None) -> bool:
    """Whether every concept in scope has passed the threshold (PRD §7.4)."""
    return not weak_concepts(source_ids=source_ids, folder_id=folder_id, limit=1)


def mastery_summary(
    *, source_ids: list[int] | None = None, folder_id: int | None = None
) -> dict[str, float | int]:
    with session_scope() as session:
        stmt = select(
            func.count(Concept.id),
            func.coalesce(func.avg(Concept.mastery), 0.0),
            func.sum(func.iif(Concept.mastery >= MASTERY_THRESHOLD, 1, 0)),
        ).select_from(Concept)
        if folder_id is not None:
            stmt = stmt.join(Source, Source.id == Concept.source_id).where(
                Source.folder_id == folder_id
            )
        if source_ids:
            stmt = stmt.where(Concept.source_id.in_(source_ids))
        total, average, mastered = session.execute(stmt).one()

    return {
        "concepts": int(total or 0),
        "average_mastery": round(float(average or 0.0), 4),
        "mastered": int(mastered or 0),
        "threshold": MASTERY_THRESHOLD,
    }


def attempt_history(quiz_item_id: int) -> list[dict[str, object]]:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(QuizAttempt)
                .where(QuizAttempt.quiz_item_id == quiz_item_id)
                .order_by(QuizAttempt.attempted_at.desc())
                .limit(20)
            ).scalars()
        )
    return [
        {
            "response": row.response,
            "is_correct": row.is_correct,
            "score": row.score,
            "attempted_at": row.attempted_at.isoformat(),
        }
        for row in rows
    ]


def reset_mastery(source_id: int) -> int:
    """Zero mastery for a source, so a user can genuinely restart a topic."""
    with session_scope() as session:
        concepts = list(
            session.execute(select(Concept).where(Concept.source_id == source_id)).scalars()
        )
        for concept in concepts:
            concept.mastery = 0.0
        return len(concepts)


def concept_for_item(quiz_item_id: int) -> int | None:
    with session_scope() as session:
        item = session.get(QuizItem, quiz_item_id)
        return item.concept_id if item else None
