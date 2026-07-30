"""LM Studio — local inference behind an OpenAI-compatible server."""

from __future__ import annotations

from .openai_compat import OpenAICompatLLM, StructuredMode


class LMStudioLLM(OpenAICompatLLM):
    name = "lmstudio"
    label = "LM Studio"
    default_base_url = "http://localhost:1234/v1"
    structured_mode: StructuredMode = "json_schema"
    requires_api_key = False
    is_local = True
    _context_window = 32_768
