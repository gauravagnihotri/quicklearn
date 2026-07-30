"""Ollama embeddings — local, free, and the offline complement to Ollama's chat models."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ...errors import ProviderResponseError
from ..base import EmbeddingProvider, ModelInfo
from ..http import build_client, request_json

log = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
#: Widths for the common embedding models, so the vector table can be sized without a probe.
KNOWN_DIMENSIONS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "bge-m3": 1024,
}


class OllamaEmbedding(EmbeddingProvider):
    name = "ollama"
    label = "Ollama (local)"
    default_base_url = "http://localhost:11434"
    requires_api_key = False
    is_local = True

    def __init__(self, *, base_url: str = "", model: str = DEFAULT_MODEL, **_: object) -> None:
        # Accept either the native root or the OpenAI-compatible /v1 URL the LLM provider uses,
        # so a user who pasted one into Settings doesn't get a confusing 404 from the other.
        self.base_url = (base_url or self.default_base_url).rstrip("/").removesuffix("/v1")
        self.model_name = model or DEFAULT_MODEL
        self._client = build_client(base_url=self.base_url)
        self._probed_dimensions: int | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def dimensions(self) -> int:
        if self._probed_dimensions is not None:
            return self._probed_dimensions
        for prefix, dims in KNOWN_DIMENSIONS.items():
            if self.model_name.startswith(prefix):
                return dims
        return 768

    async def list_models(self) -> list[ModelInfo]:
        data = await request_json(
            self._client, "GET", "/api/tags", provider=self.name, label=self.label
        )
        models = []
        for item in data.get("models") or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model") or item.get("name") or "")
            if model_id:
                models.append(ModelInfo(id=model_id, name=model_id))
        return sorted(models, key=lambda m: m.id)

    async def embed(self, texts: Sequence[str], *, model: str = "") -> list[list[float]]:
        if not texts:
            return []
        if model:
            self.model_name = model

        data = await request_json(
            self._client,
            "POST",
            "/api/embed",
            provider=self.name,
            label=self.label,
            json={
                "model": self.model_name,
                "input": [t if t.strip() else " " for t in texts],
            },
        )
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ProviderResponseError(
                f"Ollama returned {len(vectors) if isinstance(vectors, list) else 0} embeddings "
                f"for {len(texts)} inputs. Is '{self.model_name}' an embedding model? "
                "Pull one with: ollama pull nomic-embed-text",
                provider=self.name,
            )

        result = [[float(x) for x in vector] for vector in vectors]
        # Record the true width — a user may run a model that isn't in the table above, and
        # guessing wrong would silently break the vector index.
        if result and result[0]:
            self._probed_dimensions = len(result[0])
        return result
