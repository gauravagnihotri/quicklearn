"""Shared HTTP plumbing and error translation for API-backed providers.

Every provider funnels through here so that the error taxonomy is applied in exactly one place.
PRD §11 asks for "Invalid API key for OpenAI — check Settings" rather than a bare 500, and the
only way that holds across eight providers is to translate once, centrally, instead of hoping
each implementation remembers to.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ..errors import (
    ProviderAuthError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)
#: Transcription of a long file legitimately takes minutes; a short read timeout would turn
#: success into a spurious failure.
LONG_TIMEOUT = httpx.Timeout(connect=10.0, read=900.0, write=900.0, pool=10.0)


def build_client(
    *,
    base_url: str = "",
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers=dict(headers or {}),
        timeout=timeout or DEFAULT_TIMEOUT,
        follow_redirects=True,
        # Providers are long-lived; a small pool avoids re-handshaking on every call.
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
    )


def _extract_message(payload: Any, fallback: str) -> str:
    """Dig the human-readable message out of whatever shape the provider used."""
    if isinstance(payload, str):
        return payload.strip()[:600] or fallback
    if isinstance(payload, dict):
        for key in ("error", "message", "detail", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:600]
            if isinstance(value, dict):
                nested = _extract_message(value, "")
                if nested:
                    return nested
            if isinstance(value, list) and value:
                nested = _extract_message(value[0], "")
                if nested:
                    return nested
    return fallback


def raise_for_response(response: httpx.Response, *, provider: str, label: str) -> None:
    """Translate a non-2xx response into the appropriate typed error."""
    if response.is_success:
        return

    try:
        payload: Any = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = response.text

    detail = _extract_message(payload, f"HTTP {response.status_code}")
    status = response.status_code
    lowered = detail.lower()

    if status in (401, 403):
        raise ProviderAuthError(
            f"{label} rejected the API key ({detail}). Check the key in Settings → Providers.",
            provider=provider,
        )
    if status == 429:
        retry_after_raw = response.headers.get("retry-after")
        try:
            retry_after = float(retry_after_raw) if retry_after_raw else None
        except ValueError:
            retry_after = None
        raise ProviderRateLimitError(
            f"{label} rate limit reached ({detail}). quicklearn will retry automatically.",
            provider=provider,
            retry_after=retry_after,
        )
    if status == 402 or "quota" in lowered or "insufficient" in lowered or "credit" in lowered:
        raise ProviderQuotaError(
            f"{label} reports no remaining credit ({detail}). Add credit or switch provider "
            "in Settings.",
            provider=provider,
        )
    if status == 404:
        raise ProviderError(
            f"{label} could not find that model or endpoint ({detail}). "
            "Pick a different model in Settings → Model routing.",
            provider=provider,
        )
    if status == 413:
        raise ProviderResponseError(
            f"{label} rejected the request as too large ({detail}).", provider=provider
        )
    if status >= 500:
        raise ProviderUnavailableError(
            f"{label} is temporarily unavailable (HTTP {status}: {detail}).", provider=provider
        )
    raise ProviderError(f"{label} rejected the request ({detail}).", provider=provider)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    label: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Perform a request and return parsed JSON, with connection errors translated."""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(
            f"{label} timed out. If this keeps happening the service may be degraded, "
            "or the file may be too large.",
            provider=provider,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(
            f"Could not reach {label} ({type(exc).__name__}: {exc}). "
            "Check your network connection, or the base URL if this is a local provider.",
            provider=provider,
        ) from exc

    raise_for_response(response, provider=provider, label=label)

    try:
        data: Any = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderResponseError(
            f"{label} returned a response that was not valid JSON.", provider=provider
        ) from exc

    if not isinstance(data, dict):
        raise ProviderResponseError(
            f"{label} returned {type(data).__name__} where an object was expected.",
            provider=provider,
        )
    return data


async def request_bytes(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    label: str,
    **kwargs: Any,
) -> bytes:
    """Same as :func:`request_json` but for binary payloads such as TTS audio."""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(f"{label} timed out.", provider=provider) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(
            f"Could not reach {label} ({type(exc).__name__}: {exc}).", provider=provider
        ) from exc

    raise_for_response(response, provider=provider, label=label)
    return response.content


async def stream_sse(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    label: str,
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Iterate a Server-Sent Events stream, yielding decoded JSON payloads.

    Shared by every OpenAI-compatible provider plus Anthropic, all of which use SSE with a
    ``data: `` prefix and a ``[DONE]`` sentinel.
    """
    try:
        async with client.stream(method, url, **kwargs) as response:
            if not response.is_success:
                # Read the body before raising, or the error message is lost to the stream.
                await response.aread()
                raise_for_response(response, provider=provider, label=label)

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(f"{label} timed out mid-stream.", provider=provider) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(
            f"Connection to {label} failed mid-stream ({type(exc).__name__}).", provider=provider
        ) from exc
