"""SQLite-backed job queue.

Persistence and simplicity over throughput (PRD §3.3). A single ``UPDATE ... RETURNING``
statement claims a job; because SQLite executes each statement atomically and serialises
writers, two workers racing on the same row cannot both win — the loser's subquery re-runs
against post-commit state and selects a different row. That removes any need for explicit
``BEGIN IMMEDIATE`` juggling.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text, update

from ..db.enums import JobStatus, JobType
from ..db.models import Job
from ..db.session import session_scope
from ..db.types import utcnow
from .events import emit

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


@dataclass(slots=True)
class ClaimedJob:
    """Detached snapshot of a claimed job.

    A plain dataclass rather than an ORM instance because the handler runs for minutes across
    thread boundaries, and a live ``Job`` would either hold a session open or raise
    ``DetachedInstanceError`` on first attribute access.
    """

    id: int
    type: JobType
    payload: dict[str, Any]
    source_id: int | None
    parent_id: int | None
    attempts: int


def enqueue(
    job_type: JobType,
    *,
    payload: dict[str, Any] | None = None,
    source_id: int | None = None,
    parent_id: int | None = None,
    dedupe_key: str = "",
) -> int:
    """Add a job, or revive the matching one.

    Idempotency is enforced by the ``(source_id, type, dedupe_key)`` unique constraint, which
    implements PRD §11's "re-running a pipeline stage must not duplicate rows" in the schema
    rather than by convention. An existing job in a terminal state is reset to ``queued`` so
    that a retry is a no-op-safe operation.
    """
    payload = payload or {}
    with session_scope() as session:
        existing: Job | None = None
        if source_id is not None:
            existing = session.execute(
                select(Job).where(
                    Job.source_id == source_id,
                    Job.type == job_type,
                    Job.dedupe_key == dedupe_key,
                )
            ).scalar_one_or_none()

        if existing is not None:
            if existing.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                return existing.id
            existing.status = JobStatus.QUEUED
            existing.progress = 0
            existing.stage = ""
            existing.error = None
            existing.attempts = 0
            existing.payload = payload or existing.payload
            existing.parent_id = parent_id if parent_id is not None else existing.parent_id
            existing.started_at = None
            existing.finished_at = None
            session.flush()
            job_id = existing.id
        else:
            job = Job(
                type=job_type,
                payload=payload,
                source_id=source_id,
                parent_id=parent_id,
                dedupe_key=dedupe_key,
                status=JobStatus.QUEUED,
            )
            session.add(job)
            session.flush()
            job_id = job.id

    emit(
        "job.created",
        job_id=job_id,
        type=str(job_type),
        source_id=source_id,
        parent_id=parent_id,
    )
    return job_id


def claim_next() -> ClaimedJob | None:
    """Atomically move one queued job to ``running`` and return it.

    ``ORDER BY id`` gives FIFO. Children are ordered after their parent by construction, since
    a parent is always inserted first.
    """
    with session_scope() as session:
        row = session.execute(
            text(
                """
                UPDATE jobs
                   SET status = 'running',
                       started_at = :now,
                       attempts = attempts + 1
                 WHERE id = (
                     SELECT id FROM jobs
                      WHERE status = 'queued'
                      ORDER BY id
                      LIMIT 1
                 )
             RETURNING id, type, payload, source_id, parent_id, attempts
                """
            ),
            {"now": utcnow()},
        ).fetchone()

        if row is None:
            return None

        raw_payload = row[2]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
        return ClaimedJob(
            id=int(row[0]),
            type=JobType(row[1]),
            payload=payload,
            source_id=row[3],
            parent_id=row[4],
            attempts=int(row[5]),
        )


def claim_next_specific(job_id: int) -> ClaimedJob | None:
    """Claim one named job, if it is still queued.

    Used by the test suite to run a specific job deterministically rather than racing a
    background worker for it.
    """
    with session_scope() as session:
        row = session.execute(
            text(
                """
                UPDATE jobs
                   SET status = 'running',
                       started_at = :now,
                       attempts = attempts + 1
                 WHERE id = :job_id AND status = 'queued'
             RETURNING id, type, payload, source_id, parent_id, attempts
                """
            ),
            {"now": utcnow(), "job_id": job_id},
        ).fetchone()

        if row is None:
            return None
        raw_payload = row[2]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
        return ClaimedJob(
            id=int(row[0]),
            type=JobType(row[1]),
            payload=payload,
            source_id=row[3],
            parent_id=row[4],
            attempts=int(row[5]),
        )


def set_progress(job_id: int, progress: int, stage: str = "") -> None:
    clamped = max(0, min(100, int(progress)))
    with session_scope() as session:
        values: dict[str, Any] = {"progress": clamped}
        if stage:
            values["stage"] = stage
        session.execute(update(Job).where(Job.id == job_id).values(**values))
    emit("job.progress", job_id=job_id, progress=clamped, stage=stage)


def mark_succeeded(job_id: int, stage: str = "Done") -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.SUCCEEDED
        job.progress = 100
        job.stage = stage
        job.error = None
        job.finished_at = utcnow()
        source_id = job.source_id
    emit("job.succeeded", job_id=job_id, source_id=source_id, stage=stage)


def mark_failed(job_id: int, error: str, *, retryable: bool = True) -> bool:
    """Record a failure. Returns ``True`` if the job was re-queued for another attempt."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return False
        will_retry = retryable and job.attempts < MAX_ATTEMPTS
        job.status = JobStatus.QUEUED if will_retry else JobStatus.FAILED
        job.error = error[:4000]
        if not will_retry:
            job.finished_at = utcnow()
        source_id = job.source_id
        attempts = job.attempts

    if will_retry:
        log.warning(
            "job %s failed (attempt %s/%s), requeued: %s", job_id, attempts, MAX_ATTEMPTS, error
        )
        emit(
            "job.progress", job_id=job_id, progress=0, stage=f"Retrying ({attempts}/{MAX_ATTEMPTS})"
        )
    else:
        log.error("job %s failed permanently: %s", job_id, error)
        emit("job.failed", job_id=job_id, source_id=source_id, error=error[:2000])
    return will_retry


def cancel(job_id: int) -> bool:
    """Cancel a queued job, or flag a running one for cooperative cancellation."""
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return False
        job.status = JobStatus.CANCELLED
        job.finished_at = utcnow()
        job.stage = "Cancelled"
        # Cascade to children so cancelling an ingest stops the whole tree.
        session.execute(
            update(Job)
            .where(Job.parent_id == job_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
            .values(status=JobStatus.CANCELLED, finished_at=utcnow(), stage="Cancelled")
        )
    emit("job.cancelled", job_id=job_id)
    return True


def is_cancelled(job_id: int) -> bool:
    """Cooperative cancellation check, polled by long-running stages."""
    with session_scope() as session:
        status = session.execute(select(Job.status).where(Job.id == job_id)).scalar_one_or_none()
    return status == JobStatus.CANCELLED


def retry(job_id: int) -> bool:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None or job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            return False
        job.status = JobStatus.QUEUED
        job.attempts = 0
        job.progress = 0
        job.stage = ""
        job.error = None
        job.started_at = None
        job.finished_at = None
    emit("job.progress", job_id=job_id, progress=0, stage="Queued")
    return True


def recover_orphans() -> int:
    """Reset jobs stranded in ``running`` by a crash.

    PRD §3.3: on boot, anything left ``running`` returns to ``queued`` with ``attempts += 1``,
    and anything past the attempt ceiling becomes ``failed``. Without this a hard kill during a
    90-minute transcription would leave the job invisible and unrecoverable.
    """
    with session_scope() as session:
        stranded = list(
            session.execute(select(Job).where(Job.status == JobStatus.RUNNING)).scalars()
        )
        requeued = 0
        for job in stranded:
            job.attempts += 1
            if job.attempts > MAX_ATTEMPTS:
                job.status = JobStatus.FAILED
                job.error = (
                    "Exceeded the retry limit after the server restarted mid-job. "
                    "Use Retry to try again."
                )
                job.finished_at = utcnow()
            else:
                job.status = JobStatus.QUEUED
                job.progress = 0
                job.stage = "Requeued after restart"
                job.started_at = None
                requeued += 1
    if stranded:
        log.info("recovered %d interrupted job(s), %d requeued", len(stranded), requeued)
    return requeued


def queue_depth() -> int:
    with session_scope() as session:
        return int(
            session.execute(
                select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
            ).scalar_one()
        )


def list_jobs(*, limit: int = 50, active_only: bool = False) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Job).order_by(Job.id.desc()).limit(limit)
        if active_only:
            stmt = (
                select(Job)
                .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .order_by(Job.id.desc())
                .limit(limit)
            )
        return [_job_dict(job) for job in session.execute(stmt).scalars()]


def get_job(job_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        return _job_dict(job) if job else None


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "parent_id": job.parent_id,
        "source_id": job.source_id,
        "type": str(job.type),
        "status": str(job.status),
        "progress": job.progress,
        "stage": job.stage,
        "error": job.error,
        "attempts": job.attempts,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }
