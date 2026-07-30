"""Alembic environment.

Reads its database URL from :mod:`quicklearn.config` rather than from ``alembic.ini`` so that
the data directory resolved at boot — or overridden by a CLI flag or test fixture — is the one
migrated. Hard-coding a URL in the ini file would migrate the wrong database the moment anyone
passed ``--data-dir``.
"""

from __future__ import annotations

from alembic import context

from quicklearn.config import get_config
from quicklearn.db.models import Base
from quicklearn.db.session import create_db_engine

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_config().db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        # Standalone `alembic upgrade head` from the CLI.
        engine = create_db_engine()
        with engine.connect() as connection:
            _run(connection)
        engine.dispose()
    else:
        _run(connectable)


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render ``UTCDateTime`` as plain ``sa.DateTime`` in generated migrations.

    ``UTCDateTime`` is a ``TypeDecorator`` over ``DateTime``, so the emitted DDL is identical.
    Rendering the decorator by name would make every migration import application code, and a
    migration that depends on a module someone later renames stops being replayable.
    """
    if type_ == "type" and type(obj).__name__ == "UTCDateTime":
        return "sa.DateTime()"
    return False


def _run(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        # SQLite cannot ALTER most things in place; batch mode recreates-and-copies instead.
        render_as_batch=True,
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
