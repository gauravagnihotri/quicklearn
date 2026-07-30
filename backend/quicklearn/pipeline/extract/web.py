"""Web extraction: YouTube (with the subtitle fast path) and generic articles.

PRD §6.1 specifies the YouTube ordering and it is worth restating why it matters: if a video
has *manual* captions, using them skips transcription entirely. That is free, instant, and more
accurate than STT, because a human wrote them. Only when no captions exist do we download audio
and pay to transcribe it.

**Legal note.** yt-dlp usage may conflict with YouTube's Terms of Service. The feature is
user-directed — the user supplies a specific URL — and nothing is scraped or discovered
automatically. See the README's Legal section.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from ...errors import PipelineError
from .base import ExtractResult, RawUnit

log = logging.getLogger(__name__)

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "music.youtube.com",
    "www.youtube-nocookie.com",
}

_VTT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
_TAG = re.compile(r"<[^>]+>")


def is_youtube(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower() in YOUTUBE_HOSTS
    except ValueError:
        return False


# --------------------------------------------------------------------------- subtitle parsing


def parse_json3(payload: str) -> list[RawUnit]:
    """YouTube's json3 caption format — the one that carries real timing."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []

    units: list[RawUnit] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(str(seg.get("utf8") or "") for seg in segs if isinstance(seg, dict))
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        start = int(event.get("tStartMs") or 0)
        duration = int(event.get("dDurationMs") or 0)
        units.append(RawUnit(text=text, start_ms=start, end_ms=start + duration))
    return units


def parse_vtt(payload: str) -> list[RawUnit]:
    """WebVTT / SRT fallback."""
    units: list[RawUnit] = []
    current: tuple[int, int] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current and buffer:
            text = _TAG.sub("", " ".join(buffer)).strip()
            if text:
                units.append(RawUnit(text=text, start_ms=current[0], end_ms=current[1]))
        current, buffer = None, []

    for line in payload.splitlines():
        stripped = line.strip()
        match = _VTT_TIME.search(stripped)
        if match:
            flush()
            h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
            current = (
                ((h1 * 3600 + m1 * 60 + s1) * 1000) + ms1,
                ((h2 * 3600 + m2 * 60 + s2) * 1000) + ms2,
            )
        elif (
            not stripped
            or stripped.isdigit()
            or stripped.startswith(("WEBVTT", "Kind:", "Language:"))
        ):
            continue
        elif current:
            buffer.append(stripped)

    flush()

    # Auto-captions repeat the previous line as a rolling caption; collapse those.
    deduped: list[RawUnit] = []
    for unit in units:
        if deduped and deduped[-1].text == unit.text:
            deduped[-1].end_ms = unit.end_ms
            continue
        deduped.append(unit)
    return deduped


# --------------------------------------------------------------------------- YouTube


def _probe_youtube_sync(url: str) -> dict[str, Any]:
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    return dict(info or {})


def _pick_subtitle_url(info: dict[str, Any]) -> tuple[str, str, bool] | None:
    """Choose the best caption track.

    Returns ``(url, format, is_manual)``. Manual captions are strongly preferred — they are
    written by a person, so they beat both auto-captions and paid transcription.
    """
    for key, is_manual in (("subtitles", True), ("automatic_captions", False)):
        tracks = info.get(key)
        if not isinstance(tracks, dict) or not tracks:
            continue

        language = next(
            (code for code in tracks if code.startswith("en")),
            next(iter(tracks), None),
        )
        if language is None:
            continue

        formats = tracks.get(language) or []
        by_ext = {str(f.get("ext")): str(f.get("url")) for f in formats if f.get("url")}
        for ext in ("json3", "vtt", "srv3", "ttml"):
            if ext in by_ext:
                return by_ext[ext], ext, is_manual
    return None


def _download_audio_sync(url: str, destination_dir: Path) -> Path:
    import yt_dlp

    destination_dir.mkdir(parents=True, exist_ok=True)
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(destination_dir / "original.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        candidates = sorted(destination_dir.glob("original.*"))
        if not candidates:
            raise PipelineError("yt-dlp reported success but produced no audio file.")
        path = candidates[0]
    return path


async def extract_youtube(
    url: str, media_dir: Path
) -> tuple[ExtractResult | None, dict[str, Any], Path | None]:
    """Resolve a YouTube URL.

    Returns ``(captions, metadata, audio_path)``. When captions are available the first element
    holds them and ``audio_path`` is ``None`` — no transcription needed. Otherwise captions are
    ``None`` and the caller runs the downloaded audio through STT.
    """
    try:
        info = await asyncio.to_thread(_probe_youtube_sync, url)
    except Exception as exc:
        raise PipelineError(
            f"Could not read that YouTube URL ({type(exc).__name__}). The video may be "
            "private, age-restricted, region-locked, or removed."
        ) from exc

    metadata = {
        "url": url,
        "title": str(info.get("title") or ""),
        "author": str(info.get("uploader") or info.get("channel") or ""),
        "duration_sec": float(info.get("duration") or 0.0),
        "upload_date": str(info.get("upload_date") or ""),
        "video_id": str(info.get("id") or ""),
    }

    picked = _pick_subtitle_url(info)
    if picked is not None:
        sub_url, ext, is_manual = picked
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(sub_url)
                response.raise_for_status()
                payload = response.text

            units = parse_json3(payload) if ext == "json3" else parse_vtt(payload)
            if units:
                metadata["caption_source"] = "manual" if is_manual else "automatic"
                log.info(
                    "using %s captions for %s — skipping transcription entirely",
                    metadata["caption_source"],
                    metadata["video_id"],
                )
                return (
                    ExtractResult(
                        units=units,
                        duration_sec=metadata["duration_sec"] or None,
                        provider="youtube-captions",
                        model=f"{'manual' if is_manual else 'auto'}/{ext}",
                    ),
                    metadata,
                    None,
                )
        except Exception as exc:
            log.warning("caption fetch failed (%s); falling back to audio download", exc)

    audio_path = await asyncio.to_thread(_download_audio_sync, url, media_dir)
    metadata["caption_source"] = "none"
    return None, metadata, audio_path


# --------------------------------------------------------------------------- articles


def _extract_article_sync(html: str, url: str) -> tuple[str, dict[str, Any]]:
    import trafilatura

    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        url=url,
    )
    metadata: dict[str, Any] = {"url": url}
    try:
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            metadata.update(
                {
                    "title": meta.title or "",
                    "author": meta.author or "",
                    "date": meta.date or "",
                    "sitename": meta.sitename or "",
                }
            )
    except Exception:  # metadata is a nice-to-have, never worth failing the ingest for
        log.debug("trafilatura metadata extraction failed for %s", url, exc_info=True)
    return text or "", metadata


async def extract_article(url: str) -> tuple[ExtractResult, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            timeout=45.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; quicklearn/1.0)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPStatusError as exc:
        raise PipelineError(
            f"That page returned HTTP {exc.response.status_code}. It may require a login, "
            "or block automated requests."
        ) from exc
    except httpx.HTTPError as exc:
        raise PipelineError(f"Could not fetch that URL ({type(exc).__name__}).") from exc

    text, metadata = await asyncio.to_thread(_extract_article_sync, html, url)
    if not text.strip():
        raise PipelineError(
            "No article text could be extracted from that page. It may be mostly images or "
            "rendered entirely by JavaScript."
        )

    blocks = [block.strip() for block in text.split("\n\n")]
    units = [RawUnit(text=block) for block in blocks if block]
    return ExtractResult(units=units, provider="trafilatura"), metadata
