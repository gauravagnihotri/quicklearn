"""Shared API helpers: serialisation and the threadpool bridge.

Database access is synchronous by design (see :mod:`quicklearn.db.session`). Endpoints that
only touch the database are declared with ``def`` so Starlette runs them in its threadpool.
Endpoints that also make provider calls are ``async def`` and wrap their database work in
:func:`run_db`, which keeps a transaction from being held open across a network call.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from ..db.models import (
    Card,
    Chat,
    Concept,
    Folder,
    Message,
    Note,
    Podcast,
    QuizItem,
    Segment,
    Source,
)
from ..pipeline.segment import format_timestamp

P = ParamSpec("P")
R = TypeVar("R")


async def run_db(fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run a synchronous database function off the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def source_dict(source: Source, *, tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": source.id,
        "title": source.title,
        "kind": str(source.kind),
        "origin": str(source.origin),
        "status": str(source.status),
        "folder_id": source.folder_id,
        "original_filename": source.original_filename,
        "duration_sec": source.duration_sec,
        "page_count": source.page_count,
        "size_bytes": source.size_bytes,
        "has_media": bool(source.media_path),
        "has_audio": bool(source.audio_path),
        "metadata": source.meta or {},
        "error": source.error,
        "tags": tags if tags is not None else [tag.name for tag in source.tags],
        "created_at": iso(source.created_at),
        "updated_at": iso(source.updated_at),
    }


def segment_dict(segment: Segment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "index": segment.index,
        "text": segment.text,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "timestamp": format_timestamp(segment.start_ms),
        "page_no": segment.page_no,
        "bbox": segment.bbox,
        "speaker": segment.speaker,
        "relevance": round(segment.relevance, 3),
        "is_marked": segment.is_marked,
        "source_id": segment.source_id,
    }


def note_dict(note: Note, *, include_doc: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": note.id,
        "source_id": note.source_id,
        "title": note.title,
        "outline": note.outline or [],
        "style": note.style,
        "version": note.version,
        "is_dirty": note.is_dirty,
        "created_at": iso(note.created_at),
        "updated_at": iso(note.updated_at),
    }
    if include_doc:
        payload["doc"] = note.doc or {"type": "doc", "content": []}
    return payload


def concept_dict(concept: Concept) -> dict[str, Any]:
    return {
        "id": concept.id,
        "source_id": concept.source_id,
        "name": concept.name,
        "definition": concept.definition,
        "importance": concept.importance,
        "mastery": round(concept.mastery, 3),
        "segment_ids": list(concept.segment_ids or []),
    }


def card_dict(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "source_id": card.source_id,
        "concept_id": card.concept_id,
        "type": str(card.type),
        "front": card.front,
        "back": card.back,
        "state": str(card.state),
        "due": iso(card.due),
        "reps": card.reps,
        "lapses": card.lapses,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "suspended": card.suspended,
        "segment_ids": list(card.segment_ids or []),
    }


def quiz_item_dict(item: QuizItem, *, include_answer: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "source_id": item.source_id,
        "concept_id": item.concept_id,
        "type": str(item.type),
        "question": item.question,
        "options": list(item.options or []),
        "difficulty": item.difficulty,
        "segment_ids": list(item.segment_ids or []),
    }
    if include_answer:
        # Withheld by default: the answer travels to the browser, and shipping it alongside
        # the question would let anyone reading the network tab pass a test.
        payload["answer"] = item.answer
        payload["explanation"] = item.explanation
    return payload


def folder_dict(folder: Folder, *, source_count: int = 0) -> dict[str, Any]:
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "color": folder.color,
        "position": folder.position,
        "source_count": source_count,
        "created_at": iso(folder.created_at),
    }


def chat_dict(chat: Chat, *, message_count: int = 0) -> dict[str, Any]:
    return {
        "id": chat.id,
        "scope_type": str(chat.scope_type),
        "scope_id": chat.scope_id,
        "title": chat.title,
        "message_count": message_count,
        "created_at": iso(chat.created_at),
    }


def message_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "role": str(message.role),
        "content": message.content,
        "citations": list(message.citations or []),
        "followups": list(message.followups or []),
        "created_at": iso(message.created_at),
    }


def podcast_dict(podcast: Podcast) -> dict[str, Any]:
    return {
        "id": podcast.id,
        "title": podcast.title,
        "source_id": podcast.source_id,
        "source_ids": list(podcast.source_ids or []),
        "script": list(podcast.script or []),
        "status": str(podcast.status),
        "duration_sec": podcast.duration_sec,
        "target_minutes": podcast.target_minutes,
        "voice_config": podcast.voice_config or {},
        "has_audio": bool(podcast.audio_path),
        "error": podcast.error,
        "created_at": iso(podcast.created_at),
    }
