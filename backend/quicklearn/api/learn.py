"""Local patch: the Learn tab — a source's guided course, its checks and its warm-ups.

Answers go through the quiz's grader and the concept mastery model, so a lesson check moves
the same mastery the Concepts tab, practice mode and the dashboard show.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db.enums import JobType
from ..db.models import Concept, Course, Lesson, QuizAttempt, QuizItem, Segment, Source
from ..db.session import session_scope
from ..db.types import utcnow
from ..errors import NotFoundError, ValidationError
from ..jobs import queue as jq
from ..pipeline.generate import quiz as quiz_gen
from ..study import mastery
from .common import iso, quiz_item_dict, run_db

router = APIRouter(tags=["learn"])

WARMUP_COUNT = 2
#: A finished lesson is flagged for review when its first try scored below this...
REVIEW_SCORE_BELOW = 0.5
#: ...or its concepts' mastery has since fallen below this. Mastery starts at 0 and rises 30% of
#: the gap per correct answer, so a just-finished lesson sits around 0.3-0.5 and is not "slipping".
REVIEW_MASTERY_BELOW = 0.2


class BuildRequest(BaseModel):
    #: Why the learner is studying this. ``None`` keeps the saved mission.
    mission: str | None = Field(default=None, max_length=4000)


class MissionRequest(BaseModel):
    mission: str = Field(default="", max_length=4000)


class LessonAnswerRequest(BaseModel):
    item_id: int
    response: str = ""


def _check_dict(item: QuizItem, attempt: QuizAttempt | None) -> dict[str, Any]:
    payload = quiz_item_dict(item)
    payload["result"] = (
        {
            "response": attempt.response,
            "is_correct": attempt.is_correct,
            "score": round(attempt.score, 3),
            "feedback": attempt.feedback,
            "correct_answer": item.answer,
            "explanation": item.explanation,
        }
        if attempt
        else None
    )
    return payload


def _latest_attempts(session: Any, item_ids: list[int]) -> dict[int, QuizAttempt]:
    if not item_ids:
        return {}
    latest: dict[int, QuizAttempt] = {}
    for attempt in session.execute(
        select(QuizAttempt)
        .where(QuizAttempt.quiz_item_id.in_(item_ids))
        .order_by(QuizAttempt.attempted_at, QuizAttempt.id)
    ).scalars():
        latest[attempt.quiz_item_id] = attempt
    return latest


def _status(lesson: Lesson, lesson_mastery: float | None) -> str:
    if not lesson.body_md:
        return "missing"
    if lesson.completed_at is None:
        return "todo"
    if (lesson.score is not None and lesson.score < REVIEW_SCORE_BELOW) or (
        lesson_mastery is not None and lesson_mastery < REVIEW_MASTERY_BELOW
    ):
        return "review"
    return "done"


@router.get("/sources/{source_id}/course")
def get_course(source_id: int) -> dict[str, Any]:
    with session_scope() as session:
        if session.get(Source, source_id) is None:
            raise NotFoundError(f"Source {source_id} not found.")
        concepts = list(
            session.execute(
                select(Concept).where(Concept.source_id == source_id).order_by(Concept.name)
            ).scalars()
        )
        glossary = [{"id": c.id, "name": c.name, "definition": c.definition} for c in concepts]
        course = session.execute(
            select(Course).where(Course.source_id == source_id)
        ).scalar_one_or_none()
        if course is None:
            return {"course": None, "lessons": [], "glossary": glossary, "next_lesson_id": None}

        mastery_by_id = {c.id: c.mastery for c in concepts}
        lessons = list(course.lessons)
        items = list(
            session.execute(
                select(QuizItem)
                .where(QuizItem.lesson_id.in_([lesson.id for lesson in lessons]))
                .order_by(QuizItem.id)
            ).scalars()
        )
        attempts = _latest_attempts(session, [item.id for item in items])

        out: list[dict[str, Any]] = []
        for lesson in lessons:
            values = [mastery_by_id[cid] for cid in lesson.concept_ids if cid in mastery_by_id]
            lesson_mastery = round(sum(values) / len(values), 3) if values else None
            out.append(
                {
                    "id": lesson.id,
                    "index": lesson.index,
                    "title": lesson.title,
                    "objective": lesson.objective,
                    "why": lesson.why,
                    "body_md": lesson.body_md,
                    "example_md": lesson.example_md,
                    "try_it": lesson.try_it,
                    "concept_ids": list(lesson.concept_ids or []),
                    "segment_ids": list(lesson.segment_ids or []),
                    "completed_at": iso(lesson.completed_at),
                    "score": lesson.score,
                    "mastery": lesson_mastery,
                    "status": _status(lesson, lesson_mastery),
                    "checks": [
                        _check_dict(item, attempts.get(item.id))
                        for item in items
                        if item.lesson_id == lesson.id
                    ],
                }
            )

        # The zone of proximal development, crudely: the first lesson not yet done, else the
        # finished one whose concepts have slipped furthest.
        upcoming = next((lesson for lesson in out if lesson["status"] == "todo"), None)
        if upcoming is None:
            slipping = sorted(
                (lesson for lesson in out if lesson["status"] == "review"),
                key=lambda lesson: lesson["mastery"] or 0.0,
            )
            upcoming = slipping[0] if slipping else None

        return {
            "course": {
                "id": course.id,
                "mission": course.mission,
                "reference_md": course.reference_md,
                "updated_at": iso(course.updated_at),
            },
            "lessons": out,
            "glossary": glossary,
            "next_lesson_id": upcoming["id"] if upcoming else None,
        }


def _save_mission(source_id: int, mission: str | None) -> None:
    with session_scope() as session:
        if session.get(Source, source_id) is None:
            raise NotFoundError(f"Source {source_id} not found.")
        course = session.execute(
            select(Course).where(Course.source_id == source_id)
        ).scalar_one_or_none()
        if course is None:
            session.add(Course(source_id=source_id, mission=(mission or "").strip()))
        elif mission is not None:
            course.mission = mission.strip()


@router.put("/sources/{source_id}/course/mission")
async def save_mission(source_id: int, payload: MissionRequest) -> dict[str, Any]:
    await run_db(_save_mission, source_id, payload.mission)
    return {"mission": payload.mission.strip()}


@router.post("/sources/{source_id}/course")
async def build_course(source_id: int, payload: BuildRequest) -> dict[str, Any]:
    def has_content() -> bool:
        with session_scope() as session:
            return bool(
                session.execute(
                    select(func.count()).select_from(Segment).where(Segment.source_id == source_id)
                ).scalar_one()
            )

    await run_db(_save_mission, source_id, payload.mission)
    if not await run_db(has_content):
        raise ValidationError("This source has no content yet. Wait for processing to finish.")
    job_id = await run_db(
        jq.enqueue, JobType.GENERATE_COURSE, source_id=source_id, dedupe_key="course"
    )
    return {"job_id": job_id}


def _load_lesson(lesson_id: int) -> tuple[Lesson, int]:
    with session_scope() as session:
        lesson = session.get(Lesson, lesson_id)
        if lesson is None:
            raise NotFoundError(f"Lesson {lesson_id} not found.")
        source_id = lesson.course.source_id
        session.expunge(lesson)
        return lesson, source_id


@router.post("/lessons/{lesson_id}/answer")
async def answer_check(lesson_id: int, payload: LessonAnswerRequest) -> dict[str, Any]:
    """Grade a lesson check or a warm-up question, then update mastery.

    Feedback comes straight back: the teach skill wants the tightest possible loop. A lesson is
    complete once every check has an answer; its score is the mean of the first answers, so
    retrying a check after seeing the answer improves mastery but not the lesson's score.
    """
    _, source_id = await run_db(_load_lesson, lesson_id)

    def load_item() -> QuizItem:
        with session_scope() as session:
            item = session.get(QuizItem, payload.item_id)
            if item is None or item.source_id != source_id:
                raise NotFoundError(f"Question {payload.item_id} not found.")
            session.expunge(item)
            return item

    item = await run_db(load_item)
    grade = await quiz_gen.grade_answer(item, payload.response)

    def persist() -> bool:
        with session_scope() as session:
            session.add(
                QuizAttempt(
                    quiz_item_id=item.id,
                    response=payload.response,
                    is_correct=grade.is_correct,
                    score=grade.score,
                    feedback=grade.feedback,
                )
            )
            session.flush()
            row = session.get(Lesson, lesson_id)
            if row is None or row.completed_at is not None or item.lesson_id != lesson_id:
                return False
            check_ids = list(
                session.execute(select(QuizItem.id).where(QuizItem.lesson_id == lesson_id)).scalars()
            )
            first: dict[int, float] = {}
            for attempt in session.execute(
                select(QuizAttempt)
                .where(QuizAttempt.quiz_item_id.in_(check_ids))
                .order_by(QuizAttempt.attempted_at, QuizAttempt.id)
            ).scalars():
                first.setdefault(attempt.quiz_item_id, attempt.score)
            if not check_ids or len(first) < len(check_ids):
                return False
            row.completed_at = utcnow()
            row.score = round(sum(first.values()) / len(first), 3)
            return True

    completed = await run_db(persist)
    new_mastery = await run_db(
        mastery.record_attempt, item.concept_id, correct=grade.is_correct, score=grade.score
    )
    return {
        "item_id": item.id,
        "is_correct": grade.is_correct,
        "score": round(grade.score, 3),
        "feedback": grade.feedback,
        "correct_answer": item.answer,
        "explanation": item.explanation,
        "segment_ids": list(item.segment_ids or []),
        "mastery": new_mastery,
        "lesson_completed": completed,
    }


@router.get("/lessons/{lesson_id}/warmup")
def warmup(lesson_id: int) -> dict[str, Any]:
    """Questions on earlier lessons' concepts, weakest first: spaced, interleaved retrieval.

    Drawn from what already exists (the quiz bank first, then earlier lessons' checks), so
    opening a lesson never waits on the model.
    """
    with session_scope() as session:
        lesson = session.get(Lesson, lesson_id)
        if lesson is None:
            raise NotFoundError(f"Lesson {lesson_id} not found.")
        earlier = [row for row in lesson.course.lessons if row.index < lesson.index]
        lesson_of_concept: dict[int, Lesson] = {}
        for row in earlier:
            for cid in row.concept_ids or []:
                lesson_of_concept.setdefault(cid, row)
        if not lesson_of_concept:
            return {"items": []}

        weakest = list(
            session.execute(
                select(Concept)
                .where(Concept.id.in_(list(lesson_of_concept)))
                .order_by(Concept.mastery, func.random())
                .limit(WARMUP_COUNT)
            ).scalars()
        )
        rng = random.Random()
        items: list[dict[str, Any]] = []
        for concept in weakest:
            bank = list(
                session.execute(
                    select(QuizItem).where(
                        QuizItem.concept_id == concept.id,
                        QuizItem.ephemeral.is_(False),
                        QuizItem.type.in_(["mcq", "cloze", "short"]),
                    )
                ).scalars()
            )
            checks = list(
                session.execute(
                    select(QuizItem).where(
                        QuizItem.concept_id == concept.id,
                        QuizItem.lesson_id.in_([row.id for row in earlier]),
                    )
                ).scalars()
            )
            pool = bank or checks
            if not pool:
                continue
            item = rng.choice(pool)
            origin = lesson_of_concept[concept.id]
            payload = quiz_item_dict(item)
            payload["from_lesson"] = {"id": origin.id, "index": origin.index, "title": origin.title}
            payload["concept"] = {
                "id": concept.id,
                "name": concept.name,
                "mastery": round(concept.mastery, 3),
            }
            items.append(payload)
        return {"items": items}
