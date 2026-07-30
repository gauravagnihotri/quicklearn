"""Image extraction via a vision model (PRD §6.3)."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ... import prompts
from ...errors import ConfigurationError, PipelineError
from ...providers import gateway, registry
from ...providers.base import ImagePart, Message
from .base import ExtractResult, RawUnit

#: Vision endpoints reject very large payloads, and a photo of a whiteboard is often 10MB+.
MAX_IMAGE_BYTES = 18 * 1024 * 1024


async def extract_image(
    path: Path, *, source_id: int | None = None, job_id: int | None = None
) -> ExtractResult:
    resolved = registry.get_llm("standard")
    if not resolved.provider.supports_vision:
        raise ConfigurationError(
            f"The model configured for the 'standard' task class ({resolved.model}) cannot "
            "read images. Choose a vision-capable model — OpenAI, Anthropic or Gemini — in "
            "Settings → Model routing."
        )

    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise PipelineError(
            f"'{path.name}' is {len(data) / 1e6:.1f} MB, larger than the "
            f"{MAX_IMAGE_BYTES / 1e6:.0f} MB limit for image analysis. Resize it and try again."
        )

    media_type = mimetypes.guess_type(path.name)[0] or "image/png"
    image_part: ImagePart = {
        "type": "image",
        "media_type": media_type,
        "data": base64.b64encode(data).decode(),
    }
    message: Message = {
        "role": "user",
        "content": [
            {"type": "text", "text": prompts.render("image_describe", hint=path.name)},
            image_part,
        ],
    }

    text = await gateway.complete_text(
        "image_extract", [message], source_id=source_id, job_id=job_id, max_tokens=3000
    )
    if not text.strip():
        raise PipelineError(f"The vision model returned no content for '{path.name}'.")

    blocks = [block.strip() for block in text.split("\n\n")]
    units = [RawUnit(text=block, page_no=1) for block in blocks if block]
    return ExtractResult(
        units=units, page_count=1, provider=resolved.provider_name, model=resolved.model
    )
