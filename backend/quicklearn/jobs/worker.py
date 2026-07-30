"""In-process asyncio worker pool.

N tasks poll the ``jobs`` table and run handlers (PRD §3.2/§3.3). No Redis, no separate
process — the whole point of the single-container constraint is that there is nothing else to
deploy or supervise.

Handlers are registered by job type through :func:`register`. CPU-bound work inside a handler
is the handler's own responsibility to push onto a thread via ``asyncio.to_thread``, which is
why the poll loop itself must never block.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..config import Config, get_config
from ..db.enums import JobType
from ..errors import CancelledError, QuicklearnError
from . import queue as jq
from .events import emit, get_bus

log = logging.getLogger(__name__)

IDLE_POLL_SEC = 0.35
#: Backoff before a re-queued job becomes eligible again, so a rate-limited provider isn't
#: hammered three times in a second.
RETRY_BACKOFF_SEC = (1.0, 4.0, 12.0)


@dataclass(slots=True)
class JobContext:
    """Handed to every handler. The only channel a stage needs for progress and cancellation."""

    job_id: int
    job_type: JobType
    payload: dict[str, Any]
    source_id: int | None
    attempts: int
    #: Last reported percentage, so `stage()` can change the label without resetting the bar.
    _percent: int = 0

    def progress(self, percent: int, stage: str = "") -> None:
        self._percent = max(0, min(100, int(percent)))
        jq.set_progress(self.job_id, self._percent, stage)

    def stage(self, stage: str) -> None:
        """Update the stage label, holding the current percentage."""
        jq.set_progress(self.job_id, self._percent, stage)

    def check_cancelled(self) -> None:
        """Raise if the user cancelled. Call between units of work in long stages."""
        if jq.is_cancelled(self.job_id):
            raise CancelledError("Job cancelled by user")

    def sub_progress(self, base: int, span: int, done: int, total: int, stage: str = "") -> None:
        """Report progress within a sub-range of the overall job.

        Lets a stage that owns 40–70% of a job report its own 0–100% without knowing where it
        sits in the pipeline.
        """
        if total <= 0:
            return
        self.progress(base + int(span * (done / total)), stage)


Handler = Callable[[JobContext], Awaitable[None]]
_handlers: dict[JobType, Handler] = {}


def register(job_type: JobType) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        return fn

    return decorator


def get_handler(job_type: JobType) -> Handler | None:
    return _handlers.get(job_type)


def registered_types() -> list[JobType]:
    return sorted(_handlers, key=str)


class WorkerPool:
    def __init__(self, config: Config | None = None) -> None:
        self._config = config or get_config()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._active: dict[int, int] = {}

    @property
    def active_job_ids(self) -> list[int]:
        return list(self._active.values())

    async def start(self) -> None:
        # Import for side effects: handler modules register themselves on import. Done here
        # rather than at module scope to keep the import graph acyclic.
        from .. import handlers  # noqa: F401

        get_bus().bind_loop(asyncio.get_running_loop())

        recovered = await asyncio.to_thread(jq.recover_orphans)
        if recovered:
            log.info("requeued %d job(s) interrupted by a previous shutdown", recovered)

        count = max(1, self._config.worker_concurrency)
        self._stopping.clear()
        for index in range(count):
            self._tasks.append(asyncio.create_task(self._loop(index), name=f"worker-{index}"))
        log.info(
            "worker pool started with %d task(s); handlers: %s",
            count,
            ", ".join(str(t) for t in registered_types()),
        )

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, index: int) -> None:
        while not self._stopping.is_set():
            try:
                job = await asyncio.to_thread(jq.claim_next)
            except Exception:
                log.exception("worker %s failed to claim a job", index)
                await asyncio.sleep(1.0)
                continue

            if job is None:
                # Jitter so N workers don't wake in lockstep and contend on the write lock.
                await asyncio.sleep(IDLE_POLL_SEC + random.random() * 0.15)
                continue

            self._active[index] = job.id
            try:
                await self._run(job)
            finally:
                self._active.pop(index, None)

    async def _run(self, job: jq.ClaimedJob) -> None:
        handler = get_handler(job.type)
        if handler is None:
            await asyncio.to_thread(
                jq.mark_failed,
                job.id,
                f"No handler registered for job type '{job.type}'. This is a bug.",
                retryable=False,
            )
            return

        ctx = JobContext(
            job_id=job.id,
            job_type=job.type,
            payload=job.payload,
            source_id=job.source_id,
            attempts=job.attempts,
        )

        log.info("job %s (%s) starting, attempt %s", job.id, job.type, job.attempts)
        try:
            await handler(ctx)
        except CancelledError:
            log.info("job %s cancelled", job.id)
            return
        except asyncio.CancelledError:
            # Server is shutting down. Leave the row in `running` so boot-time recovery
            # requeues it rather than marking a job failed for our own restart.
            log.info("job %s interrupted by shutdown", job.id)
            raise
        except QuicklearnError as exc:
            requeued = await asyncio.to_thread(
                jq.mark_failed, job.id, exc.message, retryable=exc.retryable
            )
            if requeued:
                await asyncio.sleep(
                    RETRY_BACKOFF_SEC[min(job.attempts - 1, len(RETRY_BACKOFF_SEC) - 1)]
                )
            return
        except Exception as exc:  # unexpected — include the type, it's a bug report
            log.exception("job %s crashed", job.id)
            await asyncio.to_thread(
                jq.mark_failed, job.id, f"{type(exc).__name__}: {exc}", retryable=True
            )
            return

        if await asyncio.to_thread(jq.is_cancelled, job.id):
            return
        await asyncio.to_thread(jq.mark_succeeded, job.id)
        log.info("job %s (%s) finished", job.id, job.type)


_pool: WorkerPool | None = None


def get_pool() -> WorkerPool:
    global _pool
    if _pool is None:
        _pool = WorkerPool()
    return _pool


async def run_job_inline(job_id: int) -> None:
    """Execute a queued job immediately on the current loop, bypassing the pool.

    Used by the test suite so pipeline behaviour can be asserted deterministically instead of
    by polling for a background worker to get around to it.
    """
    job = await asyncio.to_thread(jq.claim_next_specific, job_id)
    if job is None:
        return
    pool = WorkerPool()
    await pool._run(job)


def reset_pool() -> None:
    global _pool
    _pool = None


__all__ = [
    "JobContext",
    "WorkerPool",
    "emit",
    "get_handler",
    "get_pool",
    "register",
    "registered_types",
    "reset_pool",
    "run_job_inline",
]
