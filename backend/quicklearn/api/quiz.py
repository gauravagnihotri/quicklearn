"""Quiz sessions, adaptive practice and grading (PRD §7.4)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db.enums import QuizMode, QuizSessionStatus
from ..db.models import Concept, QuizAttempt, QuizItem, QuizSession, Source
from ..db.session import session_scope
from ..db.types import utcnow
from ..errors import NotFoundError, ValidationError
from ..pipeline.generate import quiz as quiz_gen
from ..study import mastery
from .common import quiz_item_dict, run_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/quiz", tags=["quiz"])


class SessionRequest(BaseModel):
    mode: Literal["practice", "test", "cumulative"] = "practice"
    source_ids: list[int] = Field(default_factory=list)
    folder_id: int | None = None
    length: int | None = Field(default=None, ge=1, le=100)


class AnswerRequest(BaseModel):
    item_id: int
    response: str = ""


def _resolve_scope(session_row: QuizSession) -> tuple[list[int] | None, int | None]:
    return (list(session_row.source_ids) or None), session_row.folder_id


@router.post("/session")
async def create_session(payload: SessionRequest) -> dict[str, Any]:
    if not payload.source_ids and payload.folder_id is None:
        raise ValidationError("Choose at least one source or a folder to quiz on.")

    def create() -> dict[str, Any]:
        with session_scope() as session:
            # Cumulative mode spans a folder — the exam-prep case in PRD §2.2.
            source_ids = list(payload.source_ids)
            if payload.folder_id is not None and not source_ids:
                source_ids = list(
                    session.execute(
                        select(Source.id).where(Source.folder_id == payload.folder_id)
                    ).scalars()
                )

            concept_count = int(
                session.execute(
                    select(func.count())
                    .select_from(Concept)
                    .where(Concept.source_id.in_(source_ids))
                ).scalar_one()
                if source_ids
                else 0
            )
            if not concept_count:
                raise ValidationError(
                    "No concepts have been extracted for that material yet. Open the source "
                    "and use Regenerate → Concepts first."
                )

            row = QuizSession(
                mode=QuizMode(payload.mode),
                source_ids=source_ids,
                folder_id=payload.folder_id,
                length=payload.length,
            )
            session.add(row)
            session.flush()
            return {
                "session_id": row.id,
                "mode": str(row.mode),
                "length": row.length,
                "concepts": concept_count,
                "source_ids": source_ids,
            }

    return await run_db(create)


@router.get("/session/{session_id}/next")
async def next_item(session_id: int) -> dict[str, Any]:
    """Serve the next question.

    Practice mode generates a fresh item against the weakest concept rather than resampling a
    fixed bank — that is what makes it endless, and what stops a student memorising the
    questions instead of the material (PRD §7.4).
    """

    def load() -> tuple[QuizSession, list[int] | None, int | None]:
        with session_scope() as session:
            row = session.get(QuizSession, session_id)
            if row is None:
                raise NotFoundError(f"Quiz session {session_id} not found.")
            session.expunge(row)
            return row, (list(row.source_ids) or None), row.folder_id

    row, source_ids, folder_id = await run_db(load)

    if row.status is not QuizSessionStatus.ACTIVE:
        return {"done": True, "reason": "This session has already finished."}

    if row.length is not None and row.served >= row.length:
        await run_db(_finish_session, session_id)
        return {"done": True, "reason": "Reached the end of the quiz."}

    if row.mode is QuizMode.PRACTICE:
        if mastery.session_complete(source_ids=source_ids, folder_id=folder_id):
            await run_db(_finish_session, session_id)
            return {"done": True, "reason": "Every concept is above the mastery threshold."}

        concept_ids = mastery.sample_next_concepts(
            source_ids=source_ids, folder_id=folder_id, count=1
        )
        if not concept_ids:
            await run_db(_finish_session, session_id)
            return {"done": True, "reason": "Nothing left to practise."}

        item_ids = await quiz_gen.generate_adaptive_items(concept_ids, count=1)
        if not item_ids:
            raise ValidationError("Could not generate a practice question. Try again.")
        item_id = item_ids[0]
    else:
        item_id = await run_db(_pick_bank_item, session_id, source_ids)
        if item_id is None:
            await run_db(_finish_session, session_id)
            return {"done": True, "reason": "No more questions in the bank."}

    def serve() -> dict[str, Any]:
        with session_scope() as session:
            item = session.get(QuizItem, item_id)
            if item is None:
                raise NotFoundError("Question disappeared.")
            row_inner = session.get(QuizSession, session_id)
            if row_inner is not None:
                row_inner.served += 1
                served = row_inner.served
            else:
                served = 0
            concept = session.get(Concept, item.concept_id) if item.concept_id else None
            payload = quiz_item_dict(item)
            payload["concept"] = (
                {"id": concept.id, "name": concept.name, "mastery": round(concept.mastery, 3)}
                if concept
                else None
            )
            payload["served"] = served
            return payload

    result = await run_db(serve)
    result["done"] = False
    result["progress"] = mastery.mastery_summary(source_ids=source_ids, folder_id=folder_id)
    return result


def _pick_bank_item(session_id: int, source_ids: list[int] | None) -> int | None:
    """Next unseen bank item for test/cumulative mode."""
    with session_scope() as session:
        answered = select(QuizAttempt.quiz_item_id).where(QuizAttempt.session_id == session_id)
        stmt = select(QuizItem.id).where(
            QuizItem.ephemeral.is_(False), QuizItem.id.not_in(answered)
        )
        if source_ids:
            stmt = stmt.where(QuizItem.source_id.in_(source_ids))
        return session.execute(stmt.order_by(func.random()).limit(1)).scalar_one_or_none()


def _finish_session(session_id: int) -> None:
    with session_scope() as session:
        row = session.get(QuizSession, session_id)
        if row is not None and row.status is QuizSessionStatus.ACTIVE:
            row.status = QuizSessionStatus.FINISHED
            row.finished_at = utcnow()


@router.post("/session/{session_id}/answer")
async def submit_answer(session_id: int, payload: AnswerRequest) -> dict[str, Any]:
    def load_item() -> QuizItem:
        with session_scope() as session:
            item = session.get(QuizItem, payload.item_id)
            if item is None:
                raise NotFoundError(f"Question {payload.item_id} not found.")
            session.expunge(item)
            return item

    item = await run_db(load_item)
    grade = await quiz_gen.grade_answer(item, payload.response)

    def persist() -> tuple[dict[str, Any], bool]:
        with session_scope() as session:
            row = session.get(QuizSession, session_id)
            is_test = row is not None and row.mode is QuizMode.TEST
            session.add(
                QuizAttempt(
                    session_id=session_id,
                    quiz_item_id=payload.item_id,
                    response=payload.response,
                    is_correct=grade.is_correct,
                    score=grade.score,
                    feedback=grade.feedback,
                )
            )
            return {"is_test": is_test}, is_test

    _, is_test = await run_db(persist)
    new_mastery = await run_db(
        mastery.record_attempt, item.concept_id, correct=grade.is_correct, score=grade.score
    )

    response: dict[str, Any] = {
        "item_id": payload.item_id,
        "score": round(grade.score, 3),
        "mastery": new_mastery,
    }
    # Test mode withholds feedback until submission (PRD §7.4).
    if not is_test:
        response.update(
            {
                "is_correct": grade.is_correct,
                "feedback": grade.feedback,
                "correct_answer": item.answer,
                "explanation": item.explanation,
                "segment_ids": list(item.segment_ids or []),
            }
        )
    return response


@router.get("/session/{session_id}/results")
async def session_results(session_id: int) -> dict[str, Any]:
    def gather() -> dict[str, Any]:
        with session_scope() as session:
            row = session.get(QuizSession, session_id)
            if row is None:
                raise NotFoundError(f"Quiz session {session_id} not found.")

            attempts = list(
                session.execute(
                    select(QuizAttempt, QuizItem)
                    .join(QuizItem, QuizItem.id == QuizAttempt.quiz_item_id)
                    .where(QuizAttempt.session_id == session_id)
                    .order_by(QuizAttempt.id)
                ).all()
            )
            source_ids = list(row.source_ids) or None
            folder_id = row.folder_id

            answered = len(attempts)
            correct = sum(1 for attempt, _ in attempts if attempt.is_correct)
            return {
                "session_id": session_id,
                "mode": str(row.mode),
                "status": str(row.status),
                "answered": answered,
                "correct": correct,
                "accuracy": round(correct / answered, 3) if answered else 0.0,
                "average_score": round(sum(a.score for a, _ in attempts) / answered, 3)
                if answered
                else 0.0,
                "items": [
                    {
                        "question": item.question,
                        "response": attempt.response,
                        "correct_answer": item.answer,
                        "is_correct": attempt.is_correct,
                        "score": round(attempt.score, 3),
                        "feedback": attempt.feedback,
                        "explanation": item.explanation,
                        "segment_ids": list(item.segment_ids or []),
                        "source_id": item.source_id,
                    }
                    for attempt, item in attempts
                ],
                "_scope": (source_ids, folder_id),
            }

    result = await run_db(gather)
    source_ids, folder_id = result.pop("_scope")

    # The weak-concept report, with links back to the source segments (PRD §7.4).
    weak = await run_db(mastery.weak_concepts, source_ids=source_ids, folder_id=folder_id, limit=10)
    result["weak_concepts"] = [
        {
            "id": concept.id,
            "name": concept.name,
            "definition": concept.definition,
            "mastery": round(concept.mastery, 3),
            "source_id": concept.source_id,
            "source_title": concept.source_title,
            "segment_ids": concept.segment_ids,
        }
        for concept in weak
    ]
    result["mastery"] = await run_db(
        mastery.mastery_summary, source_ids=source_ids, folder_id=folder_id
    )
    await run_db(_finish_session, session_id)
    return result


@router.get("/items")
def list_items(source_id: int, include_answers: bool = False) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(QuizItem)
                .where(QuizItem.source_id == source_id, QuizItem.ephemeral.is_(False))
                .order_by(QuizItem.id)
            ).scalars()
        )
    return {"items": [quiz_item_dict(item, include_answer=include_answers) for item in rows]}


@router.post("/reset-mastery")
async def reset_mastery(source_id: int) -> dict[str, Any]:
    count = await run_db(mastery.reset_mastery, source_id)
    return {"reset": count}
