"""Offline embeddings.

Hash-based, but not random: the vector is built from overlapping character trigrams, so texts
sharing vocabulary land near each other. That is enough for retrieval tests to assert something
meaningful — that querying "eigenvalue" ranks the eigenvalue segment above the administrative
one — rather than merely asserting the plumbing returns *a* result.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from ..base import EmbeddingProvider, ModelInfo

DIMENSIONS = 384
_TOKEN = re.compile(r"[a-z0-9]+")


class MockEmbedding(EmbeddingProvider):
    name = "mock"
    label = "Mock (offline demo)"
    requires_api_key = False
    is_local = True

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-embed", name=f"Mock embeddings ({DIMENSIONS}d)")]

    async def embed(self, texts: Sequence[str], *, model: str = "") -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        tokens = _TOKEN.findall(text.lower())

        for token in tokens:
            # Whole word plus its trigrams, so related word forms overlap in the vector.
            features = [token] + [token[i : i + 3] for i in range(max(1, len(token) - 2))]
            for feature in features:
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % DIMENSIONS
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            # A deterministic unit vector beats a zero vector, which makes cosine undefined.
            return [1.0] + [0.0] * (DIMENSIONS - 1)
        return [v / norm for v in vector]
