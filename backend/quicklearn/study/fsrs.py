"""FSRS-6 scheduling (PRD §7.3).

FSRS rather than SM-2 because it is meaningfully more accurate and is now Anki's default, which
means it is the algorithm users' expectations are already calibrated to. A card that Anki would
show tomorrow should not show up here in six days.

**Reconciling the library with our schema.** FSRS-6's own ``Card`` dropped several fields the
PRD's data model lists, and removed the ``New`` state entirely — a fresh card is ``Learning``
with ``step=0`` and no stability. Those fields are kept on our row and maintained here because:

* ``reps`` / ``lapses`` are required to round-trip through Anki's ``.apkg`` format.
* ``scheduled_days`` drives the forecast graph.
* A ``new`` state is needed for the "12 new / 34 due" split every review UI shows.

They are presentation and interop state, and are never fed back into the scheduler.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import fsrs as fsrs_lib
from fsrs import Card as FSRSCard
from fsrs import Rating, Scheduler, State

from ..db.enums import CardState
from ..db.models import Card, Review
from ..db.types import utcnow
from ..settings import get_settings

log = logging.getLogger(__name__)

#: PRD §7.3 — offer personal parameter optimisation once there is enough history for it to
#: mean anything. Below this, optimisation overfits to noise.
OPTIMIZE_MIN_REVIEWS = 400

_STATE_TO_DB = {
    State.Learning: CardState.LEARNING,
    State.Review: CardState.REVIEW,
    State.Relearning: CardState.RELEARNING,
}
_DB_TO_STATE = {
    CardState.NEW: State.Learning,
    CardState.LEARNING: State.Learning,
    CardState.REVIEW: State.Review,
    CardState.RELEARNING: State.Relearning,
}


def build_scheduler() -> Scheduler:
    """Scheduler configured from user settings, including optimised parameters if present."""
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "desired_retention": settings.get_float("fsrs.desired_retention", 0.9)
    }

    parameters = settings.get("fsrs.parameters")
    if isinstance(parameters, list) and parameters:
        try:
            kwargs["parameters"] = tuple(float(p) for p in parameters)
        except (TypeError, ValueError):
            log.warning("stored FSRS parameters are malformed; using defaults")

    steps = settings.get_list("fsrs.learning_steps_min")
    if steps:
        with contextlib.suppress(TypeError, ValueError):
            kwargs["learning_steps"] = tuple(dt.timedelta(minutes=float(s)) for s in steps)

    try:
        return Scheduler(**kwargs)
    except Exception as exc:
        # Bad stored parameters must not make review impossible.
        log.warning("could not build scheduler with stored settings (%s); using defaults", exc)
        return Scheduler()


def to_fsrs_card(card: Card) -> FSRSCard:
    """Project a database row into the library's card object."""
    if card.state is CardState.NEW or card.last_review is None:
        # FSRS-6 has no `new`: an unseen card is Learning at step 0 with no stability yet.
        return FSRSCard(card_id=card.id, state=State.Learning, step=0, due=card.due)

    return FSRSCard(
        card_id=card.id,
        state=_DB_TO_STATE.get(card.state, State.Review),
        step=card.step,
        stability=card.stability,
        difficulty=card.difficulty,
        due=card.due,
        last_review=card.last_review,
    )


def snapshot(card: Card) -> dict[str, Any]:
    """Scheduling state as plain JSON, stored on each review so a rating can be undone."""
    return {
        "due": card.due.isoformat() if card.due else None,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "step": card.step,
        "elapsed_days": card.elapsed_days,
        "scheduled_days": card.scheduled_days,
        "reps": card.reps,
        "lapses": card.lapses,
        "state": str(card.state),
        "last_review": card.last_review.isoformat() if card.last_review else None,
    }


def restore(card: Card, state: dict[str, Any]) -> None:
    """Reverse of :func:`snapshot`, for undoing a misclick."""
    if not state:
        return
    due = state.get("due")
    last = state.get("last_review")
    card.due = dt.datetime.fromisoformat(due) if due else utcnow()
    card.stability = state.get("stability")
    card.difficulty = state.get("difficulty")
    card.step = state.get("step", 0)
    card.elapsed_days = int(state.get("elapsed_days") or 0)
    card.scheduled_days = int(state.get("scheduled_days") or 0)
    card.reps = int(state.get("reps") or 0)
    card.lapses = int(state.get("lapses") or 0)
    card.state = CardState(state.get("state") or CardState.NEW)
    card.last_review = dt.datetime.fromisoformat(last) if last else None


@dataclass(slots=True)
class ReviewOutcome:
    due: dt.datetime
    scheduled_days: int
    state: CardState
    #: Human-readable interval for the button label — "3d", "2mo".
    interval_label: str


def _interval_label(delta: dt.timedelta) -> str:
    minutes = delta.total_seconds() / 60
    if minutes < 60:
        return f"{max(1, round(minutes))}m"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)}h"
    days = hours / 24
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{round(days / 30.4)}mo"
    return f"{days / 365:.1f}y"


def preview_intervals(card: Card, *, now: dt.datetime | None = None) -> dict[int, str]:
    """What each of the four buttons would schedule.

    Anki shows this and users rely on it to choose a rating honestly, rather than picking
    "Good" for everything because the consequences are invisible.
    """
    now = now or utcnow()
    scheduler = build_scheduler()
    labels: dict[int, str] = {}
    for rating in (Rating.Again, Rating.Hard, Rating.Good, Rating.Easy):
        try:
            projected, _ = scheduler.review_card(to_fsrs_card(card), rating, review_datetime=now)
            labels[int(rating)] = _interval_label(projected.due - now)
        except Exception:
            labels[int(rating)] = "—"
    return labels


def apply_review(
    card: Card,
    rating: int,
    *,
    duration_ms: int | None = None,
    now: dt.datetime | None = None,
) -> tuple[Review, ReviewOutcome]:
    """Apply a rating, mutating the card in place and returning the review to persist."""
    if rating not in (1, 2, 3, 4):
        raise ValueError(f"Rating must be 1-4, got {rating}")

    now = now or utcnow()
    before = snapshot(card)
    scheduler = build_scheduler()
    updated, _ = scheduler.review_card(
        to_fsrs_card(card), Rating(rating), review_datetime=now, review_duration=duration_ms
    )

    previous_review = card.last_review
    was_review_state = card.state is CardState.REVIEW

    card.elapsed_days = (now - previous_review).days if previous_review else 0
    card.due = updated.due
    card.stability = updated.stability
    card.difficulty = updated.difficulty
    card.step = updated.step
    card.last_review = updated.last_review or now
    card.state = _STATE_TO_DB.get(updated.state, CardState.REVIEW)
    card.scheduled_days = max(0, (updated.due - now).days)
    card.reps += 1
    # A lapse is forgetting something you had already learned. Counting an "Again" during
    # initial learning would inflate the number and distort the Anki export.
    if rating == 1 and was_review_state:
        card.lapses += 1

    review = Review(
        card_id=card.id,
        rating=rating,
        reviewed_at=now,
        duration_ms=duration_ms,
        state_before=before,
    )
    return review, ReviewOutcome(
        due=card.due,
        scheduled_days=card.scheduled_days,
        state=card.state,
        interval_label=_interval_label(card.due - now),
    )


# --------------------------------------------------------------------------- optimisation


def optimizer_available() -> bool:
    """Whether personal parameter optimisation can run.

    ``fsrs[optimizer]`` depends on PyTorch, which is deliberately not installed — it would add
    several gigabytes for a feature used once per few hundred reviews.
    """
    try:
        import torch  # noqa: F401

        return hasattr(fsrs_lib, "Optimizer")
    except ImportError:
        return False


def optimize_parameters(reviews: list[Review]) -> list[float]:
    """Fit FSRS parameters to real review history."""
    if not optimizer_available():
        from ..errors import DependencyMissingError

        raise DependencyMissingError(
            "FSRS parameter optimisation needs the optional optimiser, which depends on "
            'PyTorch (~2GB). Install it with:  pip install "quicklearn[fsrs-optimizer]"  '
            "then restart quicklearn. Your reviews are already being recorded, so nothing is "
            "lost by enabling this later."
        )

    if len(reviews) < OPTIMIZE_MIN_REVIEWS:
        raise ValueError(
            f"Optimisation needs at least {OPTIMIZE_MIN_REVIEWS} reviews; you have "
            f"{len(reviews)}. Keep reviewing and check back."
        )

    from fsrs import Optimizer, ReviewLog

    logs = [
        ReviewLog(
            card_id=review.card_id,
            rating=Rating(review.rating),
            review_datetime=review.reviewed_at,
            review_duration=review.duration_ms,
        )
        for review in sorted(reviews, key=lambda r: r.reviewed_at)
    ]
    optimizer = Optimizer(logs)
    return [float(p) for p in optimizer.compute_optimal_parameters()]
