"""Quiz item generation and grading (PRD §7.4).

Two entry points with different lifetimes:

* :func:`generate_quiz_items` builds a persistent bank for a source.
* :func:`generate_adaptive_items` mints fresh items on demand for practice mode, targeting the
  lowest-mastery concepts. Those are marked ``ephemeral`` — resampling a fixed bank would let a
  student memorise the questions rather than the material, which is exactly the failure the
  "practice until it clicks" model is meant to avoid.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence

from sqlalchemy import delete, select

from ... import prompts
from ...db.enums import QuizItemType
from ...db.models import Concept, QuizItem, Source
from ...db.session import session_scope
from ...providers import gateway
from ...providers.base import text_message
from ...settings import get_settings
from ..schemas import GradeResult, QuizItemList

log = logging.getLogger(__name__)

CONCEPTS_PER_CALL = 8
PASS_THRESHOLD = 0.6


def _concept_payload(concepts: Sequence[Concept], *, with_mastery: bool = False) -> list[dict]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "definition": c.definition,
            "segment_ids": c.segment_ids,
            "mastery": c.mastery if with_mastery else None,
        }
        for c in concepts
    ]


def _neighbours(
    all_concepts: Sequence[Concept], target_ids: set[int], limit: int = 8
) -> list[dict]:
    """Concepts adjacent to the ones being tested, as a distractor pool.

    PRD §7.4 is explicit that MCQ distractors come from adjacent concepts rather than random
    ones. Random distractors are eliminable on sight and teach nothing; a neighbouring
    definition tests whether the student can actually tell two close ideas apart.
    """
    pool = [c for c in all_concepts if c.id not in target_ids]
    return [{"name": c.name, "definition": c.definition} for c in pool[:limit]]


async def generate_quiz_items(
    source_id: int,
    *,
    count: int = 20,
    types: Sequence[str] | None = None,
    job_id: int | None = None,
) -> list[int]:
    settings = get_settings()
    mcq_options = settings.get_int("generation.mcq_options", 4)

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
        session.expunge_all()

    if not concepts:
        return []

    valid_ids = {c.id for c in concepts}
    batches = [
        concepts[i : i + CONCEPTS_PER_CALL] for i in range(0, len(concepts), CONCEPTS_PER_CALL)
    ]
    per_batch = max(1, -(-count // len(batches)))
    collected: list[QuizItemOutRow] = []

    for batch in batches:
        prompt = prompts.render(
            "quiz_items",
            title=title,
            concepts=_concept_payload(batch),
            neighbours=_neighbours(concepts, {c.id for c in batch}),
            count=per_batch,
            mcq_options=mcq_options,
            requested_types=list(types) if types else None,
        )
        result = await gateway.complete_structured(
            "quiz items",
            [text_message("user", prompt)],
            QuizItemList,
            task_class="standard",
            temperature=0.4,
            source_id=source_id,
            job_id=job_id,
        )
        collected.extend(_clean_items(result, valid_ids, mcq_options))

    collected = collected[:count]
    if not collected:
        return []

    with session_scope() as session:
        session.execute(
            delete(QuizItem).where(QuizItem.source_id == source_id, QuizItem.ephemeral.is_(False))
        )
        session.flush()
        rows = [
            QuizItem(
                source_id=source_id,
                concept_id=item.concept_id,
                type=item.type,
                question=item.question,
                options=item.options,
                answer=item.answer,
                explanation=item.explanation,
                difficulty=item.difficulty,
                segment_ids=item.segment_ids,
                ephemeral=False,
            )
            for item in collected
        ]
        session.add_all(rows)
        session.flush()
        ids = [row.id for row in rows]

    log.info("generated %d quiz items for source %s", len(ids), source_id)
    return ids


class QuizItemOutRow:
    """Cleaned, storable quiz item."""

    __slots__ = (
        "answer",
        "concept_id",
        "difficulty",
        "explanation",
        "options",
        "question",
        "segment_ids",
        "type",
    )

    def __init__(
        self,
        *,
        type: QuizItemType,
        question: str,
        options: list[str],
        answer: str,
        explanation: str,
        difficulty: int,
        concept_id: int | None,
        segment_ids: list[int],
    ) -> None:
        self.type = type
        self.question = question
        self.options = options
        self.answer = answer
        self.explanation = explanation
        self.difficulty = difficulty
        self.concept_id = concept_id
        self.segment_ids = segment_ids


def _clean_items(
    result: QuizItemList, valid_concept_ids: set[int], mcq_options: int
) -> list[QuizItemOutRow]:
    cleaned: list[QuizItemOutRow] = []
    for item in result.items:
        question = item.question.strip()
        answer = item.answer.strip()
        if not question or not answer:
            continue

        options = [o.strip() for o in item.options if o.strip()]
        item_type = QuizItemType(item.type)

        if item_type is QuizItemType.MCQ:
            # An MCQ whose answer isn't among its options is unanswerable. Repair by
            # inserting it rather than discarding an otherwise good question.
            if answer not in options:
                options = [answer, *options][:mcq_options]
            if len(options) < 2:
                continue
            options = options[:mcq_options]
            if answer not in options:
                options[-1] = answer
            # Shuffle deterministically per question, so the answer isn't always first.
            random.Random(hash(question) & 0xFFFF).shuffle(options)
        else:
            options = []

        cleaned.append(
            QuizItemOutRow(
                type=item_type,
                question=question,
                options=options,
                answer=answer,
                explanation=item.explanation.strip(),
                difficulty=max(1, min(5, item.difficulty)),
                concept_id=item.concept_id if item.concept_id in valid_concept_ids else None,
                segment_ids=item.segment_ids,
            )
        )
    return cleaned


async def generate_adaptive_items(
    concept_ids: Sequence[int],
    *,
    count: int = 1,
    source_id: int | None = None,
) -> list[int]:
    """Mint fresh ephemeral items for the given concepts (practice mode)."""
    settings = get_settings()
    mcq_options = settings.get_int("generation.mcq_options", 4)

    with session_scope() as session:
        concepts = list(
            session.execute(select(Concept).where(Concept.id.in_(list(concept_ids)))).scalars()
        )
        if not concepts:
            return []
        owning_source = source_id or concepts[0].source_id
        siblings = list(
            session.execute(
                select(Concept).where(Concept.source_id == owning_source).limit(40)
            ).scalars()
        )
        title_row = session.get(Source, owning_source)
        title = title_row.title if title_row else ""
        session.expunge_all()

    prompt = prompts.render(
        "quiz_items",
        title=title,
        concepts=_concept_payload(concepts, with_mastery=True),
        neighbours=_neighbours(siblings, {c.id for c in concepts}),
        count=count,
        mcq_options=mcq_options,
        requested_types=None,
    )
    result = await gateway.complete_structured(
        "adaptive quiz item",
        [text_message("user", prompt)],
        QuizItemList,
        task_class="standard",
        # Higher temperature than the bank: practice mode should not keep producing the same
        # question for a concept the student is stuck on.
        temperature=0.8,
        source_id=owning_source,
    )

    cleaned = _clean_items(result, {c.id for c in concepts}, mcq_options)[:count]
    if not cleaned:
        return []

    with session_scope() as session:
        rows = [
            QuizItem(
                source_id=owning_source,
                concept_id=item.concept_id or concepts[0].id,
                type=item.type,
                question=item.question,
                options=item.options,
                answer=item.answer,
                explanation=item.explanation,
                difficulty=item.difficulty,
                segment_ids=item.segment_ids,
                ephemeral=True,
            )
            for item in cleaned
        ]
        session.add_all(rows)
        session.flush()
        return [row.id for row in rows]


def _normalise(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").split()).strip(" .!?\"'")


async def grade_answer(item: QuizItem, response: str) -> GradeResult:
    """Grade one answer.

    MCQ and cloze are deterministic — no reason to spend a model call, and no reason to risk a
    model disagreeing with an exact match. Short and free answers go to the LLM with a rubric
    that is lenient on phrasing and strict on substance.
    """
    response = response.strip()
    if not response:
        return GradeResult(score=0.0, is_correct=False, feedback="No answer given.")

    if item.type in {QuizItemType.MCQ, QuizItemType.CLOZE}:
        correct = _normalise(response) == _normalise(item.answer)
        return GradeResult(
            score=1.0 if correct else 0.0,
            is_correct=correct,
            feedback=item.explanation
            or ("Correct." if correct else f"The correct answer is: {item.answer}"),
        )

    prompt = prompts.render(
        "quiz_grade",
        item_type=str(item.type),
        question=item.question,
        reference=item.answer,
        response=response,
        pass_threshold=PASS_THRESHOLD,
    )
    try:
        result = await gateway.complete_structured(
            "grading",
            [text_message("user", prompt)],
            GradeResult,
            task_class="standard",
            temperature=0.0,
            source_id=item.source_id,
        )
    except Exception as exc:
        # Never lose a student's answer to a grading failure. Report it honestly as ungraded
        # rather than silently marking it wrong.
        log.warning("grading failed for item %s: %s", item.id, exc)
        return GradeResult(
            score=0.0,
            is_correct=False,
            feedback=f"This answer could not be graded automatically ({exc}). "
            f"Reference answer: {item.answer}",
        )

    # Keep the two fields consistent — a model occasionally returns score 0.9 with
    # is_correct false, and the mastery update reads is_correct.
    result.is_correct = result.score >= PASS_THRESHOLD
    return result
