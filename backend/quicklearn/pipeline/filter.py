"""Relevance classification (PRD §6.4).

The pass that separates a study tool from a transcript dumper: it scores how much instructional
value each segment carries, so administrative announcements, tangents and cross-talk stop
polluting the generated notes.

Two design points are deliberate and worth defending:

* **Segments are never deleted.** Low scorers stay in the transcript and remain visible in the
  UI — they are only withheld from the context passed to generation. Destroying source data
  because a cheap classifier disliked it would be unforgivable, and some users want the
  tangents (hence the user-adjustable threshold).
* **Failure is non-fatal.** If the classifier errors, every segment keeps its default score of
  1.0 and generation proceeds on everything. Slightly noisier notes beat a failed ingest.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from .. import prompts
from ..db.models import Segment
from ..db.session import session_scope
from ..providers import gateway
from ..providers.base import text_message
from .schemas import RelevanceScores

log = logging.getLogger(__name__)

#: Segments per LLM call. Large enough to be cheap, small enough that the model can attend to
#: each one and that a single failure doesn't cost the whole transcript.
BATCH_SIZE = 40
MAX_PARALLEL_BATCHES = 3
DEFAULT_THRESHOLD = 0.3
#: Marked segments are pinned high — the user pressed a button to say "this matters", and no
#: classifier should overrule that.
MARKED_FLOOR = 0.95


async def classify_relevance(
    source_id: int,
    *,
    kind: str = "lecture",
    source_id_for_logging: int | None = None,
    job_id: int | None = None,
    on_progress: object = None,
) -> dict[int, float]:
    """Score every segment of a source. Returns ``{segment_id: relevance}``."""
    with session_scope() as session:
        segments = list(
            session.execute(
                select(Segment).where(Segment.source_id == source_id).order_by(Segment.index)
            ).scalars()
        )

    if not segments:
        return {}

    by_index = {segment.index: segment for segment in segments}
    has_markers = any(segment.is_marked for segment in segments)
    batches = [segments[i : i + BATCH_SIZE] for i in range(0, len(segments), BATCH_SIZE)]
    semaphore = asyncio.Semaphore(MAX_PARALLEL_BATCHES)
    scores: dict[int, float] = {}
    completed = 0

    async def run(batch: list[Segment]) -> None:
        nonlocal completed
        payload = [
            {"index": segment.index, "text": segment.text, "is_marked": segment.is_marked}
            for segment in batch
        ]
        prompt = prompts.render("relevance", kind=kind, segments=payload, has_markers=has_markers)
        async with semaphore:
            try:
                result = await gateway.complete_structured(
                    "relevance",
                    [text_message("user", prompt)],
                    RelevanceScores,
                    task_class="fast",
                    temperature=0.0,
                    source_id=source_id_for_logging or source_id,
                    job_id=job_id,
                )
            except Exception as exc:
                # Keep everything from this batch rather than dropping it.
                log.warning("relevance batch failed, keeping all its segments: %s", exc)
                for segment in batch:
                    scores[segment.id] = 1.0
                return

        returned = {score.index for score in result.scores}
        for score in result.scores:
            segment = by_index.get(score.index)
            if segment is not None:
                scores[segment.id] = max(0.0, min(1.0, score.relevance))

        # A model that skips entries must not silently zero them.
        for segment in batch:
            if segment.index not in returned:
                scores[segment.id] = 1.0

        completed += 1
        if callable(on_progress):
            on_progress(completed, len(batches))

    await asyncio.gather(*(run(batch) for batch in batches))

    for segment in segments:
        if segment.is_marked:
            scores[segment.id] = max(scores.get(segment.id, 0.0), MARKED_FLOOR)

    with session_scope() as session:
        for segment_id, value in scores.items():
            row = session.get(Segment, segment_id)
            if row is not None:
                row.relevance = value

    kept = sum(1 for v in scores.values() if v >= DEFAULT_THRESHOLD)
    log.info(
        "relevance: %d/%d segments above the default threshold for source %s",
        kept,
        len(scores),
        source_id,
    )
    return scores


def filter_for_generation(segments: list[Segment], threshold: float) -> list[Segment]:
    """Apply the threshold, with a guard against filtering everything away.

    If the classifier was pessimistic — or the material genuinely is mostly admin — returning
    nothing would produce an empty note and look like a crash. Falling back to the highest
    scorers degrades quality gracefully instead.
    """
    kept = [segment for segment in segments if segment.relevance >= threshold]
    if kept:
        return kept
    if not segments:
        return []

    log.warning("every segment scored below %.2f; falling back to the top-scoring third", threshold)
    ranked = sorted(segments, key=lambda s: s.relevance, reverse=True)
    return sorted(ranked[: max(1, len(ranked) // 3)], key=lambda s: s.index)
