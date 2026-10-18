"""Hybrid retrieval: vector + BM25, fused with reciprocal rank fusion, then reranked.

PRD §7.5. The two retrievers fail in complementary ways, which is the whole argument for
running both: BM25 misses a paraphrase that shares no vocabulary with the source, and vector
search misses an exact identifier — a specific theorem name, a variable, a date — because
embeddings smear rare tokens into their neighbourhood.

RRF fuses on *rank* rather than score, which matters because BM25 scores and cosine
similarities are not on a comparable scale and normalising them into one is guesswork.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from .. import prompts
from ..db.models import Segment, Source
from ..db.search import search as fts_search
from ..db.session import raw_sqlite, session_scope
from ..pipeline.schemas import RerankScores
from ..pipeline.segment import format_timestamp
from ..providers import gateway
from ..providers.base import text_message
from ..settings import get_settings
from . import index as vector_index

log = logging.getLogger(__name__)

#: The conventional RRF constant. Damps the influence of top ranks so one retriever's
#: confident-but-wrong first result cannot dominate the fusion.
RRF_K = 60


@dataclass(slots=True)
class Excerpt:
    id: int
    text: str
    source_id: int
    source_title: str
    timestamp: str = ""
    page_no: int | None = None
    start_ms: int | None = None
    score: float = 0.0
    #: Which retrievers found it — useful for debugging retrieval quality.
    matched_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "timestamp": self.timestamp,
            "page_no": self.page_no,
            "start_ms": self.start_ms,
            "score": round(self.score, 4),
            "matched_by": self.matched_by,
        }


def reciprocal_rank_fusion(
    rankings: dict[str, list[int]], *, k: int = RRF_K
) -> list[tuple[int, float, list[str]]]:
    """Fuse ranked id lists. Returns ``(id, score, sources)`` sorted best-first."""
    scores: dict[int, float] = {}
    origins: dict[int, list[str]] = {}

    for name, ordered_ids in rankings.items():
        for rank, item_id in enumerate(ordered_ids):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            origins.setdefault(item_id, []).append(name)

    return sorted(
        ((item_id, score, origins[item_id]) for item_id, score in scores.items()),
        key=lambda row: row[1],
        reverse=True,
    )


def _scope_source_ids(scope_type: str, scope_id: int | None) -> list[int] | None:
    """Resolve a chat scope to source ids. ``None`` means the whole library."""
    if scope_type == "source" and scope_id is not None:
        return [scope_id]
    if scope_type == "folder" and scope_id is not None:
        from ..db.models import Folder

        with session_scope() as session:
            # Folders nest, so a folder scope must include descendants or a user who filed
            # lectures into subfolders would get nothing.
            wanted = {scope_id}
            frontier = [scope_id]
            while frontier:
                children = list(
                    session.execute(
                        select(Folder.id).where(Folder.parent_id.in_(frontier))
                    ).scalars()
                )
                children = [c for c in children if c not in wanted]
                wanted.update(children)
                frontier = children
            return list(
                session.execute(
                    select(Source.id).where(Source.folder_id.in_(list(wanted)))
                ).scalars()
            ) or [-1]  # a sentinel that matches nothing, rather than silently going global
    return None


def _load_excerpts(segment_ids: list[int]) -> dict[int, Excerpt]:
    if not segment_ids:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(Segment, Source.title)
            .join(Source, Source.id == Segment.source_id)
            .where(Segment.id.in_(segment_ids))
        ).all()

    return {
        segment.id: Excerpt(
            id=segment.id,
            text=segment.text,
            source_id=segment.source_id,
            source_title=title,
            timestamp=format_timestamp(segment.start_ms)
            if segment.start_ms is not None
            else (f"p.{segment.page_no}" if segment.page_no else ""),
            page_no=segment.page_no,
            start_ms=segment.start_ms,
        )
        for segment, title in rows
    }


async def retrieve(
    query: str,
    *,
    scope_type: str = "global",
    scope_id: int | None = None,
    top_k: int | None = None,
    rerank_to: int | None = None,
    use_rerank: bool = True,
) -> list[Excerpt]:
    """Run hybrid retrieval and return the reranked excerpts."""
    settings = get_settings()
    top_k = top_k or settings.get_int("rag.top_k", 12)
    rerank_to = rerank_to or settings.get_int("rag.rerank_to", 6)
    source_ids = _scope_source_ids(scope_type, scope_id)

    async def vector_leg() -> list[int]:
        try:
            hits = await vector_index.search_vectors(query, k=top_k, source_ids=source_ids)
            return [hit.segment_id for hit in hits]
        except Exception as exc:
            # Embeddings may be unconfigured or the model still downloading. BM25 alone still
            # produces a usable answer, so degrade rather than fail.
            log.warning("vector retrieval unavailable, using BM25 only: %s", exc)
            return []

    def keyword_leg() -> list[int]:
        with raw_sqlite() as conn:
            hits = fts_search(
                conn, query, kinds=["segment"], source_ids=source_ids, limit=top_k, any_terms=True
            )
        return [hit.entity_id for hit in hits]

    vector_ids, keyword_ids = await asyncio.gather(vector_leg(), asyncio.to_thread(keyword_leg))

    fused = reciprocal_rank_fusion({"vector": vector_ids, "keyword": keyword_ids})
    if not fused:
        return []

    candidate_ids = [item_id for item_id, _, _ in fused[:top_k]]
    excerpts = _load_excerpts(candidate_ids)

    ordered: list[Excerpt] = []
    for item_id, score, origins in fused[:top_k]:
        excerpt = excerpts.get(item_id)
        if excerpt is None:
            continue  # segment deleted since indexing
        excerpt.score = score
        excerpt.matched_by = origins
        ordered.append(excerpt)

    if not use_rerank or len(ordered) <= rerank_to:
        return ordered[:rerank_to]

    return await rerank(query, ordered, limit=rerank_to)


async def rerank(query: str, excerpts: list[Excerpt], *, limit: int) -> list[Excerpt]:
    """Narrow candidates with a cheap relevance judgement from the `fast` model."""
    prompt = prompts.render(
        "chat_rerank",
        query=query,
        excerpts=[{"id": e.id, "text": e.text[:1500]} for e in excerpts],
    )
    try:
        result = await gateway.complete_structured(
            "rerank",
            [text_message("user", prompt)],
            RerankScores,
            task_class="fast",
            temperature=0.0,
            max_tokens=1200,
        )
    except Exception as exc:
        # Fusion order is already reasonable; reranking is an improvement, not a requirement.
        log.warning("rerank failed, keeping fusion order: %s", exc)
        return excerpts[:limit]

    scored = {item.id: item.score for item in result.scores}
    ranked = sorted(
        excerpts,
        key=lambda e: (scored.get(e.id, 0.0), e.score),
        reverse=True,
    )
    # Drop anything the reranker judged actively irrelevant, but never return nothing —
    # an empty context turns into "your notes don't cover this", which would be wrong here.
    kept = [e for e in ranked if scored.get(e.id, 0.0) > 0.05][:limit]
    return kept or ranked[:limit]


def scope_description(scope_type: str, scope_id: int | None) -> str:
    if scope_type == "source" and scope_id is not None:
        with session_scope() as session:
            source = session.get(Source, scope_id)
            return f"the source “{source.title}”" if source else "one source"
    if scope_type == "folder" and scope_id is not None:
        from ..db.models import Folder

        with session_scope() as session:
            folder = session.get(Folder, scope_id)
            return f"the folder “{folder.name}”" if folder else "one folder"
    return "your entire library"
