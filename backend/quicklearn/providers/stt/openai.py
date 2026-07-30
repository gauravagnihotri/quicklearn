"""OpenAI speech-to-text."""

from __future__ import annotations

from .openai_compat import WhisperCompatSTT


class OpenAISTT(WhisperCompatSTT):
    name = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    default_model = "whisper-1"
    available_model_ids = ("whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe")
    _max_file_bytes = 24 * 1024 * 1024
