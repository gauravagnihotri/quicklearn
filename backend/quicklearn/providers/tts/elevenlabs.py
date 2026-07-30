"""ElevenLabs text-to-speech — the highest-quality option, and the most expensive."""

from __future__ import annotations

from ..base import ModelInfo, TTSProvider, Voice
from ..http import LONG_TIMEOUT, build_client, request_bytes, request_json

MODELS = ("eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_flash_v2_5")
#: Rachel — a stable default from the public voice library.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"
    label = "ElevenLabs"
    default_base_url = "https://api.elevenlabs.io/v1"
    default_model = "eleven_turbo_v2_5"

    def __init__(self, *, api_key: str = "", base_url: str = "") -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = build_client(
            base_url=self.base_url,
            headers={"xi-api-key": api_key} if api_key else {},
            timeout=LONG_TIMEOUT,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        data = await request_json(
            self._client, "GET", "/models", provider=self.name, label=self.label
        )
        items = data.get("models") if isinstance(data.get("models"), list) else None
        if items is None:
            return [ModelInfo(id=m) for m in MODELS]
        return [
            ModelInfo(id=str(m["model_id"]), name=str(m.get("name") or m["model_id"]))
            for m in items
            if isinstance(m, dict) and m.get("model_id")
        ]

    async def available_voices(self) -> list[Voice]:
        data = await request_json(
            self._client, "GET", "/voices", provider=self.name, label=self.label
        )
        voices = []
        for item in data.get("voices") or []:
            if not isinstance(item, dict) or not item.get("voice_id"):
                continue
            labels = item.get("labels") or {}
            description = ", ".join(
                str(v) for k, v in labels.items() if k in {"accent", "gender", "age"} and v
            )
            voices.append(
                Voice(
                    id=str(item["voice_id"]),
                    name=str(item.get("name") or item["voice_id"]),
                    description=description,
                )
            )
        return voices

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
    ) -> bytes:
        voice_id = voice or DEFAULT_VOICE_ID
        return await request_bytes(
            self._client,
            "POST",
            f"/text-to-speech/{voice_id}",
            provider=self.name,
            label=self.label,
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": model or self.default_model,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    # ElevenLabs clamps speed to 0.7–1.2, narrower than OpenAI's range.
                    "speed": max(0.7, min(1.2, speed)),
                },
            },
        )
