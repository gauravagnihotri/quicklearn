"""Podcast generation: script → per-line TTS → ffmpeg assembly (PRD §7.6).

Synthesis is cached by content hash. Regeneration after editing three lines of a 200-line
script then costs three TTS calls instead of two hundred, which is the difference between
iterating on a podcast and generating one once and living with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select

from ... import prompts
from ...config import get_config
from ...db.enums import PodcastStatus
from ...db.models import Note, Podcast, Source
from ...db.session import session_scope
from ...errors import PipelineError
from ...jobs.events import emit
from ...providers import gateway, registry
from ...providers.base import text_message
from ...settings import get_settings
from .. import media, tiptap
from ..schemas import PodcastScript

log = logging.getLogger(__name__)

#: Roughly 150 spoken words per minute.
WORDS_PER_MINUTE = 150
MAX_PARALLEL_TTS = 4
GAP_MS = 150


def _cache_path(text: str, voice: str, model: str, speed: float) -> Path:
    digest = hashlib.sha256(f"{model}|{voice}|{speed}|{text}".encode()).hexdigest()[:40]
    cache_dir = get_config().cache_dir / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.mp3"


def _gather_content(source_ids: list[int]) -> tuple[str, str]:
    """Collect note text for the podcast. Returns ``(content, title)``."""
    chunks: list[str] = []
    titles: list[str] = []

    with session_scope() as session:
        for source_id in source_ids:
            source = session.get(Source, source_id)
            if source is None:
                continue
            titles.append(source.title)
            note = (
                session.execute(select(Note).where(Note.source_id == source_id).order_by(Note.id))
                .scalars()
                .first()
            )

            if note is not None and note.doc:
                chunks.append(f"# {source.title}\n\n{tiptap.doc_to_plaintext(note.doc)}")
            else:
                # No note yet — fall back to the transcript so the feature still works on a
                # source the user has only just uploaded.
                from ...db.models import Segment

                segments = list(
                    session.execute(
                        select(Segment.text)
                        .where(Segment.source_id == source_id, Segment.relevance >= 0.3)
                        .order_by(Segment.index)
                        .limit(400)
                    ).scalars()
                )
                if segments:
                    chunks.append(f"# {source.title}\n\n" + "\n".join(segments))

    if not chunks:
        raise PipelineError("There is no content to build a podcast from.")

    title = titles[0] if len(titles) == 1 else f"{len(titles)} sources"
    return "\n\n---\n\n".join(chunks), title


async def generate_script(
    source_ids: list[int], *, target_minutes: int = 10, podcast_id: int | None = None
) -> tuple[list[dict[str, str]], str]:
    content, title = _gather_content(source_ids)
    target_words = target_minutes * WORDS_PER_MINUTE

    # Leave room for the response; a script is long output, and truncation here loses the
    # ending rather than degrading gracefully.
    max_chars = 60_000
    if len(content) > max_chars:
        log.info("trimming podcast source content from %d to %d chars", len(content), max_chars)
        content = content[:max_chars]

    prompt = prompts.render(
        "podcast_script",
        content=content,
        target_words=target_words,
        target_minutes=target_minutes,
    )
    script = await gateway.complete_structured(
        "podcast script",
        [text_message("user", prompt)],
        PodcastScript,
        task_class="deep",
        temperature=0.7,
        max_tokens=min(16000, target_words * 3),
        source_id=source_ids[0] if source_ids else None,
    )

    lines = [
        {"speaker": line.speaker, "text": line.text.strip()}
        for line in script.lines
        if line.text.strip()
    ]
    if not lines:
        raise PipelineError("The model returned an empty podcast script.")
    return lines, title


async def synthesize_lines(
    lines: list[dict[str, str]],
    *,
    voice_a: str,
    voice_b: str,
    speed: float = 1.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    # Only the model name is needed here; synthesis itself goes through the gateway below,
    # which resolves the provider on each call.
    _provider, model = registry.get_tts()
    semaphore = asyncio.Semaphore(MAX_PARALLEL_TTS)
    completed = 0
    lock = asyncio.Lock()

    async def run(index: int, line: dict[str, str]) -> tuple[int, Path]:
        nonlocal completed
        voice = voice_a if line["speaker"] == "Host A" else voice_b
        cached = _cache_path(line["text"], voice, model, speed)

        if not cached.exists():
            async with semaphore:
                audio = await gateway.synthesize(line["text"], voice=voice, speed=speed)
            # Write via a temp file then rename, so a crash mid-write cannot leave a truncated
            # file in the cache that would be treated as valid forever after.
            temporary = cached.with_suffix(".part")
            temporary.write_bytes(audio)
            temporary.replace(cached)

        async with lock:
            completed += 1
            if on_progress:
                on_progress(completed, len(lines))
        return index, cached

    results = await asyncio.gather(*(run(i, line) for i, line in enumerate(lines)))
    return [path for _, path in sorted(results, key=lambda pair: pair[0])]


async def generate_podcast(
    podcast_id: int,
    *,
    job_id: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Run the full three-stage pipeline for an existing podcast row."""
    settings = get_settings()

    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is None:
            raise PipelineError(f"Podcast {podcast_id} not found.")
        source_ids = list(podcast.source_ids or ([podcast.source_id] if podcast.source_id else []))
        target_minutes = podcast.target_minutes
        voice_config = dict(podcast.voice_config or {})
        existing_script = list(podcast.script or [])

    if not source_ids:
        raise PipelineError("This podcast has no sources attached.")

    provider, _ = registry.get_tts()
    default_voices = await provider.available_voices()
    fallback_a = default_voices[0].id if default_voices else ""
    fallback_b = default_voices[1].id if len(default_voices) > 1 else fallback_a

    voice_a = voice_config.get("host_a") or settings.get_str("tts.voice_a") or fallback_a
    voice_b = voice_config.get("host_b") or settings.get_str("tts.voice_b") or fallback_b

    # --- 1. script
    _set_status(podcast_id, PodcastStatus.SCRIPTING)
    emit("podcast.progress", podcast_id=podcast_id, stage="Writing script", progress=5)

    if existing_script:
        # Regeneration after a script edit: reuse it, and let the TTS cache make this cheap.
        lines = [
            {"speaker": str(item.get("speaker") or "Host A"), "text": str(item.get("text") or "")}
            for item in existing_script
            if str(item.get("text") or "").strip()
        ]
        title = ""
    else:
        lines, title = await generate_script(
            source_ids, target_minutes=target_minutes, podcast_id=podcast_id
        )
        with session_scope() as session:
            podcast = session.get(Podcast, podcast_id)
            if podcast is not None:
                podcast.script = lines
                if title and podcast.title in ("", "Podcast"):
                    podcast.title = f"{title} — discussion"

    # --- 2. synthesise
    _set_status(podcast_id, PodcastStatus.SYNTHESIZING)
    emit("podcast.progress", podcast_id=podcast_id, stage="Recording voices", progress=20)

    def report(done: int, total: int) -> None:
        emit(
            "podcast.progress",
            podcast_id=podcast_id,
            stage=f"Recording line {done} of {total}",
            progress=20 + int(60 * done / max(1, total)),
        )
        if on_progress:
            on_progress(done, total)

    clips = await synthesize_lines(lines, voice_a=voice_a, voice_b=voice_b, on_progress=report)

    # --- 3. assemble
    _set_status(podcast_id, PodcastStatus.ASSEMBLING)
    emit("podcast.progress", podcast_id=podcast_id, stage="Assembling audio", progress=85)

    output_dir = get_config().media_dir_for(source_ids[0])
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"podcast_{podcast_id}.mp3"

    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        podcast_title = podcast.title if podcast else "quicklearn podcast"

    with tempfile.TemporaryDirectory(prefix="quicklearn-podcast-") as tmp:
        staged = []
        for index, clip in enumerate(clips):
            # Copy into a scratch directory: the concat demuxer resolves relative paths
            # against the list file, and cache paths may contain characters it dislikes.
            target = Path(tmp) / f"{index:04d}.mp3"
            target.write_bytes(clip.read_bytes())
            staged.append(target)

        duration = await media.concat_audio(
            staged,
            Path(tmp) / "out.mp3",
            gap_ms=GAP_MS,
            crossfade_ms=40,
            metadata={
                "title": podcast_title,
                "artist": "quicklearn",
                "album": "quicklearn study podcasts",
                "genre": "Education",
            },
        )
        destination.write_bytes((Path(tmp) / "out.mp3").read_bytes())

    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is not None:
            podcast.audio_path = str(destination)
            podcast.duration_sec = duration
            podcast.status = PodcastStatus.READY
            podcast.error = None
            podcast.voice_config = {"host_a": voice_a, "host_b": voice_b}

    emit("podcast.progress", podcast_id=podcast_id, stage="Ready", progress=100)
    log.info("podcast %s ready: %.1f minutes, %d lines", podcast_id, duration / 60, len(lines))
    return destination


def _set_status(podcast_id: int, status: PodcastStatus, error: str | None = None) -> None:
    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is not None:
            podcast.status = status
            podcast.error = error
