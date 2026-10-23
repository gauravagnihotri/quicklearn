"""LM Studio — local inference behind an OpenAI-compatible server."""

from __future__ import annotations

import os

from .openai_compat import OpenAICompatLLM, StructuredMode


class LMStudioLLM(OpenAICompatLLM):
    name = "lmstudio"
    label = "LM Studio"
    default_base_url = "http://localhost:1234/v1"
    structured_mode: StructuredMode = "json_schema"
    requires_api_key = False
    is_local = True
    _context_window = 32_768

    @property
    def context_window(self) -> int:
        # Local patch: the local server's context is set when it starts (llama-server -c, vLLM
        # --max-model-len), so let the deployment say what it is. Notes outlining sizes its
        # transcript windows from this; 32k split long lectures that fit the real context whole.
        return int(os.environ.get("QUICKLEARN_LMSTUDIO_CONTEXT_WINDOW", self._context_window))
