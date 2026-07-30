"""OpenAI embeddings."""

from __future__ import annotations

from collections.abc import Sequence

from ...errors import ProviderResponseError
from ..base import EmbeddingProvider, ModelInfo
from ..http import build_client, request_json

DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbedding(EmbeddingProvider):
    name = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"

    def __init__(
        self, *, api_key: str = "", base_url: str = "", model: str = DEFAULT_MODEL
    ) -> None:
        self.api_key = api_key
        self.model_name = model or DEFAULT_MODEL
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def dimensions(self) -> int:
        return DIMENSIONS.get(self.model_name, 1536)

    @property
    def max_batch(self) -> int:
        return 128

    async def list_models(self) -> list[ModelInfo]:
        await request_json(self._client, "GET", "/models", provider=self.name, label=self.label)
        return [ModelInfo(id=k, name=f"{k} ({v}d)") for k, v in sorted(DIMENSIONS.items())]

    async def embed(self, texts: Sequence[str], *, model: str = "") -> list[list[float]]:
        if not texts:
            return []
        if model:
            self.model_name = model
        data = await request_json(
            self._client,
            "POST",
            "/embeddings",
            provider=self.name,
            label=self.label,
            # The API rejects empty strings, and a blank transcript segment is a real case.
            json={"model": self.model_name, "input": [t if t.strip() else " " for t in texts]},
        )
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise ProviderResponseError(
                "OpenAI returned a different number of embeddings than inputs.",
                provider=self.name,
            )
        # The API does not guarantee ordering, but does return an index on each item.
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
        return [[float(x) for x in item["embedding"]] for item in ordered]
