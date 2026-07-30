"""Groq speech-to-text.

The cheapest credible transcription available — roughly $0.04 per audio hour for
``whisper-large-v3-turbo``, which is what makes the README's "~$0.02 per lecture" claim true.
"""

from __future__ import annotations

from .openai_compat import WhisperCompatSTT


class GroqSTT(WhisperCompatSTT):
    name = "groq"
    label = "Groq"
    default_base_url = "https://api.groq.com/openai/v1"
    default_model = "whisper-large-v3-turbo"
    available_model_ids = (
        "whisper-large-v3-turbo",
        "whisper-large-v3",
        "distil-whisper-large-v3-en",
    )
    # Groq's documented ceiling is 25MB on the free tier. Staying under it avoids a 413 on a
    # file that is only marginally too big, and the splitter handles anything larger.
    _max_file_bytes = 24 * 1024 * 1024
