"""OpenRouter — one key, hundreds of models, pass-through pricing."""

from __future__ import annotations

from .openai_compat import OpenAICompatLLM, StructuredMode


class OpenRouterLLM(OpenAICompatLLM):
    name = "openrouter"
    label = "OpenRouter"
    default_base_url = "https://openrouter.ai/api/v1"
    structured_mode: StructuredMode = "json_object"
    _context_window = 128_000
    _supports_vision = True

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        # OpenRouter asks callers to identify themselves; these show up in its dashboard and
        # are used for its model-ranking leaderboard. No user data is included.
        headers = {
            "HTTP-Referer": "https://github.com/artbyjazi/quicklearn",
            "X-Title": "quicklearn",
        }
        headers.update(extra_headers or {})
        super().__init__(api_key=api_key, base_url=base_url, extra_headers=headers)
