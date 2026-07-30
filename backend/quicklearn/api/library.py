"""Folders, tags, global search and the dashboard (PRD §7.7)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db.models import Card, Concept, Folder, Note, NoteBlock, Segment, Source, Tag
from ..db.search import search as fts_search
from ..db.session import raw_sqlite, session_scope
from ..errors import NotFoundError, ValidationError
from ..jobs import queue as jq
from ..rag import index as rag_index
from ..study import mastery, review
from .common import folder_dict, run_db, source_dict

log = logging.getLogger(__name__)
router = APIRouter(tags=["library"])


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    color: str | None = None


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    color: str | None = None
    position: int | None = None


# --------------------------------------------------------------------------- folders


@router.get("/folders")
def list_folders() -> dict[str, Any]:
    """Full folder tree with per-folder source counts."""
    with session_scope() as session:
        folders = list(
            session.execute(select(Folder).order_by(Folder.position, Folder.name)).scalars()
        )
        counts = dict(
            session.execute(
                select(Source.folder_id, func.count(Source.id))
                .where(Source.folder_id.is_not(None))
                .group_by(Source.folder_id)
            ).all()
        )
        unfiled = int(
            session.execute(
                select(func.count()).select_from(Source).where(Source.folder_id.is_(None))
            ).scalar_one()
        )
        payload = [
            folder_dict(folder, source_count=int(counts.get(folder.id, 0))) for folder in folders
        ]

    return {"folders": payload, "unfiled_count": unfiled}


@router.post("/folders")
def create_folder(payload: FolderRequest) -> dict[str, Any]:
    with session_scope() as session:
        if payload.parent_id is not None and session.get(Folder, payload.parent_id) is None:
            raise NotFoundError("Parent folder not found.")
        folder = Folder(name=payload.name.strip(), parent_id=payload.parent_id, color=payload.color)
        session.add(folder)
        session.flush()
        return folder_dict(folder)


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: int, payload: FolderPatch) -> dict[str, Any]:
    with session_scope() as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            raise NotFoundError(f"Folder {folder_id} not found.")

        if payload.parent_id is not None:
            if payload.parent_id == folder_id:
                raise ValidationError("A folder cannot be its own parent.")
            # Walk up from the proposed parent; if we reach this folder, the move would
            # create a cycle and orphan the whole subtree from the tree view.
            cursor = session.get(Folder, payload.parent_id) if payload.parent_id else None
            seen = 0
            while cursor is not None and seen < 64:
                if cursor.id == folder_id:
                    raise ValidationError("That move would nest a folder inside itself.")
                cursor = session.get(Folder, cursor.parent_id) if cursor.parent_id else None
                seen += 1
            folder.parent_id = payload.parent_id or None

        if payload.name is not None:
            folder.name = payload.name.strip() or folder.name
        if payload.color is not None:
            folder.color = payload.color
        if payload.position is not None:
            folder.position = payload.position

        session.flush()
        return folder_dict(folder)


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, delete_sources: bool = False) -> dict[str, Any]:
    """Delete a folder. Sources are unfiled by default rather than destroyed."""
    with session_scope() as session:
        folder = session.get(Folder, folder_id)
        if folder is None:
            raise NotFoundError(f"Folder {folder_id} not found.")
        source_ids = list(
            session.execute(select(Source.id).where(Source.folder_id == folder_id)).scalars()
        )
        if not delete_sources:
            for source in session.execute(
                select(Source).where(Source.folder_id == folder_id)
            ).scalars():
                source.folder_id = None
        session.delete(folder)

    if delete_sources:
        from .sources import _delete_source_rows

        for source_id in source_ids:
            _delete_source_rows(source_id)

    return {"deleted": True, "sources_affected": len(source_ids)}


@router.get("/tags")
def list_tags() -> dict[str, Any]:
    with session_scope() as session:
        rows = list(session.execute(select(Tag).order_by(Tag.name)).scalars())
        return {"tags": [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in rows]}


# --------------------------------------------------------------------------- search


@router.get("/search")
async def search(
    q: str = Query(min_length=1),
    semantic: bool = False,
    kinds: str | None = None,
    limit: int = Query(default=40, le=200),
) -> dict[str, Any]:
    """Global search (Cmd+K).

    Keyword search is FTS5 across notes, transcripts, cards, concepts and titles. The semantic
    toggle adds vector hits over segments, which finds paraphrases that share no vocabulary
    with the query.
    """
    wanted = [k.strip() for k in (kinds or "").split(",") if k.strip()] or None

    def keyword() -> list[dict[str, Any]]:
        with raw_sqlite() as conn:
            hits = fts_search(conn, q, kinds=wanted, limit=limit)  # type: ignore[arg-type]
        return [
            {
                "kind": hit.kind,
                "id": hit.entity_id,
                "source_id": hit.source_id,
                "title": hit.title,
                "snippet": hit.snippet,
                # BM25 is negative and more-negative-is-better; flip it so the UI can sort
                # descending like every other score in the app.
                "score": round(-hit.score, 4),
                "match": "keyword",
            }
            for hit in hits
        ]

    results = await run_db(keyword)

    if semantic:
        try:
            hits = await rag_index.search_vectors(q, k=limit)
            seen = {(r["kind"], r["id"]) for r in results}

            def hydrate() -> list[dict[str, Any]]:
                extra: list[dict[str, Any]] = []
                with session_scope() as session:
                    for hit in hits:
                        if ("segment", hit.segment_id) in seen:
                            continue
                        segment = session.get(Segment, hit.segment_id)
                        if segment is None:
                            continue
                        source = session.get(Source, segment.source_id)
                        extra.append(
                            {
                                "kind": "segment",
                                "id": segment.id,
                                "source_id": segment.source_id,
                                "title": source.title if source else "",
                                "snippet": segment.text[:280],
                                "score": round(hit.score, 4),
                                "match": "semantic",
                            }
                        )
                return extra

            results.extend(await run_db(hydrate))
        except Exception as exc:
            log.warning("semantic search unavailable: %s", exc)

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"query": q, "results": results[:limit], "semantic": semantic}


# --------------------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard() -> dict[str, Any]:
    """Everything the home screen shows (PRD §7.7)."""
    from ..providers import gateway, registry

    with session_scope() as session:
        recent = list(
            session.execute(select(Source).order_by(Source.created_at.desc()).limit(8)).scalars()
        )
        totals = {
            "sources": int(session.execute(select(func.count()).select_from(Source)).scalar_one()),
            "notes": int(session.execute(select(func.count()).select_from(Note)).scalar_one()),
            "cards": int(session.execute(select(func.count()).select_from(Card)).scalar_one()),
            "concepts": int(
                session.execute(select(func.count()).select_from(Concept)).scalar_one()
            ),
            "segments": int(
                session.execute(select(func.count()).select_from(Segment)).scalar_one()
            ),
        }
        recent_payload = [source_dict(source) for source in recent]

    weak = mastery.weak_concepts(limit=6)
    return {
        "totals": totals,
        "recent_sources": recent_payload,
        "due": review.due_counts(),
        "stats": review.review_stats(),
        "active_jobs": jq.list_jobs(limit=12, active_only=True),
        "usage": gateway.total_cost(),
        "weak_concepts": [
            {
                "id": concept.id,
                "name": concept.name,
                "mastery": round(concept.mastery, 3),
                "source_id": concept.source_id,
                "source_title": concept.source_title,
            }
            for concept in weak
        ],
        "mastery": mastery.mastery_summary(),
        "providers_configured": {
            "llm": registry.has_llm(),
            "stt": registry.has_stt(),
            "tts": registry.has_tts(),
        },
        "unfinished_recordings": len([r for r in _unfinished() if r.get("has_data")]),
    }


def _unfinished() -> list[dict[str, Any]]:
    from ..pipeline.acquire import unfinished_recordings

    try:
        return unfinished_recordings()  # type: ignore[return-value]
    except Exception:
        return []


@router.get("/blocks/{block_id}/segments")
def block_segments(block_id: int) -> dict[str, Any]:
    """Segments behind one note block — the click-to-source lookup."""
    from .common import segment_dict

    with session_scope() as session:
        block = session.get(NoteBlock, block_id)
        if block is None:
            raise NotFoundError("Block not found.")
        ids = list(block.segment_ids or [])
        rows = (
            list(session.execute(select(Segment).where(Segment.id.in_(ids))).scalars())
            if ids
            else []
        )
        rows.sort(key=lambda s: s.index)
        return {"segments": [segment_dict(segment) for segment in rows]}
