"""Podcast generation, playback and the RSS feed (PRD §7.6)."""

from __future__ import annotations

import html
import logging
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db.enums import JobType, PodcastStatus
from ..db.models import Podcast, Source
from ..db.session import session_scope
from ..errors import NotFoundError, ValidationError
from ..jobs import queue as jq
from .common import podcast_dict, run_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/podcasts", tags=["podcasts"])


class CreatePodcastRequest(BaseModel):
    source_ids: list[int] = Field(min_length=1)
    length: int = Field(default=10, ge=3, le=40)
    title: str = ""
    voice_a: str = ""
    voice_b: str = ""


class ScriptUpdate(BaseModel):
    script: list[dict[str, str]]


@router.post("")
async def create_podcast(payload: CreatePodcastRequest) -> dict[str, Any]:
    from ..providers import registry

    if not await run_db(registry.has_tts):
        raise ValidationError(
            "No text-to-speech provider is configured. Add OpenAI or ElevenLabs in "
            "Settings → Providers to generate podcasts."
        )

    def create() -> int:
        with session_scope() as session:
            sources = list(
                session.execute(select(Source).where(Source.id.in_(payload.source_ids))).scalars()
            )
            if not sources:
                raise NotFoundError("None of those sources exist.")
            title = payload.title.strip() or (
                f"{sources[0].title} — discussion"
                if len(sources) == 1
                else f"{len(sources)} sources — discussion"
            )
            podcast = Podcast(
                source_id=sources[0].id,
                source_ids=[source.id for source in sources],
                title=title[:400],
                target_minutes=payload.length,
                voice_config={"host_a": payload.voice_a, "host_b": payload.voice_b},
                status=PodcastStatus.PENDING,
            )
            session.add(podcast)
            session.flush()
            return podcast.id

    podcast_id = await run_db(create)
    job_id = await run_db(
        jq.enqueue,
        JobType.GENERATE_PODCAST,
        payload={"podcast_id": podcast_id},
        source_id=payload.source_ids[0],
        dedupe_key=f"podcast-{podcast_id}",
    )
    return {"podcast_id": podcast_id, "job_id": job_id}


@router.get("")
def list_podcasts(limit: int = 50) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Podcast).order_by(Podcast.created_at.desc()).limit(limit)
            ).scalars()
        )
    return {"podcasts": [podcast_dict(podcast) for podcast in rows]}


@router.get("/voices")
async def list_voices() -> dict[str, Any]:
    from ..providers import registry

    try:
        provider, _ = registry.get_tts()
    except Exception as exc:
        return {"voices": [], "error": str(exc)}
    voices = await provider.available_voices()
    return {
        "provider": provider.name,
        "voices": [
            {"id": voice.id, "name": voice.name, "description": voice.description}
            for voice in voices
        ],
    }


@router.get("/feed.xml")
def rss_feed(request: Request) -> Response:
    """Podcast RSS feed.

    A small amount of code with a disproportionate effect on whether the feature gets used —
    subscribing in a real podcast app is how people actually listen, and PRD §7.6 calls this
    out specifically.
    """
    base = str(request.base_url).rstrip("/")

    with session_scope() as session:
        rows = list(
            session.execute(
                select(Podcast)
                .where(Podcast.status == PodcastStatus.READY)
                .order_by(Podcast.created_at.desc())
                .limit(100)
            ).scalars()
        )
        episodes = [
            {
                "id": podcast.id,
                "title": podcast.title,
                "created_at": podcast.created_at,
                "duration": int(podcast.duration_sec or 0),
                "size": Path(podcast.audio_path).stat().st_size
                if podcast.audio_path and Path(podcast.audio_path).exists()
                else 0,
                "lines": len(podcast.script or []),
            }
            for podcast in rows
        ]

    items = []
    for episode in episodes:
        hours, remainder = divmod(episode["duration"], 3600)
        minutes, seconds = divmod(remainder, 60)
        duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        url = f"{base}/api/v1/podcasts/{episode['id']}/audio"
        summary = html.escape(f"Generated study discussion — {episode['lines']} exchanges.")
        items.append(
            f"""    <item>
      <title>{html.escape(str(episode["title"]))}</title>
      <description>{summary}</description>
      <enclosure url="{html.escape(url)}" length="{episode["size"]}" type="audio/mpeg"/>
      <guid isPermaLink="false">quicklearn-podcast-{episode["id"]}</guid>
      <pubDate>{format_datetime(episode["created_at"])}</pubDate>
      <itunes:duration>{duration}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>quicklearn</title>
    <link>{html.escape(base)}</link>
    <description>Study podcasts generated from your own material.</description>
    <language>en</language>
    <itunes:author>quicklearn</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Education"/>
{chr(10).join(items)}
  </channel>
</rss>
"""
    return Response(feed, media_type="application/rss+xml; charset=utf-8")


@router.get("/{podcast_id}")
def get_podcast(podcast_id: int) -> dict[str, Any]:
    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is None:
            raise NotFoundError(f"Podcast {podcast_id} not found.")
        return podcast_dict(podcast)


@router.get("/{podcast_id}/audio")
def get_podcast_audio(podcast_id: int) -> FileResponse:
    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is None or not podcast.audio_path:
            raise NotFoundError("This podcast has no audio yet.")
        path = Path(podcast.audio_path)
        title = podcast.title

    if not path.exists():
        raise NotFoundError("The podcast audio is missing from disk.")
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "podcast"
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"{safe}.mp3",
        headers={"Accept-Ranges": "bytes"},
    )


@router.patch("/{podcast_id}/script")
async def update_script(podcast_id: int, payload: ScriptUpdate) -> dict[str, Any]:
    """Edit the script before or after synthesis.

    Regenerating afterwards is cheap because synthesis is cached by content hash — only the
    lines that actually changed are re-synthesised.
    """

    def save() -> dict[str, Any]:
        with session_scope() as session:
            podcast = session.get(Podcast, podcast_id)
            if podcast is None:
                raise NotFoundError(f"Podcast {podcast_id} not found.")
            podcast.script = [
                {
                    "speaker": str(line.get("speaker") or "Host A"),
                    "text": str(line.get("text") or "").strip(),
                }
                for line in payload.script
                if str(line.get("text") or "").strip()
            ]
            return podcast_dict(podcast)

    return await run_db(save)


@router.post("/{podcast_id}/regenerate")
async def regenerate_podcast(podcast_id: int) -> dict[str, Any]:
    def reset() -> int:
        with session_scope() as session:
            podcast = session.get(Podcast, podcast_id)
            if podcast is None:
                raise NotFoundError(f"Podcast {podcast_id} not found.")
            podcast.status = PodcastStatus.PENDING
            podcast.error = None
            return podcast.source_id or (podcast.source_ids or [0])[0]

    source_id = await run_db(reset)
    job_id = await run_db(
        jq.enqueue,
        JobType.GENERATE_PODCAST,
        payload={"podcast_id": podcast_id},
        source_id=source_id,
        dedupe_key=f"podcast-{podcast_id}",
    )
    return {"job_id": job_id}


@router.delete("/{podcast_id}")
def delete_podcast(podcast_id: int) -> dict[str, Any]:
    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is None:
            raise NotFoundError(f"Podcast {podcast_id} not found.")
        if podcast.audio_path:
            Path(podcast.audio_path).unlink(missing_ok=True)
        session.delete(podcast)
    return {"deleted": True}
