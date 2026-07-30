"""Stage 1 — get bytes onto disk and create the ``Source`` row (PRD §6.1).

Everything lands at ``/data/media/<source_id>/original.<ext>`` so that deleting one directory
removes every trace of a source, and backing up one directory captures everything.
"""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

from ..config import get_config
from ..db.enums import SourceKind, SourceOrigin, SourceStatus
from ..db.models import Source, UploadSession
from ..db.search import upsert as search_upsert
from ..db.session import raw_sqlite, session_scope
from ..errors import ValidationError

log = logging.getLogger(__name__)

#: PRD §6.1 — hard upload ceiling.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

EXTENSION_KINDS: dict[str, SourceKind] = {
    ".mp3": SourceKind.AUDIO,
    ".wav": SourceKind.AUDIO,
    ".m4a": SourceKind.AUDIO,
    ".aac": SourceKind.AUDIO,
    ".ogg": SourceKind.AUDIO,
    ".oga": SourceKind.AUDIO,
    ".opus": SourceKind.AUDIO,
    ".flac": SourceKind.AUDIO,
    ".wma": SourceKind.AUDIO,
    ".aiff": SourceKind.AUDIO,
    ".weba": SourceKind.AUDIO,
    ".mp4": SourceKind.VIDEO,
    ".mov": SourceKind.VIDEO,
    ".mkv": SourceKind.VIDEO,
    ".avi": SourceKind.VIDEO,
    ".webm": SourceKind.VIDEO,
    ".m4v": SourceKind.VIDEO,
    ".wmv": SourceKind.VIDEO,
    ".flv": SourceKind.VIDEO,
    ".pdf": SourceKind.PDF,
    ".docx": SourceKind.DOCX,
    ".doc": SourceKind.DOCX,
    ".pptx": SourceKind.PPTX,
    ".ppt": SourceKind.PPTX,
    ".png": SourceKind.IMAGE,
    ".jpg": SourceKind.IMAGE,
    ".jpeg": SourceKind.IMAGE,
    ".gif": SourceKind.IMAGE,
    ".webp": SourceKind.IMAGE,
    ".bmp": SourceKind.IMAGE,
    ".heic": SourceKind.IMAGE,
    ".tiff": SourceKind.IMAGE,
    ".txt": SourceKind.TEXT,
    ".md": SourceKind.TEXT,
    ".markdown": SourceKind.TEXT,
    ".rtf": SourceKind.TEXT,
    ".csv": SourceKind.TEXT,
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]")


def sanitise_filename(name: str) -> str:
    """Strip anything that could escape the media directory.

    The filename comes from a browser upload, so it is untrusted input that ends up in a
    filesystem path. Directory components are dropped entirely rather than escaped.
    """
    cleaned = _UNSAFE.sub("_", Path(name).name).strip(". ")
    return cleaned[:180] or "upload"


def kind_for_filename(name: str) -> SourceKind:
    suffix = Path(name).suffix.lower()
    kind = EXTENSION_KINDS.get(suffix)
    if kind is None:
        raise ValidationError(
            f"Unsupported file type '{suffix or name}'. quicklearn accepts audio, video, PDF, "
            "DOCX, PPTX, images and plain text."
        )
    return kind


def is_timed_kind(kind: SourceKind) -> bool:
    return kind in {SourceKind.AUDIO, SourceKind.VIDEO, SourceKind.YOUTUBE}


def create_source(
    *,
    title: str,
    kind: SourceKind,
    origin: SourceOrigin,
    folder_id: int | None = None,
    original_filename: str | None = None,
    metadata: dict[str, object] | None = None,
    status: SourceStatus = SourceStatus.PENDING,
) -> int:
    with session_scope() as session:
        source = Source(
            title=title.strip() or "Untitled",
            kind=kind,
            origin=origin,
            folder_id=folder_id,
            original_filename=original_filename,
            meta=dict(metadata or {}),
            status=status,
        )
        session.add(source)
        session.flush()
        source_id = source.id

    with raw_sqlite() as conn:
        search_upsert(conn, "source", source_id, title=title, source_id=source_id)

    get_config().media_dir_for(source_id).mkdir(parents=True, exist_ok=True)
    return source_id


def media_path_for(source_id: int, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    return get_config().media_dir_for(source_id) / f"original{suffix}"


def begin_upload(
    *, filename: str, total_bytes: int, folder_id: int | None = None
) -> tuple[int, str, Path]:
    """Open a resumable upload session. Returns ``(source_id, token, path)``."""
    if total_bytes > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"That file is {total_bytes / 1e9:.1f} GB. The limit is "
            f"{MAX_UPLOAD_BYTES / 1e9:.0f} GB."
        )

    safe_name = sanitise_filename(filename)
    kind = kind_for_filename(safe_name)
    source_id = create_source(
        title=Path(safe_name).stem.replace("_", " ").replace("-", " ").strip() or safe_name,
        kind=kind,
        origin=SourceOrigin.UPLOAD,
        folder_id=folder_id,
        original_filename=safe_name,
    )

    path = media_path_for(source_id, safe_name)
    token = secrets.token_urlsafe(24)

    with session_scope() as session:
        session.add(
            UploadSession(
                token=token,
                source_id=source_id,
                filename=safe_name,
                path=str(path),
                total_bytes=total_bytes,
            )
        )
    return source_id, token, path


def _load_upload(session: object, token: str) -> UploadSession | None:
    from sqlalchemy import select

    return session.execute(  # type: ignore[attr-defined,no-any-return]
        select(UploadSession).where(UploadSession.token == token)
    ).scalar_one_or_none()


def append_chunk(token: str, data: bytes, *, offset: int | None = None) -> int:
    """Write a chunk. Returns the total bytes now on disk.

    Two modes, matching the two callers:

    * ``offset is None`` — sequential append, used by browser recording. Chunks arrive every
      five seconds and go straight to disk as crash insurance (PRD §7.1): buffering a
      90-minute lecture in a tab and losing it to a crash is a catastrophic failure mode.
    * ``offset`` given — positioned write, used by resumable upload. Writing at an explicit
      offset makes a retried chunk idempotent; appending it would duplicate those bytes and
      silently corrupt the file.
    """
    with session_scope() as session:
        upload = _load_upload(session, token)
        if upload is None:
            raise ValidationError("Unknown or expired upload token.")
        if upload.completed:
            raise ValidationError("This upload has already been finalised.")

        path = Path(upload.path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if offset is None:
            with path.open("ab") as handle:
                handle.write(data)
            upload.received_bytes += len(data)
        else:
            if not path.exists():
                path.touch()
            with path.open("r+b") as handle:
                handle.seek(offset)
                handle.write(data)
            upload.received_bytes = max(upload.received_bytes, offset + len(data))

        upload.chunk_count += 1
        return upload.received_bytes


def finish_upload(token: str) -> tuple[int, Path]:
    """Mark an upload complete and record the media path on the source."""
    with session_scope() as session:
        upload = _load_upload(session, token)
        if upload is None:
            raise ValidationError("Unknown or expired upload token.")
        upload.completed = True
        path = Path(upload.path)
        source_id = upload.source_id
        if source_id is None:
            raise ValidationError("Upload session is not attached to a source.")

        source = session.get(Source, source_id)
        if source is not None:
            source.media_path = str(path)
            source.size_bytes = path.stat().st_size if path.exists() else 0
            source.status = SourceStatus.PENDING

    if not path.exists() or path.stat().st_size == 0:
        raise ValidationError("The upload finished but no data was received.")
    return source_id, path


def create_text_source(*, title: str, content: str, folder_id: int | None = None) -> int:
    if not content.strip():
        raise ValidationError("There is no text to ingest.")
    source_id = create_source(
        title=title or "Pasted text",
        kind=SourceKind.TEXT,
        origin=SourceOrigin.PASTE,
        folder_id=folder_id,
        metadata={"characters": len(content)},
    )
    path = get_config().media_dir_for(source_id) / "original.txt"
    path.write_text(content, encoding="utf-8")

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.media_path = str(path)
            source.size_bytes = len(content.encode())
    return source_id


def begin_recording(*, folder_id: int | None = None, title: str = "") -> tuple[int, str, Path]:
    """Start a browser recording session (PRD §7.1)."""
    from ..db.types import utcnow

    stamp = utcnow().strftime("%Y-%m-%d %H:%M")
    source_id = create_source(
        title=title or f"Recording {stamp}",
        kind=SourceKind.AUDIO,
        origin=SourceOrigin.RECORDING,
        folder_id=folder_id,
        original_filename="recording.webm",
        status=SourceStatus.RECORDING,
        metadata={"markers": []},
    )
    path = get_config().media_dir_for(source_id) / "original.webm"
    path.write_bytes(b"")
    token = secrets.token_urlsafe(24)

    with session_scope() as session:
        session.add(
            UploadSession(
                token=token,
                source_id=source_id,
                filename="recording.webm",
                path=str(path),
                is_recording=True,
            )
        )
        source = session.get(Source, source_id)
        if source is not None:
            source.media_path = str(path)
    return source_id, token, path


def add_marker(source_id: int, position_ms: int) -> list[int]:
    """Record an importance marker tapped during a recording.

    These feed the relevance pass, where marked segments are pinned high — the user pressed a
    button to say "this matters", and no classifier should overrule that.
    """
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValidationError("Recording not found.")
        meta = dict(source.meta or {})
        markers = list(meta.get("markers") or [])
        markers.append(int(position_ms))
        meta["markers"] = sorted(set(markers))
        source.meta = meta
        return list(meta["markers"])


def unfinished_recordings() -> list[dict[str, object]]:
    """Recordings left in progress by a closed tab or a crash (PRD §7.1)."""
    from sqlalchemy import select

    with session_scope() as session:
        rows = list(
            session.execute(select(Source).where(Source.status == SourceStatus.RECORDING)).scalars()
        )
        result = []
        for source in rows:
            path = Path(source.media_path) if source.media_path else None
            size = path.stat().st_size if path and path.exists() else 0
            result.append(
                {
                    "id": source.id,
                    "title": source.title,
                    "created_at": source.created_at.isoformat(),
                    "size_bytes": size,
                    "has_data": size > 0,
                }
            )
        return result
