"""Vector index over segments, with two interchangeable backends.

``sqlite-vec`` is a loadable C extension, and whether it loads depends on the platform wheel.
Betting the entire chat and semantic-search feature on that succeeding would be careless, so the
index sits behind one seam with a pure-numpy fallback. At this corpus size the fallback is
genuinely fine — brute-force cosine over ten thousand 384-dimensional vectors is a few
milliseconds and about 15 MB of RAM — so the degradation is in scaling headroom, not in
behaviour the user can perceive.

Embedding width is recorded in settings. Changing embedding provider changes the width, which
invalidates every stored vector; that is detected and forces a rebuild rather than producing
silently meaningless similarities.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select, update

from ..db.models import Segment
from ..db.session import raw_sqlite, session_scope, vec_available
from ..settings import get_settings

log = logging.getLogger(__name__)

DIM_SETTING = "rag.vector_dim"
BACKEND_SETTING = "rag.vector_backend"
EMBED_BATCH = 32


@dataclass(slots=True)
class VectorHit:
    segment_id: int
    #: Cosine *similarity* in 0..1 — higher is better. Backends convert their native metric.
    score: float


def _serialise(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialise(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class VectorIndex(Protocol):
    name: str

    def ensure(self, dimensions: int) -> None: ...
    def upsert(self, rows: Sequence[tuple[int, int, Sequence[float]]]) -> None: ...
    def search(
        self, vector: Sequence[float], *, k: int, source_ids: Sequence[int] | None = None
    ) -> list[VectorHit]: ...
    def delete_source(self, source_id: int) -> None: ...
    def clear(self) -> None: ...
    def count(self) -> int: ...


class Vec0Index:
    """sqlite-vec backend. Uses a metadata column so scoped search stays in the engine."""

    name = "sqlite-vec"

    def ensure(self, dimensions: int) -> None:
        with raw_sqlite() as conn:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS segment_vectors USING vec0(
                    segment_id INTEGER PRIMARY KEY,
                    source_id INTEGER,
                    embedding FLOAT[{dimensions}]
                )
                """
            )

    def upsert(self, rows: Sequence[tuple[int, int, Sequence[float]]]) -> None:
        if not rows:
            return
        with raw_sqlite() as conn:
            conn.executemany(
                "DELETE FROM segment_vectors WHERE segment_id = ?",
                [(segment_id,) for segment_id, _, _ in rows],
            )
            conn.executemany(
                "INSERT INTO segment_vectors(segment_id, source_id, embedding) VALUES (?, ?, ?)",
                [(sid, src, _serialise(vec)) for sid, src, vec in rows],
            )

    def search(
        self, vector: Sequence[float], *, k: int, source_ids: Sequence[int] | None = None
    ) -> list[VectorHit]:
        sql = [
            "SELECT segment_id, distance FROM segment_vectors",
            "WHERE embedding MATCH ? AND k = ?",
        ]
        params: list[object] = [_serialise(vector), k]
        if source_ids:
            sql.append(f"AND source_id IN ({','.join('?' * len(source_ids))})")
            params.extend(source_ids)
        sql.append("ORDER BY distance")

        with raw_sqlite() as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()

        # vec0 returns L2 distance over unit-length vectors, where d² = 2(1 − cos).
        return [
            VectorHit(segment_id=int(sid), score=max(0.0, 1.0 - (float(dist) ** 2) / 2.0))
            for sid, dist in rows
        ]

    def delete_source(self, source_id: int) -> None:
        # The table only exists once something has been indexed. Deleting a source that never
        # got that far is normal, not an error.
        with raw_sqlite() as conn, contextlib.suppress(sqlite3.OperationalError):
            conn.execute("DELETE FROM segment_vectors WHERE source_id = ?", (source_id,))

    def clear(self) -> None:
        with raw_sqlite() as conn:
            conn.execute("DROP TABLE IF EXISTS segment_vectors")

    def count(self) -> int:
        with raw_sqlite() as conn:
            try:
                return int(conn.execute("SELECT count(*) FROM segment_vectors").fetchone()[0])
            except Exception:
                return 0


class NumpyIndex:
    """Fallback backend: vectors in a normal table, cosine computed in numpy."""

    name = "numpy"

    def ensure(self, dimensions: int) -> None:
        with raw_sqlite() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS segment_vectors_blob (
                    segment_id INTEGER PRIMARY KEY,
                    source_id  INTEGER,
                    embedding  BLOB NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_segvec_source ON segment_vectors_blob(source_id)"
            )

    def upsert(self, rows: Sequence[tuple[int, int, Sequence[float]]]) -> None:
        if not rows:
            return
        with raw_sqlite() as conn:
            conn.executemany(
                """
                INSERT INTO segment_vectors_blob(segment_id, source_id, embedding)
                VALUES (?, ?, ?)
                ON CONFLICT(segment_id) DO UPDATE SET
                    source_id = excluded.source_id, embedding = excluded.embedding
                """,
                [(sid, src, _serialise(vec)) for sid, src, vec in rows],
            )

    def search(
        self, vector: Sequence[float], *, k: int, source_ids: Sequence[int] | None = None
    ) -> list[VectorHit]:
        import numpy as np

        sql = "SELECT segment_id, embedding FROM segment_vectors_blob"
        params: list[object] = []
        if source_ids:
            sql += f" WHERE source_id IN ({','.join('?' * len(source_ids))})"
            params.extend(source_ids)

        with raw_sqlite() as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            return []

        ids = np.array([r[0] for r in rows], dtype=np.int64)
        width = len(vector)
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
        if matrix.size % width != 0:
            log.error("stored vectors do not match the expected width; reindex required")
            return []
        matrix = matrix.reshape(-1, width)

        query = np.asarray(vector, dtype=np.float32)
        # Normalise rather than assuming unit vectors — not every provider returns them.
        denominator = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
        denominator[denominator == 0] = 1.0
        scores = (matrix @ query) / denominator

        top = np.argsort(-scores)[:k]
        return [VectorHit(segment_id=int(ids[i]), score=float(scores[i])) for i in top]

    def delete_source(self, source_id: int) -> None:
        with raw_sqlite() as conn, contextlib.suppress(sqlite3.OperationalError):
            conn.execute("DELETE FROM segment_vectors_blob WHERE source_id = ?", (source_id,))

    def clear(self) -> None:
        with raw_sqlite() as conn:
            conn.execute("DROP TABLE IF EXISTS segment_vectors_blob")

    def count(self) -> int:
        with raw_sqlite() as conn:
            try:
                return int(conn.execute("SELECT count(*) FROM segment_vectors_blob").fetchone()[0])
            except Exception:
                return 0


_index: VectorIndex | None = None


def get_index() -> VectorIndex:
    global _index
    if _index is None:
        _index = Vec0Index() if vec_available() else NumpyIndex()
        log.info("vector index backend: %s", _index.name)
    return _index


def set_index(index: VectorIndex | None) -> None:
    """Override the backend. Used by tests to exercise both paths identically."""
    global _index
    _index = index


def _prepare(dimensions: int) -> VectorIndex:
    """Ensure the index exists at the right width, rebuilding if the width changed."""
    index = get_index()
    settings = get_settings()
    stored_dim = settings.get_int(DIM_SETTING, 0)
    stored_backend = settings.get_str(BACKEND_SETTING, "")

    if stored_dim and stored_dim != dimensions:
        log.warning(
            "embedding width changed (%d → %d); clearing the vector index and reindexing",
            stored_dim,
            dimensions,
        )
        index.clear()
        with session_scope() as session:
            session.execute(update(Segment).values(embedded=False))
    elif stored_backend and stored_backend != index.name:
        # Backends store vectors in different tables; a switch means nothing is readable.
        log.warning("vector backend changed (%s → %s); reindexing", stored_backend, index.name)
        with session_scope() as session:
            session.execute(update(Segment).values(embedded=False))

    index.ensure(dimensions)
    if stored_dim != dimensions or stored_backend != index.name:
        settings.set_many({DIM_SETTING: dimensions, BACKEND_SETTING: index.name})
    return index


async def index_source(
    source_id: int,
    *,
    job_id: int | None = None,
    force: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Embed and index a source's segments. Returns the number indexed."""
    from ..providers import gateway, registry

    provider, _ = registry.get_embedding()
    index = _prepare(provider.dimensions)

    with session_scope() as session:
        stmt = select(Segment).where(Segment.source_id == source_id)
        if not force:
            stmt = stmt.where(Segment.embedded.is_(False))
        segments = list(session.execute(stmt.order_by(Segment.index)).scalars())

    if not segments:
        return 0

    batch_size = min(EMBED_BATCH, provider.max_batch)
    batches = [segments[i : i + batch_size] for i in range(0, len(segments), batch_size)]
    total = 0

    for batch_index, batch in enumerate(batches):
        vectors = await gateway.embed(
            [segment.text for segment in batch], source_id=source_id, job_id=job_id
        )
        if len(vectors) != len(batch):
            log.error(
                "embedding provider returned %d vectors for %d segments", len(vectors), len(batch)
            )
            continue

        index.upsert(
            [
                (segment.id, source_id, vector)
                for segment, vector in zip(batch, vectors, strict=True)
            ]
        )
        with session_scope() as session:
            session.execute(
                update(Segment)
                .where(Segment.id.in_([segment.id for segment in batch]))
                .values(embedded=True)
            )
        total += len(batch)
        if on_progress:
            on_progress(batch_index + 1, len(batches))

    log.info("indexed %d segments for source %s using %s", total, source_id, index.name)
    return total


def remove_source(source_id: int) -> None:
    get_index().delete_source(source_id)


async def search_vectors(
    query: str, *, k: int = 12, source_ids: Sequence[int] | None = None
) -> list[VectorHit]:
    from ..providers import gateway, registry

    provider, _ = registry.get_embedding()
    _prepare(provider.dimensions)
    vectors = await gateway.embed([query])
    if not vectors:
        return []
    return get_index().search(vectors[0], k=k, source_ids=source_ids)


def index_status() -> dict[str, object]:
    from sqlalchemy import func

    index = get_index()
    with session_scope() as session:
        total = int(session.execute(select(func.count()).select_from(Segment)).scalar_one())
        embedded = int(
            session.execute(
                select(func.count()).select_from(Segment).where(Segment.embedded.is_(True))
            ).scalar_one()
        )
    return {
        "backend": index.name,
        "vectors": index.count(),
        "segments": total,
        "embedded": embedded,
        "pending": max(0, total - embedded),
    }
