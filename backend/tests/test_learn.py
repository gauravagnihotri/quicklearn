"""Local patch: the Learn tab's course, end to end over the API with the mock provider."""

from __future__ import annotations

import httpx

from quicklearn.db.models import Concept, Lesson, QuizItem
from quicklearn.db.session import session_scope
from quicklearn.pipeline.generate.course import clean_citations

from .test_api import client, drain  # noqa: F401  (fixture)

CONTENT = (
    "A dynamic array stores its elements in a fixed-size block and keeps a separate capacity.\n\n"
    "When an append finds the block full, it allocates a block twice as large and copies every "
    "element across, which costs O(n) for that one append.\n\n"
    "Because the capacity doubles, the copies are rare: n appends cost O(n) in total, so each "
    "append is O(1) amortised.\n\n"
    "Inserting at the front still shifts every element and stays O(n), whatever the capacity."
)


async def _source_with_course(client: httpx.AsyncClient, mission: str = "") -> int:
    response = await client.post("/sources/text", json={"title": "Dynamic arrays", "content": CONTENT})
    source_id = response.json()["source_id"]
    await drain()
    response = await client.post(f"/sources/{source_id}/course", json={"mission": mission})
    assert response.status_code == 200, response.text
    await drain()
    return source_id


async def test_no_course_until_asked(client: httpx.AsyncClient) -> None:
    response = await client.post("/sources/text", json={"title": "T", "content": CONTENT})
    source_id = response.json()["source_id"]
    await drain()

    body = (await client.get(f"/sources/{source_id}/course")).json()
    assert body["course"] is None
    assert body["lessons"] == []
    assert body["glossary"], "the glossary comes from concepts and exists before any course"
    assert (await client.get(f"/sources/{source_id}")).json()["counts"]["lessons"] == 0


async def test_build_course_writes_lessons_checks_and_reference(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client, mission="Pass my data structures exam")

    body = (await client.get(f"/sources/{source_id}/course")).json()
    assert body["course"]["mission"] == "Pass my data structures exam"
    assert body["course"]["reference_md"]
    lessons = body["lessons"]
    assert lessons
    assert [lesson["index"] for lesson in lessons] == list(range(len(lessons)))
    assert body["next_lesson_id"] == lessons[0]["id"]
    assert (await client.get(f"/sources/{source_id}")).json()["counts"]["lessons"] == len(lessons)

    segment_ids = {
        s["id"] for s in (await client.get(f"/sources/{source_id}/segments")).json()["segments"]
    }
    for lesson in lessons:
        assert lesson["body_md"]
        assert lesson["status"] == "todo"
        assert set(lesson["segment_ids"]) <= segment_ids
        assert lesson["checks"], "every lesson ends in retrieval practice"
        for check in lesson["checks"]:
            assert "answer" not in check, "answers stay on the server until a check is answered"
            assert check["result"] is None
            if check["type"] == "mcq":
                assert len(check["options"]) >= 2


async def test_lesson_checks_stay_out_of_the_quiz_bank(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client)
    bank = (await client.get("/quiz/items", params={"source_id": source_id})).json()["items"]
    with session_scope() as session:
        lesson_items = session.query(QuizItem).filter(QuizItem.lesson_id.is_not(None)).count()
    assert lesson_items
    assert all(item.get("lesson_id") is None for item in bank)

    # Regenerating the bank must not delete the lessons' checks.
    await client.post(f"/sources/{source_id}/regenerate", json={"target": "quiz"})
    await drain()
    with session_scope() as session:
        assert session.query(QuizItem).filter(QuizItem.lesson_id.is_not(None)).count() == lesson_items


async def test_answering_every_check_completes_the_lesson(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client)
    lesson = (await client.get(f"/sources/{source_id}/course")).json()["lessons"][0]

    with session_scope() as session:
        answers = {
            item.id: (item.answer, item.concept_id)
            for item in session.query(QuizItem).filter(QuizItem.lesson_id == lesson["id"])
        }
        before = {c.id: c.mastery for c in session.query(Concept)}

    results = []
    for check in lesson["checks"]:
        answer, _ = answers[check["id"]]
        response = await client.post(
            f"/lessons/{lesson['id']}/answer", json={"item_id": check["id"], "response": answer}
        )
        assert response.status_code == 200, response.text
        results.append(response.json())

    assert all("feedback" in r and "correct_answer" in r for r in results)
    assert [r["lesson_completed"] for r in results][-1] is True
    assert not any(r["lesson_completed"] for r in results[:-1])

    after = (await client.get(f"/sources/{source_id}/course")).json()
    done = next(row for row in after["lessons"] if row["id"] == lesson["id"])
    assert done["completed_at"] is not None
    assert all(check["result"] is not None for check in done["checks"])
    assert after["next_lesson_id"] != lesson["id"]

    mcq_concepts = {
        answers[c["id"]][1] for c in lesson["checks"] if c["type"] == "mcq" and answers[c["id"]][1]
    }
    with session_scope() as session:
        for concept_id in mcq_concepts:
            assert session.get(Concept, concept_id).mastery > before[concept_id]


async def test_warmup_draws_on_earlier_lessons_only(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client)
    lessons = (await client.get(f"/sources/{source_id}/course")).json()["lessons"]

    first = (await client.get(f"/lessons/{lessons[0]['id']}/warmup")).json()
    assert first["items"] == []

    if len(lessons) > 1:
        later = lessons[-1]
        with session_scope() as session:
            earlier_concepts = {
                cid
                for row in session.query(Lesson).filter(Lesson.index < later["index"])
                for cid in row.concept_ids
            }
        items = (await client.get(f"/lessons/{later['id']}/warmup")).json()["items"]
        for item in items:
            assert item["concept_id"] in earlier_concepts
            assert item["from_lesson"]["index"] < later["index"]
            assert "answer" not in item


async def test_rebuild_keeps_mission_and_replaces_lessons(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client, mission="Interview prep")
    lesson = (await client.get(f"/sources/{source_id}/course")).json()["lessons"][0]
    check = lesson["checks"][0]
    await client.post(f"/lessons/{lesson['id']}/answer", json={"item_id": check["id"], "response": "x"})

    await client.post(f"/sources/{source_id}/course", json={})
    await drain()
    body = (await client.get(f"/sources/{source_id}/course")).json()
    assert body["course"]["mission"] == "Interview prep"
    assert body["lessons"]
    # SQLite reuses row ids, so "replaced" shows as progress starting over.
    assert all(c["result"] is None for row in body["lessons"] for c in row["checks"])


async def test_deleting_the_source_removes_the_course(client: httpx.AsyncClient) -> None:
    source_id = await _source_with_course(client)
    await client.delete(f"/sources/{source_id}")
    with session_scope() as session:
        assert session.query(Lesson).count() == 0
        assert session.query(QuizItem).filter(QuizItem.lesson_id.is_not(None)).count() == 0


def test_clean_citations_keeps_real_cites_and_code() -> None:
    allowed = {3, 12, 15}
    assert clean_citations("Grows [12]. Made up [99].", allowed) == "Grows [12]. Made up."
    assert clean_citations("Both [12, 15].", allowed) == "Both [12][15]."
    assert clean_citations("Literal [5, 6, 7] and arr[3].", allowed) == "Literal [5, 6, 7] and arr[3]."
    assert clean_citations("```python\nx[99]\n```", allowed) == "```python\nx[99]\n```"
    assert clean_citations("Echo segment_ids: [3] here", allowed) == "Echo here"
    assert clean_citations("One [3]. \u23ce\nTwo.", allowed) == "One [3]. \nTwo."
    assert clean_citations("- Pop: $O(1)\n- Push: $O(1)$", allowed) == "- Pop: $O(1)$\n- Push: $O(1)$"
    assert clean_citations("Is $O(1). Then $n$ grows.", allowed) == "Is $O(1)$. Then $n$ grows."
    assert clean_citations("$$x^2$$ and $y$", allowed) == "$$x^2$$ and $y$"


def test_clean_checks_caps_dedupes_and_spreads_answers() -> None:
    import random

    from quicklearn.pipeline.generate.course import _clean_checks
    from quicklearn.pipeline.schemas import LessonCheckOut

    def mcq(n: int, extra: list[str] | None = None) -> LessonCheckOut:
        return LessonCheckOut(
            type="mcq", concept_id=1, question=f"Q{n}?", answer="It is $O(1)$.",
            options=["It is $O(1)$.", "It is $O(1).", "It is $O(n)$.", "It is $O(n^2)$.", *(extra or [])],
        )

    short = LessonCheckOut(type="short", concept_id=9, question="Explain.", answer="Because.")
    rows = _clean_checks(
        [mcq(1), mcq(2), mcq(3), short, short], concept_ids=[1, 2], mcq_options=4, rng=random.Random(0)
    )
    assert [str(r["type"]) for r in rows] == ["mcq", "mcq", "short"]
    for row in rows[:2]:
        assert row["options"].count("It is $O(1)$.") == 1
        assert "It is $O(1)." not in row["options"], "near-duplicate of the answer is dropped"
    positions = [row["options"].index(row["answer"]) for row in rows[:2]]
    assert len(set(positions)) == 2, "answers sit in different positions"
    assert rows[2]["concept_id"] == 1, "unknown concept ids fall back to the lesson's own"


def test_review_status_needs_a_poor_first_try_or_slipped_mastery() -> None:
    import datetime as dt

    from quicklearn.api.learn import _status

    done = Lesson(title="t", body_md="b", completed_at=dt.datetime.now(dt.UTC), score=0.67)
    assert _status(done, 0.33) == "done", "a just-finished lesson is not flagged"
    assert _status(done, 0.1) == "review"
    done.score = 0.33
    assert _status(done, 0.6) == "review"
    assert _status(Lesson(title="t", body_md=""), None) == "missing"
    assert _status(Lesson(title="t", body_md="b"), None) == "todo"
