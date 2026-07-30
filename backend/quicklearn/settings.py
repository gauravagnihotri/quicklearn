"""User-facing settings, stored in the database rather than in files or environment variables.

PRD §4.3 is emphatic that provider config lives in a ``settings`` table and is edited through
the UI, because "zero configuration files require hand-editing" is a success criterion (§2.3).
Environment variables act purely as an *override* for CI and power users.

Resolution order for any key: **environment → database → default.** Environment wins so that a
CI run or a `docker run -e` invocation is authoritative and cannot be silently overridden by
whatever happens to be in a mounted volume's database.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, cast

from sqlalchemy import select

from .config import ENV_PREFIX, get_config
from .crypto import DecryptionError, SecretBox
from .db.models import Setting
from .db.session import session_scope

log = logging.getLogger(__name__)

#: Settings whose values are encrypted at rest.
SECRET_SUFFIX = ".api_key"

DEFAULTS: dict[str, Any] = {
    # Per-task model routing (PRD §4.3). Empty until the user configures a provider; the
    # registry raises an actionable error rather than guessing.
    "routing.fast": {"provider": "", "model": ""},
    "routing.standard": {"provider": "", "model": ""},
    "routing.deep": {"provider": "", "model": ""},
    "stt.provider": "",
    "stt.model": "",
    "stt.language": "",
    "stt.diarize": False,
    "tts.provider": "",
    "tts.voice_a": "",
    "tts.voice_b": "",
    # fastembed needs no key and downloads ~130MB on first use, so it can be the default and
    # still satisfy "works immediately" (PRD §4.2).
    "embedding.provider": "fastembed",
    "embedding.model": "BAAI/bge-small-en-v1.5",
    # Generation preferences
    "generation.relevance_threshold": 0.3,
    "generation.note_style": "detailed",
    "generation.max_cards_per_source": 50,
    "generation.mcq_options": 4,
    "generation.chunk_target_sec": 30,
    # FSRS
    "fsrs.desired_retention": 0.9,
    "fsrs.parameters": None,
    "fsrs.learning_steps_min": [1, 10],
    # RAG
    "rag.top_k": 12,
    "rag.rerank_to": 6,
    # UI / lifecycle
    "onboarding.completed": False,
    "ui.theme": "system",
}

#: Explicit env aliases, so the documented names stay short and stable.
ENV_ALIASES: dict[str, str] = {
    "providers.openai.api_key": "OPENAI_API_KEY",
    "providers.anthropic.api_key": "ANTHROPIC_API_KEY",
    "providers.google.api_key": "GOOGLE_API_KEY",
    "providers.groq.api_key": "GROQ_API_KEY",
    "providers.openrouter.api_key": "OPENROUTER_API_KEY",
    "providers.deepgram.api_key": "DEEPGRAM_API_KEY",
    "providers.elevenlabs.api_key": "ELEVENLABS_API_KEY",
    "providers.ollama.base_url": "OLLAMA_BASE_URL",
    "providers.lmstudio.base_url": "LMSTUDIO_BASE_URL",
    "stt.provider": "STT_PROVIDER",
    "tts.provider": "TTS_PROVIDER",
    "embedding.provider": "EMBEDDING_PROVIDER",
}


def _env_names(key: str) -> list[str]:
    names = []
    if key in ENV_ALIASES:
        names.append(f"{ENV_PREFIX}{ENV_ALIASES[key]}")
    names.append(f"{ENV_PREFIX}{key.upper().replace('.', '_')}")
    return names


def _coerce_from_env(raw: str, default: Any) -> Any:
    """Interpret an environment string against the shape of the default value."""
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    if isinstance(default, dict | list):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default
    return raw


class SettingsStore:
    """Thread-safe, cached accessor over the ``settings`` table."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._box: SecretBox | None = None

    # --- internals -----------------------------------------------------------------
    @property
    def box(self) -> SecretBox:
        if self._box is None:
            self._box = SecretBox.from_path(get_config().secret_path)
        return self._box

    def _load(self) -> dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return self._cache
            data: dict[str, Any] = {}
            with session_scope() as session:
                for row in session.execute(select(Setting)).scalars():
                    if row.encrypted and isinstance(row.value, str):
                        try:
                            data[row.key] = self.box.decrypt(row.value)
                        except DecryptionError as exc:
                            # Don't take down the whole settings page because one key is
                            # unreadable — surface it as absent so the UI prompts for re-entry.
                            log.warning("dropping unreadable setting %s: %s", row.key, exc)
                            data[row.key] = ""
                    else:
                        data[row.key] = row.value
            self._cache = data
            return data

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    # --- reads ---------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        fallback = DEFAULTS.get(key, default)
        for env_name in _env_names(key):
            raw = os.environ.get(env_name)
            if raw is not None and raw != "":
                return _coerce_from_env(raw, fallback)
        stored = self._load().get(key)
        if stored is None or stored == "":
            return fallback
        return stored

    def get_str(self, key: str, default: str = "") -> str:
        value = self.get(key, default)
        return "" if value is None else str(value)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def get_dict(self, key: str) -> dict[str, Any]:
        value = self.get(key)
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def get_list(self, key: str) -> list[Any]:
        value = self.get(key)
        return cast(list[Any], value) if isinstance(value, list) else []

    # --- writes --------------------------------------------------------------------
    def set(self, key: str, value: Any) -> None:
        self.set_many({key: value})

    def set_many(self, values: dict[str, Any]) -> None:
        with session_scope() as session:
            for key, value in values.items():
                encrypted = key.endswith(SECRET_SUFFIX)
                stored: Any = value
                if encrypted:
                    # An empty string means "forget this key", not "encrypt nothing".
                    stored = self.box.encrypt(str(value)) if value else ""
                row = session.get(Setting, key)
                if row is None:
                    session.add(Setting(key=key, value=stored, encrypted=encrypted))
                else:
                    row.value = stored
                    row.encrypted = encrypted
        self.invalidate()

    def delete(self, key: str) -> None:
        with session_scope() as session:
            row = session.get(Setting, key)
            if row is not None:
                session.delete(row)
        self.invalidate()

    # --- provider helpers ----------------------------------------------------------
    def api_key(self, provider: str) -> str:
        return self.get_str(f"providers.{provider}.api_key")

    def base_url(self, provider: str, default: str = "") -> str:
        return self.get_str(f"providers.{provider}.base_url", default) or default

    def routing(self, task_class: str) -> tuple[str, str]:
        """``(provider, model)`` for a task class, falling back to ``standard``."""
        conf = self.get_dict(f"routing.{task_class}")
        provider = str(conf.get("provider") or "")
        model = str(conf.get("model") or "")
        if not provider and task_class != "standard":
            return self.routing("standard")
        return provider, model

    def configured_providers(self) -> set[str]:
        """Providers with either a stored key or a base URL — i.e. plausibly usable."""
        found: set[str] = set()
        merged = dict(self._load())
        for key in list(merged) + list(ENV_ALIASES):
            if not key.startswith("providers."):
                continue
            parts = key.split(".")
            if len(parts) < 3:
                continue
            name = parts[1]
            if self.get_str(f"providers.{name}.api_key") or self.get_str(
                f"providers.{name}.base_url"
            ):
                found.add(name)
        # Local providers and the offline mock need no credentials to be usable.
        found.update({"mock", "ollama", "lmstudio", "fastembed"})
        return found

    def as_public_dict(self) -> dict[str, Any]:
        """Settings safe to hand to the browser.

        Secrets are replaced with a boolean presence flag. The UI needs to know a key is set so
        it can show "configured" without ever receiving the value.
        """
        merged: dict[str, Any] = {k: self.get(k) for k in DEFAULTS}
        secrets_present: dict[str, bool] = {}
        for key in set(self._load()) | set(ENV_ALIASES):
            if key.endswith(SECRET_SUFFIX):
                provider = key.split(".")[1]
                secrets_present[provider] = bool(self.get_str(key))
            elif key.startswith("providers.") and key not in merged:
                merged[key] = self.get(key)
        merged["_secrets_present"] = secrets_present
        return merged


_store: SettingsStore | None = None


def get_settings() -> SettingsStore:
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store


def reset_settings() -> None:
    """Drop the cached store. Used between tests."""
    global _store
    _store = None
