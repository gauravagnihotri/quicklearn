"""FTS5 full-text search index.

One index covers everything searchable — note text, transcript segments, card faces, concept
definitions and source titles — so ``Cmd+K`` is a single query rather than a union of five.

Built on FTS5's *external content* pattern: a plain ``search_docs`` table holds the rows and
owns a ``(kind, entity_id)`` unique key, while ``search_fts`` indexes it and is kept in sync by
triggers. Triggers rather than application-level syncing because an index that can silently
drift out of date is worse than no index — a user who searches and gets nothing concludes the
content isn't there.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

SearchKind = Literal["note", "segment", "card", "concept", "source"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_docs (
    id         INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL,
    entity_id  INTEGER NOT NULL,
    source_id  INTEGER,
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL DEFAULT '',
    UNIQUE(kind, entity_id)
);

CREATE INDEX IF NOT EXISTS ix_search_docs_source ON search_docs(source_id);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    title,
    body,
    content='search_docs',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO search_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
"""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


@dataclass(slots=True)
class SearchHit:
    kind: SearchKind
    entity_id: int
    source_id: int | None
    title: str
    snippet: str
    #: FTS5 BM25. Negative, and more negative means a better match.
    score: float


def upsert(
    conn: sqlite3.Connection,
    kind: SearchKind,
    entity_id: int,
    *,
    title: str = "",
    body: str = "",
    source_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO search_docs (kind, entity_id, source_id, title, body)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(kind, entity_id) DO UPDATE SET
            source_id = excluded.source_id,
            title     = excluded.title,
            body      = excluded.body
        """,
        (kind, entity_id, source_id, title, body),
    )


def upsert_many(
    conn: sqlite3.Connection,
    kind: SearchKind,
    rows: Iterable[tuple[int, str, str, int | None]],
) -> None:
    """Bulk variant. ``rows`` is ``(entity_id, title, body, source_id)``."""
    conn.executemany(
        """
        INSERT INTO search_docs (kind, entity_id, title, body, source_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(kind, entity_id) DO UPDATE SET
            title = excluded.title, body = excluded.body, source_id = excluded.source_id
        """,
        [(kind, eid, title, body, sid) for eid, title, body, sid in rows],
    )


def delete(conn: sqlite3.Connection, kind: SearchKind, entity_id: int) -> None:
    conn.execute("DELETE FROM search_docs WHERE kind = ? AND entity_id = ?", (kind, entity_id))


def delete_kind_for_source(conn: sqlite3.Connection, kind: SearchKind, source_id: int) -> None:
    conn.execute("DELETE FROM search_docs WHERE kind = ? AND source_id = ?", (kind, source_id))


def delete_source(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM search_docs WHERE source_id = ?", (source_id,))


def escape_query(raw: str, *, any_terms: bool = False) -> str:
    """Turn user input into a safe FTS5 MATCH expression.

    FTS5 has its own query syntax, and characters like ``"``, ``*``, ``:``, ``^``, ``(``, ``-``
    are operators. A user typing ``C++ (advanced)`` into a search box means those literally and
    would otherwise get a bare ``fts5: syntax error`` instead of results. Each run of
    alphanumerics becomes one quoted token, and a trailing ``*`` gives prefix matching on the
    final word so search feels responsive as you type.

    Local patch: ``any_terms`` ORs the tokens instead, for chat retrieval. A typed question
    ("what are static vs dynamic arrays") almost never has every word in one passage, so the
    implicit AND matched nothing and chat silently ran on vector search alone. BM25 already
    ranks passages matching more, and rarer, words first.
    """
    tokens = []
    current: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in {"_", "'"}:
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))

    if not tokens:
        return ""
    if any_terms:
        return " OR ".join(f'"{t}"' for t in tokens)
    quoted = [f'"{t}"' for t in tokens[:-1]]
    # Prefix-match the last token only if it looks like a word being typed.
    last = tokens[-1]
    quoted.append(f'"{last}"*' if len(last) >= 2 else f'"{last}"')
    return " ".join(quoted)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    kinds: Sequence[SearchKind] | None = None,
    source_ids: Sequence[int] | None = None,
    limit: int = 50,
    any_terms: bool = False,
) -> list[SearchHit]:
    match_expr = escape_query(query, any_terms=any_terms)
    if not match_expr:
        return []

    sql = [
        """
        SELECT d.kind, d.entity_id, d.source_id, d.title,
               snippet(search_fts, 1, '<mark>', '</mark>', '…', 24) AS snip,
               bm25(search_fts, 4.0, 1.0) AS score
        FROM search_fts
        JOIN search_docs d ON d.id = search_fts.rowid
        WHERE search_fts MATCH ?
        """
    ]
    params: list[object] = [match_expr]

    if kinds:
        sql.append(f"AND d.kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    if source_ids:
        sql.append(f"AND d.source_id IN ({','.join('?' * len(source_ids))})")
        params.extend(source_ids)

    sql.append("ORDER BY score LIMIT ?")
    params.append(limit)

    try:
        rows = conn.execute(" ".join(sql), params).fetchall()
    except sqlite3.OperationalError:
        # Defensive: escape_query should make this unreachable, but a search box that
        # 500s is worse than one that returns nothing.
        return []

    return [
        SearchHit(
            kind=row[0],
            entity_id=row[1],
            source_id=row[2],
            title=row[3] or "",
            snippet=row[4] or "",
            score=float(row[5]),
        )
        for row in rows
    ]
