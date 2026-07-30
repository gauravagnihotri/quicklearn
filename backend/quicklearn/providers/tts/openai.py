"""OpenAI text-to-speech."""

from __future__ import annotations

from ..base import ModelInfo, TTSProvider, Voice
from ..http import LONG_TIMEOUT, build_client, request_bytes, request_json

VOICES = [
    Voice("alloy", "Alloy", "Neutral, even"),
    Voice("ash", "Ash", "Warm, conversational"),
    Voice("coral", "Coral", "Bright, energetic"),
    Voice("echo", "Echo", "Calm, measured"),
    Voice("fable", "Fable", "Expressive, storytelling"),
    Voice("nova", "Nova", "Friendly, upbeat"),
    Voice("onyx", "Onyx", "Deep, authoritative"),
    Voice("sage", "Sage", "Soft, thoughtful"),
    Voice("shimmer", "Shimmer", "Light, clear"),
]

MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")


class OpenAITTS(TTSProvider):
    name = "openai"
    label = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o-mini-tts"
    default_voice = "alloy"

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=LONG_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        # Confirms the key works before the user commits to generating a 20-minute podcast.
        await request_json(self._client, "GET", "/models", provider=self.name, label=self.label)
        return [ModelInfo(id=m) for m in MODELS]

    async def available_voices(self) -> list[Voice]:
        return list(VOICES)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
    ) -> bytes:
        return await request_bytes(
            self._client,
            "POST",
            "/audio/speech",
            provider=self.name,
            label=self.label,
            json={
                "model": model or self.default_model,
                "input": text,
                "voice": voice or self.default_voice,
                "response_format": "mp3",
                # The API rejects anything outside 0.25–4.0.
                "speed": max(0.25, min(4.0, speed)),
            },
        )
