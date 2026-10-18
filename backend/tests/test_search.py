"""Local patch: chat questions reach the keyword index even when not every word matches."""

from __future__ import annotations

import sqlite3

from quicklearn.db import search


def _index(*bodies: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    search.create_schema(conn)
    for entity_id, body in enumerate(bodies, start=1):
        search.upsert(conn, "segment", entity_id, title="", body=body, source_id=1)
    return conn


def test_search_box_still_requires_every_word() -> None:
    conn = _index("A static array has a fixed size.")
    assert search.search(conn, "static arrays vs linked lists") == []


def test_question_matches_on_any_word_ranked_by_relevance() -> None:
    conn = _index(
        "Today we cover the course outline.",
        "A static array has a fixed size; a dynamic array grows.",
        "Static typing is unrelated.",
    )
    hits = search.search(conn, "what are static vs dynamic arrays", any_terms=True)
    assert [hit.entity_id for hit in hits][:2] == [2, 3]
    assert 1 not in [hit.entity_id for hit in hits]


def test_any_terms_escapes_operators() -> None:
    conn = _index("C++ templates")
    hits = search.search(conn, 'C++ (advanced) "OR"', any_terms=True)
    assert [hit.entity_id for hit in hits] == [1]
