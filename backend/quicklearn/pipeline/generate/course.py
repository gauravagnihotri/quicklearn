"""Local patch: the Learn tab's guided course.

Modelled on the "teach" skill (github.com/mattpocock/skills, skills/productivity/teach). Notes
are something to read; a course is something to work through: a few short lessons, each with
one objective, a plain explanation, a worked example, retrieval checks with immediate feedback
and a small task to do away from the screen, then a reference sheet for coming back later.

Three passes, the same shape as notes:

* **Plan** — one call picks the lessons, their order, and which concepts and segments each owns.
* **Lessons** — one call per lesson, seeing only its own segments, persisted as each finishes
  so the tab fills in while the job runs.
* **Reference** — one call compresses the finished lessons into a cheat sheet. The glossary is
  not generated: it is the source's concepts.

The checks are stored as ``QuizItem`` rows tagged with ``lesson_id`` (and ``ephemeral`` so the
test bank and its regeneration ignore them), which means grading and the concept mastery
model are the quiz's own.
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import delete, select

from ... import prompts
from ...db.enums import QuizItemType
from ...db.models import Concept, Course, Lesson, QuizItem, Segment, Source
from ...db.session import session_scope
from ...providers import gateway
from ...providers.base import text_message
from ...settings import get_settings
from ..filter import filter_for_generation
from ..segment import format_timestamp
from ..schemas import CourseOutline, LessonCheckOut, LessonOut, ReferenceSheetOut
from .notes import _FIELD_ECHO

log = logging.getLogger(__name__)

MIN_LESSONS = 1
MAX_LESSONS = 8
CHECK_COUNT = 3
#: Characters of segment text shown per lesson; segments past it are left out.
LESSON_SEGMENT_BUDGET = 20_000
#: Characters of each segment shown to the planner.
PLAN_PREVIEW_CHARS = 160

_CITE = re.compile(r"(?<!\w)\[(\d+(?:\s*,\s*\d+)*)\](?!\()")
#: Code and maths, where brackets are indexing, literals or intervals rather than citations.
_PROTECTED = re.compile(r"(```.*?```|\$\$.*?\$\$|\$[^$\n]+\$|`[^`\n]+`)", re.S)


def _close_maths(line: str) -> str:
    """Close an inline ``$`` the model left open (it wrote ``$O(1)`` once), at the end of the
    word it opens, so the rest of the line is not swallowed as maths.

    Pairs left to right; as in KaTeX's auto-render, a ``$`` right after whitespace cannot
    close, so ``$O(1). Then $n$`` is read as an open ``$O(1).`` followed by ``$n$``.
    """
    if "$$" in line or line.count("$") % 2 == 0:
        return line
    positions = [m.start() for m in re.finditer(r"\$", line)]
    k = 0
    while k < len(positions):
        start = positions[k]
        if k + 1 < len(positions) and not line[positions[k + 1] - 1].isspace():
            k += 2
            continue
        match = re.match(r"\S*?(?=[.,;:!?]*(?:\s|$))", line[start + 1 :])
        end = start + 1 + (match.end() if match else 0)
        return line[:end] + "$" + line[end:]
    return line


def clean_citations(text: str, allowed: set[int]) -> str:
    """Keep ``[12]`` cites that point at the lesson's segments; drop made-up ones.

    ``[12, 15]`` becomes ``[12][15]``, the form the markdown renderer turns into buttons. A
    group with no known id is kept as written when it lists several numbers, since that is
    more likely a literal like ``[5, 6, 7]`` than a citation.
    """
    # Gemma 4 sometimes writes a visible return symbol before each newline inside JSON strings.
    text = _FIELD_ECHO.sub("", text.replace("\u23ce", ""))

    def fix(match: re.Match[str]) -> str:
        ids = [int(part) for part in re.split(r"\s*,\s*", match.group(1))]
        known = [i for i in ids if i in allowed]
        if known:
            return "".join(f"[{i}]" for i in known)
        return match.group(0) if len(ids) > 1 else ""

    text = "\n".join(_close_maths(line) for line in text.split("\n"))
    parts = _PROTECTED.split(text)
    for index in range(0, len(parts), 2):
        parts[index] = _CITE.sub(fix, parts[index])
    return re.sub(r"[ \t]+([.,;:])", r"\1", "".join(parts)).strip()


def _segment_payload(segments: Sequence[Segment], *, preview: int | None = None) -> list[dict]:
    return [
        {
            "id": segment.id,
            "text": segment.text if preview is None else segment.text[:preview],
            "timestamp": format_timestamp(segment.start_ms)
            if segment.start_ms is not None
            else (f"p.{segment.page_no}" if segment.page_no else ""),
        }
        for segment in segments
    ]


def _within_budget(segments: Sequence[Segment], budget: int) -> list[Segment]:
    kept: list[Segment] = []
    used = 0
    for segment in segments:
        if kept and used + len(segment.text) > budget:
            break
        kept.append(segment)
        used += len(segment.text)
    return kept


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _clean_checks(
    checks: Sequence[LessonCheckOut],
    *,
    concept_ids: list[int],
    mcq_options: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Validate the checks: MCQs first, then one short answer, at most ``CHECK_COUNT`` in all.

    The teach skill asks that the correct answer's position never be a clue. A per-question
    shuffle can still put every answer in the same slot, so the MCQs in a lesson take distinct
    positions drawn at random.
    """
    mcqs: list[dict[str, Any]] = []
    shorts: list[dict[str, Any]] = []
    for check in checks:
        question = check.question.strip()
        answer = check.answer.strip()
        if not question or not answer:
            continue
        concept_id = check.concept_id if check.concept_id in concept_ids else None
        row = {
            "type": QuizItemType.SHORT,
            "question": question,
            "options": [],
            "answer": answer,
            "explanation": check.explanation.strip(),
            "concept_id": concept_id or (concept_ids[0] if concept_ids else None),
        }
        if check.type != "mcq":
            shorts.append(row)
            continue
        # Drop distractors that are the answer again in other punctuation (Gemma wrote both
        # "It is $O(1)$." and "It is $O(1)." once).
        seen = {_normalise(answer)}
        distractors = []
        for option in (o.strip() for o in check.options):
            if option and _normalise(option) not in seen:
                seen.add(_normalise(option))
                distractors.append(option)
        if not distractors:
            continue
        row["type"] = QuizItemType.MCQ
        row["options"] = distractors[: mcq_options - 1]
        mcqs.append(row)

    mcqs = mcqs[: CHECK_COUNT - 1] if shorts else mcqs[:CHECK_COUNT]
    slots = list(range(mcq_options))
    rng.shuffle(slots)
    for index, row in enumerate(mcqs):
        distractors = row["options"]
        rng.shuffle(distractors)
        position = slots[index % len(slots)] % (len(distractors) + 1)
        row["options"] = [*distractors[:position], row["answer"], *distractors[position:]]
    return mcqs + shorts[: CHECK_COUNT - len(mcqs)]


def _load(source_id: int) -> tuple[str, str, list[Concept], list[Segment]]:
    threshold = get_settings().get_float("generation.relevance_threshold", 0.3)
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source {source_id} not found")
        course = session.execute(
            select(Course).where(Course.source_id == source_id)
        ).scalar_one_or_none()
        concepts = list(
            session.execute(
                select(Concept)
                .where(Concept.source_id == source_id)
                .order_by(Concept.importance.desc(), Concept.id)
            ).scalars()
        )
        segments = list(
            session.execute(
                select(Segment).where(Segment.source_id == source_id).order_by(Segment.index)
            ).scalars()
        )
        title = source.title
        mission = course.mission if course else ""
        session.expunge_all()
    return title, mission, concepts, filter_for_generation(segments, threshold)


async def plan_course(
    source_id: int,
    *,
    title: str,
    mission: str,
    concepts: list[Concept],
    segments: list[Segment],
    job_id: int | None = None,
) -> list[dict[str, Any]]:
    """Pass 1: the lesson plan, validated against the source's real concepts and segments."""
    max_lessons = max(MIN_LESSONS, min(MAX_LESSONS, len(concepts)))
    prompt = prompts.render(
        "course_outline",
        title=title,
        mission=mission.strip(),
        min_lessons=min(3, max_lessons),
        max_lessons=max_lessons,
        concepts=[
            {
                "id": c.id,
                "name": c.name,
                "definition": c.definition,
                "importance": c.importance,
                "segment_ids": c.segment_ids,
            }
            for c in concepts
        ],
        segments=_segment_payload(segments[:300], preview=PLAN_PREVIEW_CHARS),
    )
    result = await gateway.complete_structured(
        "course plan",
        [text_message("user", prompt)],
        CourseOutline,
        task_class="standard",
        temperature=0.3,
        max_tokens=2048,
        source_id=source_id,
        job_id=job_id,
    )

    concept_by_id = {c.id: c for c in concepts}
    segment_ids = {s.id for s in segments}
    plans: list[dict[str, Any]] = []
    for plan in result.lessons[:max_lessons]:
        lesson_title = plan.title.strip()
        if not lesson_title:
            continue
        owned = [cid for cid in dict.fromkeys(plan.concept_ids) if cid in concept_by_id]
        cited = [sid for sid in dict.fromkeys(plan.segment_ids) if sid in segment_ids]
        if not cited:
            cited = [
                sid
                for cid in owned
                for sid in concept_by_id[cid].segment_ids or []
                if sid in segment_ids
            ]
        plans.append(
            {
                "title": lesson_title,
                "objective": plan.objective.strip()
                .removeprefix("By the end of this lesson you can")
                .strip(),
                "concept_ids": owned,
                "segment_ids": list(dict.fromkeys(cited)),
            }
        )
    if not plans:
        raise ValueError("The course plan came back empty.")
    return plans


async def write_lesson(
    lesson_id: int,
    *,
    number: int,
    plans: list[dict[str, Any]],
    title: str,
    mission: str,
    concepts: list[Concept],
    segments: list[Segment],
    source_id: int,
    job_id: int | None = None,
) -> None:
    """Pass 2: one lesson's text and checks, persisted straight away."""
    plan = plans[number - 1]
    concept_by_id = {c.id: c for c in concepts}
    segment_by_id = {s.id: s for s in segments}
    lesson_segments = [segment_by_id[sid] for sid in plan["segment_ids"] if sid in segment_by_id]
    if not lesson_segments:
        # Without evidence of its own a lesson would be written from the model's memory.
        lesson_segments = segments
    lesson_segments = _within_budget(lesson_segments, LESSON_SEGMENT_BUDGET)
    lesson_concepts = [concept_by_id[cid] for cid in plan["concept_ids"]]
    mcq_options = get_settings().get_int("generation.mcq_options", 4)

    prompt = prompts.render(
        "lesson",
        title=title,
        mission=mission.strip(),
        lesson=plan,
        lesson_number=number,
        lesson_count=len(plans),
        outline=[{"title": p["title"]} for p in plans],
        concepts=[{"id": c.id, "name": c.name, "definition": c.definition} for c in lesson_concepts],
        segments=_segment_payload(lesson_segments),
        check_count=CHECK_COUNT,
        mcq_options=mcq_options,
    )
    result = await gateway.complete_structured(
        f"lesson '{plan['title']}'",
        [text_message("user", prompt)],
        LessonOut,
        task_class="standard",
        temperature=0.4,
        max_tokens=4096,
        source_id=source_id,
        job_id=job_id,
    )

    allowed = {s.id for s in lesson_segments}
    checks = _clean_checks(
        result.checks,
        concept_ids=plan["concept_ids"],
        mcq_options=mcq_options,
        rng=random.Random(f"{lesson_id}:{plan['title']}"),
    )
    with session_scope() as session:
        lesson = session.get(Lesson, lesson_id)
        if lesson is None:
            return  # the course was rebuilt while this lesson was being written
        lesson.why = clean_citations(result.why, set())
        lesson.body_md = clean_citations(result.body, allowed)
        lesson.example_md = clean_citations(result.example, allowed)
        lesson.try_it = clean_citations(result.try_it, set())
        lesson.segment_ids = sorted(allowed)
        session.execute(delete(QuizItem).where(QuizItem.lesson_id == lesson_id))
        for check in checks:
            concept = concept_by_id.get(check["concept_id"]) if check["concept_id"] else None
            cites = [sid for sid in (concept.segment_ids if concept else []) if sid in allowed]
            session.add(
                QuizItem(
                    source_id=source_id,
                    lesson_id=lesson_id,
                    ephemeral=True,
                    difficulty=2,
                    segment_ids=cites or sorted(allowed)[:3],
                    **check,
                )
            )


async def write_reference(
    course_id: int, *, title: str, source_id: int, job_id: int | None = None
) -> None:
    """Pass 3: the cheat sheet, compressed from the lessons as written."""
    with session_scope() as session:
        lessons = [
            {"title": lesson.title, "body": lesson.body_md, "example": lesson.example_md}
            for lesson in session.execute(
                select(Lesson).where(Lesson.course_id == course_id).order_by(Lesson.index)
            ).scalars()
            if lesson.body_md
        ]
    if not lessons:
        return
    result = await gateway.complete_structured(
        "reference sheet",
        [text_message("user", prompts.render("course_reference", title=title, lessons=lessons))],
        ReferenceSheetOut,
        task_class="standard",
        temperature=0.2,
        max_tokens=3072,
        source_id=source_id,
        job_id=job_id,
    )
    with session_scope() as session:
        course = session.get(Course, course_id)
        if course is not None:
            parts = []
            for group in result.groups:
                body = clean_citations(group.body, set())
                label = group.label.strip().strip("*").strip()[:80]
                if body:
                    parts.append(f"**{label}**\n\n{body}" if label else body)
            course.reference_md = "\n\n".join(parts)


async def generate_course(
    source_id: int,
    *,
    job_id: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Plan, write and persist a source's course. Returns the course id.

    Rebuilding keeps the mission but replaces every lesson, and with them their checks and the
    answers given to those checks.
    """
    title, mission, concepts, segments = _load(source_id)
    if not concepts:
        raise ValueError("This source has no concepts to build a course from.")
    if not segments:
        raise ValueError("No content available to build a course from.")

    plans = await plan_course(
        source_id, title=title, mission=mission, concepts=concepts, segments=segments, job_id=job_id
    )
    total = len(plans) + 1

    with session_scope() as session:
        course = session.execute(
            select(Course).where(Course.source_id == source_id)
        ).scalar_one_or_none()
        if course is None:
            course = Course(source_id=source_id, mission=mission)
            session.add(course)
            session.flush()
        session.execute(delete(Lesson).where(Lesson.course_id == course.id))
        course.reference_md = ""
        rows = [
            Lesson(
                course_id=course.id,
                index=index,
                title=plan["title"],
                objective=plan["objective"],
                concept_ids=plan["concept_ids"],
                segment_ids=plan["segment_ids"],
            )
            for index, plan in enumerate(plans)
        ]
        session.add_all(rows)
        session.flush()
        course_id = course.id
        lesson_ids = [row.id for row in rows]

    failures = 0
    for number, lesson_id in enumerate(lesson_ids, start=1):
        if on_progress:
            on_progress(number - 1, total, f"Writing lesson {number} of {len(plans)}")
        try:
            await write_lesson(
                lesson_id,
                number=number,
                plans=plans,
                title=title,
                mission=mission,
                concepts=concepts,
                segments=segments,
                source_id=source_id,
                job_id=job_id,
            )
        except Exception:
            # One bad lesson should not cost the others; the tab says it is missing.
            log.exception("lesson %d of source %s failed", number, source_id)
            failures += 1
    if failures == len(lesson_ids):
        raise ValueError("No lesson could be written. Check the language model in Settings.")

    if on_progress:
        on_progress(total - 1, total, "Writing the reference sheet")
    try:
        await write_reference(course_id, title=title, source_id=source_id, job_id=job_id)
    except Exception:
        log.exception("reference sheet for source %s failed", source_id)

    log.info("built a %d-lesson course for source %s", len(lesson_ids), source_id)
    return course_id
