"""Groq — the recommended default. Very fast, very cheap, and has a free tier."""

from __future__ import annotations

from .openai_compat import OpenAICompatLLM, StructuredMode


class GroqLLM(OpenAICompatLLM):
    name = "groq"
    label = "Groq"
    default_base_url = "https://api.groq.com/openai/v1"
    # Groq accepts `json_object` across its whole catalogue, while `json_schema` is only
    # honoured by some models. Choosing the universally supported mode keeps every model in
    # the model picker usable; Pydantic validation with one retry covers the difference.
    structured_mode: StructuredMode = "json_object"
    _context_window = 128_000
