"""Local ONNX embeddings via fastembed — the default, and the reason RAG works with no API key.

PRD §4.2 makes this the fresh-install default: "local, ~130MB, no API key needed, works
immediately." It runs on onnxruntime rather than PyTorch, which is what keeps the base image
near 1.5GB instead of pulling several gigabytes of CUDA-capable wheels for a model that fits in
a phone.

The model is loaded lazily and downloaded on first use, so a user who never touches chat or
semantic search never pays the download.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from typing import Any

from ...config import get_config
from ...errors import DependencyMissingError, ProviderError
from ..base import EmbeddingProvider, ModelInfo

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

#: Output widths for the models offered in the UI. Declared rather than probed so the
#: sqlite-vec table can be created before the ~130MB download happens.
KNOWN_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "jinaai/jina-embeddings-v2-small-en": 512,
    "intfloat/multilingual-e5-large": 1024,
}


class FastEmbedProvider(EmbeddingProvider):
    name = "fastembed"
    label = "fastembed (local)"
    requires_api_key = False
    is_local = True

    def __init__(self, *, model: str = DEFAULT_MODEL) -> None:
        self.model_name = model or DEFAULT_MODEL
        self._model: Any = None
        # Model construction is not thread-safe and two concurrent embed calls would otherwise
        # each start a download.
        self._lock = threading.Lock()

    @property
    def dimensions(self) -> int:
        return KNOWN_DIMENSIONS.get(self.model_name, 384)

    @property
    def max_batch(self) -> int:
        return 32

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=name, name=f"{name} ({dims}d)")
            for name, dims in sorted(KNOWN_DIMENSIONS.items())
        ]

    def _load(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - declared dependency
                raise DependencyMissingError(
                    "fastembed is not installed. Reinstall quicklearn, or pick a different "
                    "embedding provider in Settings."
                ) from exc

            cache_dir = get_config().cache_dir / "fastembed"
            cache_dir.mkdir(parents=True, exist_ok=True)
            log.info("loading embedding model %s (first run downloads ~130MB)", self.model_name)
            try:
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(cache_dir))
            except Exception as exc:
                raise ProviderError(
                    f"Could not load the local embedding model '{self.model_name}' "
                    f"({exc}). The first run needs to download it — check your network, or "
                    "choose a different embedding provider in Settings.",
                    provider=self.name,
                ) from exc
            return self._model

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        return [list(map(float, vector)) for vector in model.embed(texts)]

    async def embed(self, texts: Sequence[str], *, model: str = "") -> list[list[float]]:
        if model and model != self.model_name:
            # Switching models mid-flight changes the vector width, which would corrupt the
            # index. Rebuild the provider instead of silently mixing dimensions.
            self.model_name = model
            self._model = None
        items = [t if t.strip() else " " for t in texts]
        if not items:
            return []
        # ONNX inference is CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(self._embed_sync, items)
