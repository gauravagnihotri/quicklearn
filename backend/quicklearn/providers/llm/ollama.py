"""Ollama — fully local inference. Nothing leaves the machine.

This is the provider that makes the project's central privacy claim literal rather than
aspirational, so it needs to work as smoothly as the cloud options.
"""

from __future__ import annotations

from ..base import ModelInfo
from ..http import request_json
from .openai_compat import OpenAICompatLLM, StructuredMode


class OllamaLLM(OpenAICompatLLM):
    name = "ollama"
    label = "Ollama"
    default_base_url = "http://localhost:11434/v1"
    structured_mode: StructuredMode = "json_object"
    requires_api_key = False
    is_local = True
    _context_window = 32_768

    async def list_models(self) -> list[ModelInfo]:
        """Enumerate pulled models via Ollama's native endpoint.

        The OpenAI-compatible ``/v1/models`` route works, but the native ``/api/tags`` response
        includes parameter size and quantisation, which is genuinely useful when choosing
        between three local variants of the same model in the Settings UI.
        """
        native_base = self.base_url.removesuffix("/v1")
        data = await request_json(
            self._client,
            "GET",
            f"{native_base}/api/tags",
            provider=self.name,
            label=self.label,
        )
        models = []
        for item in data.get("models") or []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("model") or item.get("name")
            if not model_id:
                continue
            details = item.get("details") or {}
            bits = [
                str(details.get(k))
                for k in ("parameter_size", "quantization_level")
                if details.get(k)
            ]
            suffix = f" ({', '.join(bits)})" if bits else ""
            models.append(ModelInfo(id=str(model_id), name=f"{model_id}{suffix}"))
        return sorted(models, key=lambda m: m.id)
