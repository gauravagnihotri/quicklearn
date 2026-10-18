"""Job handlers for the ingest pipeline.

An ``ingest`` job is an orchestrator: it creates one child row per stage up front so the UI can
render the whole progress tree immediately, then runs the stages in order. Each stage is also
registered as a standalone job type, so "retry just the transcription" and "regenerate only the
notes" run exactly the same code as the full pipeline rather than a parallel implementation.

Stages are sequential because they genuinely depend on each other — you cannot segment a
transcript you have not produced. Modelling that with a dependency scheduler would be more
machinery than a strictly linear chain deserves.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from ..db.enums import JobStatus, JobType, SourceKind, SourceStatus
from ..db.models import Job, Source
from ..db.session import session_scope
from ..db.types import utcnow
from ..errors import ConfigurationError, QuicklearnError
from ..jobs import queue as jq
from ..jobs.events import emit
from ..jobs.worker import JobContext, register
from ..pipeline import stages
from ..pipeline.generate import cards as cards_gen
from ..pipeline.generate import concepts as concepts_gen
from ..pipeline.generate import notes as notes_gen
from ..pipeline.generate import quiz as quiz_gen
from ..providers import registry
from ..rag import index as rag_index

log = logging.getLogger(__name__)

#: (job type, label, progress span). Spans are weighted by how long each stage actually takes —
#: transcription dominates, so a bar that moved linearly per stage would sit at 30% for
#: minutes and then leap to 100%.
PIPELINE: list[tuple[JobType, str, int, int]] = [
    (JobType.NORMALIZE, "Preparing media", 0, 8),
    (JobType.TRANSCRIBE, "Extracting content", 8, 45),
    (JobType.FILTER, "Assessing relevance", 45, 58),
    (JobType.GENERATE_NOTES, "Writing notes", 58, 80),
    (JobType.GENERATE_CONCEPTS, "Extracting concepts", 80, 88),
    (JobType.GENERATE_CARDS, "Creating flashcards", 88, 95),
    (JobType.INDEX, "Indexing for search", 95, 100),
]


def _child_job_ids(parent_id: int, source_id: int) -> dict[JobType, int]:
    """Create the child rows for a pipeline run, so the UI shows the tree from the start."""
    ids: dict[JobType, int] = {}
    with session_scope() as session:
        for job_type, label, _, _ in PIPELINE:
            existing = session.execute(
                select(Job).where(
                    Job.source_id == source_id,
                    Job.type == job_type,
                    Job.dedupe_key == "pipeline",
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.parent_id = parent_id
                existing.status = JobStatus.QUEUED
                existing.progress = 0
                existing.stage = label
                existing.error = None
                existing.attempts = 0
                existing.started_at = None
                existing.finished_at = None
                session.flush()
                ids[job_type] = existing.id
                continue

            job = Job(
                parent_id=parent_id,
                source_id=source_id,
                type=job_type,
                status=JobStatus.QUEUED,
                stage=label,
                dedupe_key="pipeline",
            )
            session.add(job)
            session.flush()
            ids[job_type] = job.id
    return ids


def _begin_child(job_id: int, label: str) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = JobStatus.RUNNING
            job.stage = label
            job.started_at = utcnow()
            job.attempts += 1
    emit("job.progress", job_id=job_id, progress=0, stage=label)


def _finish_child(job_id: int, *, ok: bool, error: str = "", skipped: bool = False) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.SUCCEEDED if ok else JobStatus.FAILED
        job.progress = 100 if ok else job.progress
        job.finished_at = utcnow()
        job.error = error or None
        if skipped:
            job.stage = "Skipped"
    emit("job.succeeded" if ok else "job.failed", job_id=job_id, error=error)


@register(JobType.INGEST)
async def handle_ingest(ctx: JobContext) -> None:
    """Run the whole pipeline for one source."""
    source_id = ctx.source_id
    if source_id is None:
        raise ValueError("Ingest job has no source")

    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None:
            raise ValueError(f"Source {source_id} no longer exists")
        kind = source.kind
        source.status = SourceStatus.PROCESSING
        source.error = None

    emit("source.updated", source_id=source_id, status="processing")
    children = _child_job_ids(ctx.job_id, source_id)

    #: Stages that do not apply to this source type. Skipped visibly rather than silently, so
    #: a PDF's progress tree doesn't look like transcription mysteriously vanished.
    skip: set[JobType] = set()
    if kind not in {SourceKind.AUDIO, SourceKind.VIDEO}:
        skip.add(JobType.NORMALIZE)
    if not registry.has_llm():
        skip.update(
            {
                JobType.FILTER,
                JobType.GENERATE_NOTES,
                JobType.GENERATE_CONCEPTS,
                JobType.GENERATE_CARDS,
            }
        )

    runners: dict[JobType, Callable[[], Awaitable[None]]] = {
        JobType.NORMALIZE: lambda: stages.normalize(source_id),
        JobType.TRANSCRIBE: lambda: stages.extract(
            source_id,
            job_id=ctx.job_id,
            on_progress=lambda done, total: ctx.sub_progress(
                8, 37, done, total, f"Transcribing — part {done} of {total}"
            ),
        ),
        JobType.FILTER: lambda: stages.relevance(
            source_id,
            job_id=ctx.job_id,
            on_progress=lambda done, total: ctx.sub_progress(
                45, 13, done, total, f"Assessing relevance — batch {done} of {total}"
            ),
        ),
        JobType.GENERATE_NOTES: lambda: notes_gen.generate_notes(
            source_id,
            job_id=ctx.job_id,
            on_progress=lambda done, total: ctx.sub_progress(
                58, 22, done, total, f"Writing section {done} of {total}"
            ),
        ),
        JobType.GENERATE_CONCEPTS: lambda: concepts_gen.generate_concepts(
            source_id, job_id=ctx.job_id
        ),
        JobType.GENERATE_CARDS: lambda: cards_gen.generate_cards(source_id, job_id=ctx.job_id),
        JobType.INDEX: lambda: rag_index.index_source(source_id, job_id=ctx.job_id),
    }

    failures: list[str] = []

    for job_type, label, base, end in PIPELINE:
        ctx.check_cancelled()
        child_id = children[job_type]

        if job_type in skip:
            _finish_child(child_id, ok=True, skipped=True)
            continue

        _begin_child(child_id, label)
        ctx.progress(base, label)

        try:
            result = runners[job_type]()
            await result
        except QuicklearnError as exc:
            _finish_child(child_id, ok=False, error=exc.message)
            if job_type in {JobType.NORMALIZE, JobType.TRANSCRIBE}:
                # Without content there is nothing downstream can do.
                _mark_remaining_skipped(children, job_type)
                stages.set_status(source_id, SourceStatus.FAILED, exc.message)
                emit("source.updated", source_id=source_id, status="failed")
                raise
            # Everything else is additive. A source with notes but no flashcards is still
            # useful, and failing the whole ingest would throw away the transcription the
            # user already paid for.
            log.warning("stage %s failed but is not fatal: %s", job_type, exc.message)
            failures.append(f"{label}: {exc.message}")
            continue
        except Exception as exc:
            _finish_child(child_id, ok=False, error=str(exc))
            if job_type in {JobType.NORMALIZE, JobType.TRANSCRIBE}:
                _mark_remaining_skipped(children, job_type)
                stages.set_status(source_id, SourceStatus.FAILED, str(exc))
                emit("source.updated", source_id=source_id, status="failed")
                raise
            log.exception("stage %s crashed", job_type)
            failures.append(f"{label}: {exc}")
            continue

        _finish_child(child_id, ok=True)
        ctx.progress(end, label)

        if job_type is JobType.TRANSCRIBE:
            # Cheap, and turns "recording-2026-07-30" into something findable.
            try:
                await stages.autotitle(source_id)
            except Exception:
                log.debug("auto-title failed", exc_info=True)

    stages.set_status(
        source_id,
        SourceStatus.READY,
        "; ".join(failures)[:2000] if failures else None,
    )
    ctx.progress(100, "Ready")
    emit("source.updated", source_id=source_id, status="ready")

    # Local patch: the simplified note is its own job, queued once everything else is ready so
    # it never delays the standard notes, cards or quiz.
    with session_scope() as session:
        has_note = notes_gen.note_for(session, source_id) is not None
    if has_note:
        jq.enqueue(JobType.GENERATE_SIMPLIFIED_NOTES, source_id=source_id, dedupe_key="simplified")


def _mark_remaining_skipped(children: dict[JobType, int], failed_at: JobType) -> None:
    """Mark downstream children as skipped once a fatal stage fails.

    Leaving them ``queued`` forever would make the progress tree look like it is still working.
    """
    seen_failure = False
    for job_type, _, _, _ in PIPELINE:
        if job_type is failed_at:
            seen_failure = True
            continue
        if not seen_failure:
            continue
        job_id = children.get(job_type)
        if job_id is None:
            continue
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is not None and job.status is JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED
                job.stage = "Skipped — an earlier stage failed"
                job.finished_at = utcnow()


# --------------------------------------------------------------------------- single stages


@register(JobType.NORMALIZE)
async def handle_normalize(ctx: JobContext) -> None:
    ctx.progress(10, "Preparing media")
    await stages.normalize(_require_source(ctx))


@register(JobType.TRANSCRIBE)
async def handle_transcribe(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    ctx.progress(5, "Extracting content")
    await stages.extract(
        source_id,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(
            5, 90, done, total, f"Transcribing — part {done} of {total}"
        ),
    )


@register(JobType.EXTRACT)
async def handle_extract(ctx: JobContext) -> None:
    await handle_transcribe(ctx)


@register(JobType.SEGMENT)
async def handle_segment(ctx: JobContext) -> None:
    # Segmentation is performed as part of extraction, since it needs the units in memory.
    # Registered so a retry of this stage does something sensible rather than erroring.
    await handle_transcribe(ctx)


@register(JobType.FILTER)
async def handle_filter(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    await stages.relevance(
        source_id,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(
            0, 100, done, total, f"Assessing relevance — batch {done} of {total}"
        ),
    )


@register(JobType.GENERATE_NOTES)
async def handle_generate_notes(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    style = str(ctx.payload.get("style") or "")
    await notes_gen.generate_notes(
        source_id,
        style=style,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(
            10, 85, done, total, f"Writing section {done} of {total}"
        ),
    )
    emit("source.updated", source_id=source_id, status="ready")


@register(JobType.GENERATE_SIMPLIFIED_NOTES)
async def handle_generate_simplified_notes(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    await notes_gen.generate_notes(
        source_id,
        style=notes_gen.SIMPLIFIED_STYLE,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(
            10, 85, done, total, f"Writing simplified section {done} of {total}"
        ),
    )
    emit("source.updated", source_id=source_id, status="ready")


@register(JobType.GENERATE_CONCEPTS)
async def handle_generate_concepts(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    await concepts_gen.generate_concepts(
        source_id,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(
            0, 100, done, total, "Extracting concepts"
        ),
    )


@register(JobType.GENERATE_CARDS)
async def handle_generate_cards(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    max_cards = ctx.payload.get("max_cards")

    # Cards come from concepts, so extract them first if a user asks for cards on a source
    # that was ingested before concept extraction existed, or where that stage failed.
    if not _has_concepts(source_id):
        ctx.progress(10, "Extracting concepts first")
        await concepts_gen.generate_concepts(source_id, job_id=ctx.job_id)

    ctx.progress(50, "Creating flashcards")
    await cards_gen.generate_cards(
        source_id,
        max_cards=int(max_cards)
        if isinstance(max_cards, int | str) and str(max_cards).isdigit()
        else None,
        job_id=ctx.job_id,
    )


@register(JobType.GENERATE_QUIZ)
async def handle_generate_quiz(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    count = ctx.payload.get("count", 20)

    if not _has_concepts(source_id):
        ctx.progress(10, "Extracting concepts first")
        await concepts_gen.generate_concepts(source_id, job_id=ctx.job_id)

    ctx.progress(50, "Writing quiz questions")
    await quiz_gen.generate_quiz_items(
        source_id,
        count=int(count) if isinstance(count, int) else 20,
        job_id=ctx.job_id,
    )


@register(JobType.INDEX)
async def handle_index(ctx: JobContext) -> None:
    source_id = _require_source(ctx)
    await rag_index.index_source(
        source_id,
        job_id=ctx.job_id,
        on_progress=lambda done, total: ctx.sub_progress(0, 100, done, total, "Indexing"),
    )


def _require_source(ctx: JobContext) -> int:
    if ctx.source_id is None:
        raise ConfigurationError(f"Job {ctx.job_id} has no source attached.")
    return ctx.source_id


def _has_concepts(source_id: int) -> bool:
    from sqlalchemy import func

    from ..db.models import Concept

    with session_scope() as session:
        return bool(
            session.execute(
                select(func.count()).select_from(Concept).where(Concept.source_id == source_id)
            ).scalar_one()
        )


def enqueue_ingest(source_id: int) -> int:
    """Queue a full pipeline run for a source."""
    return jq.enqueue(JobType.INGEST, source_id=source_id, dedupe_key="pipeline")
