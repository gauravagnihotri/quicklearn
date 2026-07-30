"""OpenAI chat models."""

from __future__ import annotations

from .openai_compat import OpenAICompatLLM, StructuredMode


class OpenAILLM(OpenAICompatLLM):
    name = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    structured_mode: StructuredMode = "json_schema"
    _context_window = 128_000
    _supports_vision = True
