"""Database layer: models, session management, migrations and the search index."""

from .session import get_engine, new_session, raw_sqlite, session_scope, vec_available

__all__ = ["get_engine", "new_session", "raw_sqlite", "session_scope", "vec_available"]
