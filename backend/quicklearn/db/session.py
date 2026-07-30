"""Engine construction, connection pragmas and session helpers.

Deliberately synchronous. SQLite is a local file, so async access buys no I/O concurrency —
``aiosqlite`` just relays calls to a thread — while costing the ability to load a C extension
on connect. More importantly, the sync design pushes pipeline stages into a
*read → do async provider work → write* shape, so a transaction is never held open across a
30-second inference call.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import Config, get_config

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

#: Whether the sqlite-vec extension loaded. When false the RAG layer transparently falls back
#: to brute-force cosine in numpy — see :mod:`quicklearn.rag.index`.
VEC_AVAILABLE = False


def vec_available() -> bool:
    return VEC_AVAILABLE


def _configure_connection(dbapi_conn: Any, _record: Any) -> None:
    """Per-connection setup: load sqlite-vec, then apply pragmas."""
    global VEC_AVAILABLE

    try:
        dbapi_conn.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(dbapi_conn)
        VEC_AVAILABLE = True
    except Exception as exc:  # pragma: no cover - depends on platform wheels
        # Not fatal. Vector search degrades to the numpy path; everything else is unaffected.
        log.warning("sqlite-vec unavailable (%s); using numpy fallback for vector search", exc)
    finally:
        with contextlib.suppress(Exception):
            dbapi_conn.enable_load_extension(False)

    cur = dbapi_conn.cursor()
    try:
        # WAL lets the API read while the worker writes — the whole reason a single SQLite
        # file can serve as both database and job queue.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # Worker tasks and request threads do contend. Without this, a concurrent write
        # raises "database is locked" instead of waiting the moment or two it needs.
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA temp_store=MEMORY")
    finally:
        cur.close()


def create_db_engine(config: Config | None = None, *, echo: bool = False) -> Engine:
    cfg = config or get_config()
    engine = create_engine(
        cfg.db_url,
        echo=echo,
        future=True,
        # Connections cross threads because DB work runs in the threadpool.
        connect_args={"check_same_thread": False, "timeout": 15.0},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _configure_connection)
    return engine


def init_engine(config: Config | None = None) -> Engine:
    global _engine, _session_factory
    cfg = config or get_config()
    if _engine is not None:
        _engine.dispose()
    _engine = create_db_engine(cfg)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return init_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


def new_session() -> Session:
    return get_session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session. Commits on success, rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def raw_sqlite() -> Iterator[sqlite3.Connection]:
    """The underlying sqlite3 connection.

    Needed for the sqlite-vec and FTS5 virtual tables, which have no ORM representation.
    The connection is returned to the pool, not closed.
    """
    fairy = get_engine().raw_connection()
    try:
        conn: sqlite3.Connection = fairy.driver_connection  # type: ignore[assignment]
        yield conn
        fairy.commit()
    except Exception:
        fairy.rollback()
        raise
    finally:
        fairy.close()


def dispose_engine() -> None:
    """Release all pooled connections. Used at shutdown and between tests."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
