"""Process-level configuration and the ``/data`` directory layout.

Two distinct kinds of configuration exist in quicklearn and it is worth keeping them apart:

* **This module** — where things live and how the server binds. Needed before the database
  exists, so it comes from CLI flags and environment variables only.
* :mod:`quicklearn.settings` — provider keys, model routing, generation preferences. Lives in
  the ``settings`` table so that everything is configurable from the UI and no file ever needs
  hand-editing (PRD §2.3). Environment variables may *override* those, for CI and power users,
  but are never required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "QUICKLEARN_"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"{ENV_PREFIX}{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def default_data_dir() -> Path:
    """Resolve the data directory.

    ``/data`` is the container mount point, so it wins when it exists and is writable. Outside
    a container that check fails harmlessly and we fall back to the user's home directory.
    """
    explicit = _env("DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    container_mount = Path("/data")
    if container_mount.is_dir() and os.access(container_mount, os.W_OK):
        return container_mount
    return (Path.home() / "quicklearn").resolve()


@dataclass(slots=True)
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1") or "127.0.0.1")
    port: int = field(default_factory=lambda: _env_int("PORT", 8080))
    #: Number of asyncio worker tasks draining the jobs table.
    worker_concurrency: int = field(default_factory=lambda: _env_int("WORKERS", 2))
    #: Bound on parallel LLM calls during map-reduce note generation and per-line TTS.
    max_parallel_llm: int = field(default_factory=lambda: _env_int("MAX_PARALLEL_LLM", 4))
    reload: bool = field(default_factory=lambda: _env_bool("RELOAD", False))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "info") or "info")
    #: Allows the frontend dev server (Vite on 5173) to talk to the API in development.
    dev_cors: bool = field(default_factory=lambda: _env_bool("DEV_CORS", False))

    # --- derived paths ---
    @property
    def db_path(self) -> Path:
        return self.data_dir / "quicklearn.db"

    @property
    def db_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path.as_posix()}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / ".secret"

    @property
    def static_dir(self) -> Path:
        """Compiled SPA. Ships at ``static/`` inside the image; in a source checkout the Vite
        build output one level up is used instead."""
        packaged = Path(__file__).parent / "static"
        if (packaged / "index.html").exists():
            return packaged
        return (Path(__file__).parent.parent.parent / "frontend" / "dist").resolve()

    def media_dir_for(self, source_id: int) -> Path:
        return self.media_dir / str(source_id)

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.media_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        # fastembed downloads its ONNX weights here rather than into the user's home cache,
        # keeping "delete one directory for a clean uninstall" true.
        os.environ.setdefault("HF_HOME", str(self.cache_dir / "hf"))
        os.environ.setdefault("FASTEMBED_CACHE_PATH", str(self.cache_dir / "fastembed"))


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Install a configuration. Used by the CLI entrypoint and by tests."""
    global _config
    _config = config
