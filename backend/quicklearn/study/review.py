"""Review queue, rating application and the forecast graph (PRD §7.3)."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import case, func, select

from ..db.enums import CardState
from ..db.models import Card, Concept, Review, Source
from ..db.session import session_scope
from ..db.types import utcnow
from ..errors import NotFoundError
from . import fsrs as fsrs_module
from . import mastery

log = logging.getLogger(__name__)

#: Interleave new cards among due reviews rather than front-loading them. A session that opens
#: with twenty unseen cards feels like work; one that mixes them into review feels like practice.
NEW_CARD_RATIO = 4


def _card_dict(
    card: Card, source_title: str = "", *, with_intervals: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": card.id,
        "front": card.front,
        "back": card.back,
        "type": str(card.type),
        "state": str(card.state),
        "due": card.due.isoformat() if card.due else None,
        "reps": card.reps,
        "lapses": card.lapses,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "segment_ids": list(card.segment_ids or []),
        "source_id": card.source_id,
        "source_title": source_title,
        "concept_id": card.concept_id,
    }
    if with_intervals:
        payload["intervals"] = fsrs_module.preview_intervals(card)
    return payload


def due_cards(
    *,
    folder_id: int | None = None,
    source_ids: list[int] | None = None,
    limit: int = 50,
    include_new: bool = True,
) -> list[dict[str, Any]]:
    """The review queue: cards past due, interleaved with new ones."""
    now = utcnow()
    with session_scope() as session:
        base = (
            select(Card, Source.title)
            .join(Source, Source.id == Card.source_id)
            .where(Card.suspended.is_(False))
        )
        if folder_id is not None:
            base = base.where(Source.folder_id == folder_id)
        if source_ids:
            base = base.where(Card.source_id.in_(source_ids))

        review_rows = session.execute(
            base.where(Card.state != CardState.NEW, Card.due <= now).order_by(Card.due).limit(limit)
        ).all()

        new_rows = []
        if include_new:
            remaining = max(0, limit - len(review_rows))
            if remaining:
                new_rows = session.execute(
                    base.where(Card.state == CardState.NEW).order_by(Card.id).limit(remaining)
                ).all()

        reviews = [_card_dict(c, t, with_intervals=True) for c, t in review_rows]
        fresh = [_card_dict(c, t, with_intervals=True) for c, t in new_rows]

    # Interleave: one new card after every NEW_CARD_RATIO reviews.
    merged: list[dict[str, Any]] = []
    new_iter = iter(fresh)
    for index, item in enumerate(reviews):
        merged.append(item)
        if (index + 1) % NEW_CARD_RATIO == 0:
            nxt = next(new_iter, None)
            if nxt is not None:
                merged.append(nxt)
    merged.extend(new_iter)
    return merged[:limit]


def due_counts(*, folder_id: int | None = None) -> dict[str, int]:
    now = utcnow()
    with session_scope() as session:
        stmt = (
            select(
                func.sum(case((Card.state == CardState.NEW, 1), else_=0)),
                func.sum(
                    case(
                        ((Card.state != CardState.NEW) & (Card.due <= now), 1),
                        else_=0,
                    )
                ),
                func.count(Card.id),
            )
            .select_from(Card)
            .join(Source, Source.id == Card.source_id)
            .where(Card.suspended.is_(False))
        )
        if folder_id is not None:
            stmt = stmt.where(Source.folder_id == folder_id)
        new_count, due_count, total = session.execute(stmt).one()

    return {
        "new": int(new_count or 0),
        "due": int(due_count or 0),
        "total": int(total or 0),
    }


def apply_rating(card_id: int, rating: int, duration_ms: int | None = None) -> dict[str, Any]:
    """Record a review, reschedule the card, and nudge its concept's mastery."""
    with session_scope() as session:
        card = session.get(Card, card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found.")

        review, outcome = fsrs_module.apply_review(card, rating, duration_ms=duration_ms)
        session.add(review)
        concept_id = card.concept_id
        payload = {
            "card_id": card.id,
            "due": outcome.due.isoformat(),
            "interval": outcome.interval_label,
            "scheduled_days": outcome.scheduled_days,
            "state": str(outcome.state),
            "reps": card.reps,
            "lapses": card.lapses,
        }

    # A flashcard rating is evidence about the concept, so it moves mastery too — that is what
    # keeps cards and quizzes pointing at the same underlying model rather than two scores.
    if concept_id is not None:
        payload["mastery"] = mastery.record_attempt(concept_id, correct=rating >= 3, score=None)
    return payload


def undo_review(card_id: int) -> bool:
    """Roll back the most recent rating for a card."""
    with session_scope() as session:
        review = session.execute(
            select(Review).where(Review.card_id == card_id).order_by(Review.id.desc()).limit(1)
        ).scalar_one_or_none()
        if review is None:
            return False
        card = session.get(Card, card_id)
        if card is None:
            return False
        fsrs_module.restore(card, review.state_before or {})
        session.delete(review)
        return True


def forecast(days: int = 30, *, folder_id: int | None = None) -> list[dict[str, Any]]:
    """Cards due per day for the next ``days``, for the forecast graph."""
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = start + dt.timedelta(days=days)

    with session_scope() as session:
        stmt = (
            select(Card.due)
            .join(Source, Source.id == Card.source_id)
            .where(
                Card.suspended.is_(False),
                Card.state != CardState.NEW,
                Card.due < horizon,
            )
        )
        if folder_id is not None:
            stmt = stmt.where(Source.folder_id == folder_id)
        dues = list(session.execute(stmt).scalars())

    buckets = dict.fromkeys(range(days), 0)
    overdue = 0
    for due in dues:
        offset = (due - start).days
        if offset < 0:
            overdue += 1
        elif offset < days:
            buckets[offset] += 1

    result = [
        {
            "date": (start + dt.timedelta(days=index)).date().isoformat(),
            "count": count + (overdue if index == 0 else 0),
            "offset": index,
        }
        for index, count in sorted(buckets.items())
    ]
    return result


def review_stats() -> dict[str, Any]:
    """Aggregates for the dashboard, including the study streak."""
    now = utcnow()
    today = now.date()

    with session_scope() as session:
        total_reviews = int(session.execute(select(func.count()).select_from(Review)).scalar_one())
        reviewed_today = int(
            session.execute(
                select(func.count())
                .select_from(Review)
                .where(Review.reviewed_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
            ).scalar_one()
        )
        # Distinct calendar days with at least one review, newest first.
        day_rows = list(
            session.execute(
                select(func.date(Review.reviewed_at))
                .group_by(func.date(Review.reviewed_at))
                .order_by(func.date(Review.reviewed_at).desc())
                .limit(400)
            ).scalars()
        )
        cards_total = int(session.execute(select(func.count()).select_from(Card)).scalar_one())
        concepts_total = int(
            session.execute(select(func.count()).select_from(Concept)).scalar_one()
        )

    streak = 0
    cursor = today
    days = {str(d) for d in day_rows if d}
    # A streak survives "haven't studied yet today" — breaking it at midnight rather than at
    # the end of the day would punish someone who studies every evening.
    if str(cursor) not in days:
        cursor = cursor - dt.timedelta(days=1)
    while str(cursor) in days:
        streak += 1
        cursor -= dt.timedelta(days=1)

    return {
        "total_reviews": total_reviews,
        "reviewed_today": reviewed_today,
        "streak_days": streak,
        "cards": cards_total,
        "concepts": concepts_total,
        "can_optimize": total_reviews >= fsrs_module.OPTIMIZE_MIN_REVIEWS,
        "optimizer_available": fsrs_module.optimizer_available(),
        "optimize_threshold": fsrs_module.OPTIMIZE_MIN_REVIEWS,
    }


def set_suspended(card_id: int, suspended: bool) -> bool:
    with session_scope() as session:
        card = session.get(Card, card_id)
        if card is None:
            return False
        card.suspended = suspended
        return True
