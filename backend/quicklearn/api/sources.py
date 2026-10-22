"""Source ingest and retrieval endpoints (PRD §8)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ..db.enums import JobType, SourceKind, SourceOrigin, SourceStatus
from ..db.models import Card, Concept, Course, Lesson, QuizItem, Segment, Source, Tag, Transcript
from ..db.search import delete_source as search_delete_source
from ..db.session import raw_sqlite, session_scope
from ..errors import NotFoundError, ValidationError
from ..handlers.ingest import enqueue_ingest
from ..jobs import queue as jq
from ..pipeline import acquire
from ..pipeline.extract.web import is_youtube
from ..pipeline.generate.notes import note_for
from ..rag import index as rag_index
from .common import (
    concept_dict,
    note_dict,
    run_db,
    segment_dict,
    source_dict,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sources", tags=["sources"])

CHUNK_READ = 1024 * 1024


# --------------------------------------------------------------------------- payloads


class UrlRequest(BaseModel):
    url: str = Field(min_length=4)
    folder_id: int | None = None


class TextRequest(BaseModel):
    title: str = ""
    content: str = Field(min_length=1)
    folder_id: int | None = None


class RecordStartRequest(BaseModel):
    title: str = ""
    folder_id: int | None = None


class MarkerRequest(BaseModel):
    position_ms: int = Field(ge=0)


class RegenerateRequest(BaseModel):
    #: ``simplified`` (local patch) rewrites only the ADHD-friendly note.
    target: Literal["notes", "simplified", "concepts", "cards", "quiz", "all"] = "notes"
    style: str = ""
    count: int = 20
    max_cards: int | None = None


class UpdateSourceRequest(BaseModel):
    title: str | None = None
    folder_id: int | None = None
    tags: list[str] | None = None


# --------------------------------------------------------------------------- upload


@router.post("/upload")
async def upload_source(
    file: Annotated[UploadFile, File()],
    folder_id: Annotated[int | None, Form()] = None,
) -> dict[str, Any]:
    """Single-request upload, for files small enough to send in one go."""
    filename = acquire.sanitise_filename(file.filename or "upload")
    kind = acquire.kind_for_filename(filename)

    source_id = await run_db(
        acquire.create_source,
        title=Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename,
        kind=kind,
        origin=SourceOrigin.UPLOAD,
        folder_id=folder_id,
        original_filename=filename,
    )

    path = acquire.media_path_for(source_id, filename)
    written = 0
    # Streamed to disk rather than read into memory: a 2GB lecture recording would otherwise
    # take 2GB of RSS during upload.
    with path.open("wb") as handle:
        while chunk := await file.read(CHUNK_READ):
            written += len(chunk)
            if written > acquire.MAX_UPLOAD_BYTES:
                handle.close()
                path.unlink(missing_ok=True)
                await run_db(_delete_source_rows, source_id)
                raise ValidationError(
                    f"File exceeds the {acquire.MAX_UPLOAD_BYTES / 1e9:.0f} GB limit."
                )
            handle.write(chunk)

    if written == 0:
        path.unlink(missing_ok=True)
        await run_db(_delete_source_rows, source_id)
        raise ValidationError("The uploaded file was empty.")

    await run_db(_attach_media, source_id, path, written)
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id, "size_bytes": written}


@router.post("/upload/init")
async def upload_init(
    filename: Annotated[str, Body(embed=True)],
    total_bytes: Annotated[int, Body(embed=True)] = 0,
    folder_id: Annotated[int | None, Body(embed=True)] = None,
) -> dict[str, Any]:
    """Open a resumable upload."""
    source_id, token, path = await run_db(
        acquire.begin_upload,
        filename=filename,
        total_bytes=total_bytes,
        folder_id=folder_id,
    )
    return {"source_id": source_id, "upload_token": token, "path": path.name}


@router.post("/upload/chunk")
async def upload_chunk(
    request: Request,
    token: Annotated[str, Query()],
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Append one chunk at a byte offset. Idempotent, so a retried chunk is safe."""
    data = await request.body()
    if not data:
        raise ValidationError("Empty chunk.")
    received = await run_db(acquire.append_chunk, token, data, offset=offset)
    return {"received_bytes": received}


@router.post("/upload/complete")
async def upload_complete(token: Annotated[str, Body(embed=True)]) -> dict[str, Any]:
    source_id, _ = await run_db(acquire.finish_upload, token)
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id}


# --------------------------------------------------------------------------- other inputs


@router.post("/url")
async def add_url(payload: UrlRequest) -> dict[str, Any]:
    url = payload.url.strip()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("Enter a valid http or https URL.")
    url = parsed.geturl()

    youtube = is_youtube(url)
    source_id = await run_db(
        acquire.create_source,
        title=url,
        kind=SourceKind.YOUTUBE if youtube else SourceKind.WEB,
        origin=SourceOrigin.URL,
        folder_id=payload.folder_id,
        metadata={"url": url},
    )
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id, "kind": "youtube" if youtube else "web"}


@router.post("/text")
async def add_text(payload: TextRequest) -> dict[str, Any]:
    source_id = await run_db(
        acquire.create_text_source,
        title=payload.title.strip() or "Pasted text",
        content=payload.content,
        folder_id=payload.folder_id,
    )
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id}


# --------------------------------------------------------------------------- recording


@router.post("/record/start")
async def record_start(payload: RecordStartRequest) -> dict[str, Any]:
    from ..providers import registry

    source_id, token, _ = await run_db(
        acquire.begin_recording, folder_id=payload.folder_id, title=payload.title
    )
    # PRD §7.1: warn on start, never after 90 minutes of recording.
    stt_ready = await run_db(registry.has_stt)
    return {
        "source_id": source_id,
        "upload_token": token,
        "stt_configured": stt_ready,
        "warning": None
        if stt_ready
        else "No transcription provider is configured. You can record now, but the audio "
        "cannot be transcribed until you add one in Settings → Providers.",
    }


@router.post("/record/chunk")
async def record_chunk(request: Request, token: Annotated[str, Query()]) -> dict[str, Any]:
    data = await request.body()
    if not data:
        return {"received_bytes": 0}
    received = await run_db(acquire.append_chunk, token, data)
    return {"received_bytes": received}


@router.post("/{source_id}/marker")
async def add_marker(source_id: int, payload: MarkerRequest) -> dict[str, Any]:
    markers = await run_db(acquire.add_marker, source_id, payload.position_ms)
    return {"markers": markers}


@router.post("/record/stop")
async def record_stop(token: Annotated[str, Body(embed=True)]) -> dict[str, Any]:
    source_id, path = await run_db(acquire.finish_upload, token)
    if path.stat().st_size < 1024:
        raise ValidationError("The recording is too short to process.")
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id}


@router.get("/recordings/unfinished")
def list_unfinished() -> dict[str, Any]:
    """Recordings interrupted by a closed tab, offered for recovery on next load."""
    return {"recordings": acquire.unfinished_recordings()}


@router.post("/{source_id}/process-unfinished")
async def process_unfinished(source_id: int) -> dict[str, Any]:
    def prepare() -> None:
        with session_scope() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise NotFoundError("Recording not found.")
            path = Path(source.media_path) if source.media_path else None
            if path is None or not path.exists() or path.stat().st_size < 1024:
                raise ValidationError("That recording has no usable audio.")
            source.status = SourceStatus.PENDING
            source.size_bytes = path.stat().st_size

    await run_db(prepare)
    job_id = await run_db(enqueue_ingest, source_id)
    return {"source_id": source_id, "job_id": job_id}


@router.delete("/recordings/unfinished/{source_id}")
async def discard_unfinished(source_id: int) -> dict[str, Any]:
    await run_db(_delete_source_rows, source_id)
    return {"deleted": True}


# --------------------------------------------------------------------------- listing


@router.get("")
def list_sources(
    folder_id: int | None = None,
    status: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(Source)
        if folder_id is not None:
            stmt = stmt.where(Source.folder_id == folder_id)
        if status:
            stmt = stmt.where(Source.status == SourceStatus(status))
        if q:
            stmt = stmt.where(Source.title.ilike(f"%{q}%"))
        if tag:
            stmt = stmt.join(Source.tags).where(Tag.name == tag)

        total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
        rows = list(
            session.execute(
                stmt.order_by(Source.created_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
        return {
            "sources": [source_dict(source) for source in rows],
            "total": int(total),
        }


@router.get("/{source_id}")
def get_source(source_id: int) -> dict[str, Any]:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} not found.")

        note = note_for(session, source_id)
        simplified = note_for(session, source_id, simplified=True)
        transcript = session.execute(
            select(Transcript).where(Transcript.source_id == source_id)
        ).scalar_one_or_none()

        counts = {
            "segments": int(
                session.execute(
                    select(func.count()).select_from(Segment).where(Segment.source_id == source_id)
                ).scalar_one()
            ),
            "concepts": int(
                session.execute(
                    select(func.count()).select_from(Concept).where(Concept.source_id == source_id)
                ).scalar_one()
            ),
            "cards": int(
                session.execute(
                    select(func.count()).select_from(Card).where(Card.source_id == source_id)
                ).scalar_one()
            ),
            "quiz_items": int(
                session.execute(
                    select(func.count())
                    .select_from(QuizItem)
                    .where(QuizItem.source_id == source_id, QuizItem.ephemeral.is_(False))
                ).scalar_one()
            ),
            # Local patch: the Learn tab's badge.
            "lessons": int(
                session.execute(
                    select(func.count())
                    .select_from(Lesson)
                    .join(Course, Course.id == Lesson.course_id)
                    .where(Course.source_id == source_id)
                ).scalar_one()
            ),
        }

        return {
            "source": source_dict(source),
            "note": note_dict(note) if note else None,
            "simplified_note": note_dict(simplified) if simplified else None,
            "transcript": {
                "language": transcript.language,
                "provider": transcript.provider,
                "model": transcript.model,
            }
            if transcript
            else None,
            "counts": counts,
            "jobs": jq.list_jobs(limit=20),
        }


@router.patch("/{source_id}")
def update_source(source_id: int, payload: UpdateSourceRequest) -> dict[str, Any]:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} not found.")

        if payload.title is not None:
            source.title = payload.title.strip()[:400] or source.title
        if payload.folder_id is not None:
            source.folder_id = payload.folder_id or None
        if payload.tags is not None:
            wanted = {t.strip() for t in payload.tags if t.strip()}
            existing = {
                tag.name: tag
                for tag in session.execute(select(Tag).where(Tag.name.in_(list(wanted)))).scalars()
            }
            resolved = []
            for name in wanted:
                tag = existing.get(name)
                if tag is None:
                    tag = Tag(name=name)
                    session.add(tag)
                    session.flush()
                resolved.append(tag)
            source.tags = resolved

        session.flush()
        result = source_dict(source)

    if payload.title is not None:
        from ..db.search import upsert as search_upsert

        with raw_sqlite() as conn:
            search_upsert(conn, "source", source_id, title=result["title"], source_id=source_id)
    return result


@router.get("/{source_id}/segments")
def get_segments(source_id: int, min_relevance: float | None = None) -> dict[str, Any]:
    with session_scope() as session:
        stmt = select(Segment).where(Segment.source_id == source_id)
        if min_relevance is not None:
            stmt = stmt.where(Segment.relevance >= min_relevance)
        rows = list(session.execute(stmt.order_by(Segment.index)).scalars())
    return {"segments": [segment_dict(segment) for segment in rows]}


@router.get("/{source_id}/transcript")
def get_transcript(source_id: int) -> dict[str, Any]:
    with session_scope() as session:
        transcript = session.execute(
            select(Transcript).where(Transcript.source_id == source_id)
        ).scalar_one_or_none()
        rows = list(
            session.execute(
                select(Segment).where(Segment.source_id == source_id).order_by(Segment.index)
            ).scalars()
        )
    return {
        "transcript": {
            "language": transcript.language if transcript else None,
            "provider": transcript.provider if transcript else None,
            "model": transcript.model if transcript else None,
        },
        "segments": [segment_dict(segment) for segment in rows],
        "text": "\n".join(segment.text for segment in rows),
    }


@router.get("/{source_id}/concepts")
def get_concepts(source_id: int) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Concept)
                .where(Concept.source_id == source_id)
                .order_by(Concept.importance.desc(), Concept.id)
            ).scalars()
        )
    return {"concepts": [concept_dict(concept) for concept in rows]}


# --------------------------------------------------------------------------- media


@router.get("/{source_id}/media")
def get_media(source_id: int) -> FileResponse:
    """Serve the original file, for the audio player and PDF viewer."""
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None or not source.media_path:
            raise NotFoundError("No media file for this source.")
        path = Path(source.media_path)
        title = source.title

    if not path.exists():
        raise NotFoundError("The media file is missing from disk.")
    return FileResponse(
        path,
        filename=f"{title}{path.suffix}",
        # Range support is what lets the audio player seek without downloading the whole file.
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/{source_id}/audio")
def get_audio(source_id: int) -> FileResponse:
    """Serve the normalised WAV, which every browser can decode."""
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        candidate = source.audio_path or source.media_path

    if not candidate or not Path(candidate).exists():
        raise NotFoundError("No audio available for this source.")
    return FileResponse(Path(candidate), headers={"Accept-Ranges": "bytes"})


# --------------------------------------------------------------------------- regenerate


@router.post("/{source_id}/regenerate")
async def regenerate(source_id: int, payload: RegenerateRequest) -> dict[str, Any]:
    """Re-run part of the pipeline.

    PRD §12.3 asks what happens when a user regenerates a note they have edited. Resolved
    here by warning rather than by silently overwriting: the response reports whether the
    note was hand-edited, and the UI confirms before proceeding.
    """

    def existing_note_is_dirty() -> bool:
        with session_scope() as session:
            notes = [note_for(session, source_id)] if payload.target in {"notes", "all"} else []
            if payload.target in {"simplified", "all"}:
                notes.append(note_for(session, source_id, simplified=True))
            return any(note is not None and note.is_dirty for note in notes)

    was_edited = await run_db(existing_note_is_dirty)

    job_ids: list[int] = []
    if payload.target in {"notes", "all"}:
        job_ids.append(
            await run_db(
                jq.enqueue,
                JobType.GENERATE_NOTES,
                payload={"style": payload.style},
                source_id=source_id,
                dedupe_key="regen",
            )
        )
    if payload.target in {"simplified", "all"}:
        job_ids.append(
            await run_db(
                jq.enqueue,
                JobType.GENERATE_SIMPLIFIED_NOTES,
                source_id=source_id,
                dedupe_key="simplified",
            )
        )
    if payload.target in {"concepts", "all"}:
        job_ids.append(
            await run_db(
                jq.enqueue, JobType.GENERATE_CONCEPTS, source_id=source_id, dedupe_key="regen"
            )
        )
    if payload.target in {"cards", "all"}:
        job_ids.append(
            await run_db(
                jq.enqueue,
                JobType.GENERATE_CARDS,
                payload={"max_cards": payload.max_cards} if payload.max_cards else {},
                source_id=source_id,
                dedupe_key="regen",
            )
        )
    if payload.target in {"quiz", "all"}:
        job_ids.append(
            await run_db(
                jq.enqueue,
                JobType.GENERATE_QUIZ,
                payload={"count": payload.count},
                source_id=source_id,
                dedupe_key="regen",
            )
        )

    return {"job_ids": job_ids, "note_was_edited": was_edited}


@router.post("/{source_id}/reprocess")
async def reprocess(source_id: int) -> dict[str, Any]:
    job_id = await run_db(enqueue_ingest, source_id)
    return {"job_id": job_id}


# --------------------------------------------------------------------------- delete


def _attach_media(source_id: int, path: Path, size: int) -> None:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.media_path = str(path)
            source.size_bytes = size


def _delete_source_rows(source_id: int) -> None:
    """Delete a source and everything derived from it, including files on disk."""
    from ..config import get_config

    rag_index.remove_source(source_id)
    with raw_sqlite() as conn:
        search_delete_source(conn, source_id)
    with session_scope() as session:
        session.execute(delete(Source).where(Source.id == source_id))

    media_dir = get_config().media_dir_for(source_id)
    if media_dir.exists():
        import shutil

        shutil.rmtree(media_dir, ignore_errors=True)


@router.delete("/{source_id}")
async def delete_source(source_id: int) -> dict[str, Any]:
    def exists() -> bool:
        with session_scope() as session:
            return session.get(Source, source_id) is not None

    if not await run_db(exists):
        raise NotFoundError(f"Source {source_id} not found.")
    await run_db(_delete_source_rows, source_id)
    return {"deleted": True, "id": source_id}
