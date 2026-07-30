"""First-boot and every-boot database initialisation.

PRD §3.5: "The DB must self-initialize on first run with no user action." So migrations run
automatically at startup rather than being a documented step someone has to remember.

Responsibility is split deliberately:

* **Alembic** owns the declarative tables in :mod:`quicklearn.db.models`.
* **This module** owns the virtual tables and triggers — FTS5 and sqlite-vec — because Alembic
  autogenerate cannot see them and would otherwise propose dropping them on every revision.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from ..config import Config, get_config
from . import search
from .session import get_engine, init_engine, raw_sqlite

log = logging.getLogger(__name__)


def _alembic_config() -> AlembicConfig:
    root = Path(__file__).resolve().parent.parent.parent
    ini = root / "alembic.ini"
    cfg = AlembicConfig(str(ini)) if ini.exists() else AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    return cfg


def run_migrations() -> None:
    """Bring the schema to head, sharing the application engine's connection."""
    engine = get_engine()
    cfg = _alembic_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def create_virtual_tables() -> None:
    with raw_sqlite() as conn:
        search.create_schema(conn)


def init_database(config: Config | None = None) -> None:
    """Idempotent full initialisation. Safe to call on every boot."""
    cfg = config or get_config()
    cfg.ensure_dirs()
    init_engine(cfg)
    run_migrations()
    create_virtual_tables()
    log.info("database ready at %s", cfg.db_path)
