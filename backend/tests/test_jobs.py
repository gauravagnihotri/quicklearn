"""Job queue behaviour.

The concurrency test is the important one here. The whole "SQLite is the queue" design rests on
a single ``UPDATE ... RETURNING`` being atomic enough that two workers cannot claim the same
row, and that is a claim worth verifying against real threads rather than trusting.
"""

from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from quicklearn.config import Config
from quicklearn.db.enums import JobStatus, JobType, SourceKind, SourceOrigin
from quicklearn.db.models import Job, Source
from quicklearn.db.session import session_scope
from quicklearn.jobs import queue as jq


def _make_source(title: str = "Lecture") -> int:
    with session_scope() as session:
        source = Source(
            title=title,
            kind=SourceKind.AUDIO,
            origin=SourceOrigin.UPLOAD,
        )
        session.add(source)
        session.flush()
        return source.id


def test_enqueue_and_claim(config: Config) -> None:
    job_id = jq.enqueue(JobType.TRANSCRIBE, payload={"path": "a.wav"}, source_id=_make_source())

    claimed = jq.claim_next()
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.type is JobType.TRANSCRIBE
    assert claimed.payload == {"path": "a.wav"}
    assert claimed.attempts == 1

    # Queue is now empty — the job is running, not queued.
    assert jq.claim_next() is None


def test_payload_survives_round_trip(config: Config) -> None:
    payload = {"nested": {"list": [1, 2, 3], "flag": True}, "text": "unicode ✓ é"}
    jq.enqueue(JobType.GENERATE_NOTES, payload=payload, source_id=_make_source())
    claimed = jq.claim_next()
    assert claimed is not None
    assert claimed.payload == payload


def test_enqueue_is_idempotent_per_source_and_stage(config: Config) -> None:
    """PRD §11: re-running a stage must not duplicate rows."""
    source_id = _make_source()
    first = jq.enqueue(JobType.SEGMENT, source_id=source_id)
    second = jq.enqueue(JobType.SEGMENT, source_id=source_id)
    assert first == second

    with session_scope() as session:
        count = len(list(session.execute(select(Job).where(Job.source_id == source_id)).scalars()))
    assert count == 1


def test_dedupe_key_separates_logical_runs(config: Config) -> None:
    source_id = _make_source()
    a = jq.enqueue(JobType.GENERATE_NOTES, source_id=source_id, dedupe_key="concise")
    b = jq.enqueue(JobType.GENERATE_NOTES, source_id=source_id, dedupe_key="cornell")
    assert a != b


def test_reenqueue_revives_a_failed_job(config: Config) -> None:
    source_id = _make_source()
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=source_id)
    jq.claim_next()
    jq.mark_failed(job_id, "boom", retryable=False)

    revived = jq.enqueue(JobType.TRANSCRIBE, source_id=source_id)
    assert revived == job_id
    job = jq.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["error"] is None


def test_concurrent_claims_never_double_assign(config: Config) -> None:
    """Twelve jobs, eight threads racing. Every job claimed exactly once, or the queue is
    unsound and two workers would transcribe the same file."""
    source_ids = [_make_source(f"Lecture {i}") for i in range(12)]
    expected = {jq.enqueue(JobType.TRANSCRIBE, source_id=sid) for sid in source_ids}

    def worker() -> list[int]:
        claimed = []
        while True:
            job = jq.claim_next()
            if job is None:
                return claimed
            claimed.append(job.id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result() for f in [pool.submit(worker) for _ in range(8)]]

    all_claimed = [job_id for batch in results for job_id in batch]
    duplicates = [jid for jid, n in collections.Counter(all_claimed).items() if n > 1]

    assert not duplicates, f"jobs claimed more than once: {duplicates}"
    assert set(all_claimed) == expected


def test_progress_is_clamped(config: Config) -> None:
    job_id = jq.enqueue(JobType.NORMALIZE, source_id=_make_source())
    jq.set_progress(job_id, 150, "Transcribing")
    assert (jq.get_job(job_id) or {})["progress"] == 100
    jq.set_progress(job_id, -20)
    assert (jq.get_job(job_id) or {})["progress"] == 0


def test_failure_retries_to_the_ceiling_then_gives_up(config: Config) -> None:
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())

    for attempt in range(1, jq.MAX_ATTEMPTS):
        jq.claim_next()
        assert jq.mark_failed(job_id, f"attempt {attempt}") is True, "should still be retrying"

    jq.claim_next()
    assert jq.mark_failed(job_id, "final") is False, "should be exhausted"
    job = jq.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["error"] == "final"


def test_non_retryable_failure_gives_up_immediately(config: Config) -> None:
    """A bad API key must not burn three attempts — the key will not fix itself."""
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())
    jq.claim_next()
    assert jq.mark_failed(job_id, "Invalid API key for Groq", retryable=False) is False
    assert (jq.get_job(job_id) or {})["status"] == "failed"


def test_recover_orphans_requeues_interrupted_jobs(config: Config) -> None:
    """PRD §3.3 crash recovery: a job left `running` by a kill must come back."""
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())
    jq.claim_next()
    assert (jq.get_job(job_id) or {})["status"] == "running"

    assert jq.recover_orphans() == 1
    job = jq.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["attempts"] == 2
    assert job["started_at"] is None


def test_recover_orphans_fails_jobs_past_the_ceiling(config: Config) -> None:
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.attempts = jq.MAX_ATTEMPTS

    jq.recover_orphans()
    result = jq.get_job(job_id)
    assert result is not None
    assert result["status"] == "failed"
    assert "retry limit" in (result["error"] or "")


def test_cancel_cascades_to_children(config: Config) -> None:
    """Cancelling an ingest must stop the whole tree, not just the parent row."""
    source_id = _make_source()
    parent = jq.enqueue(JobType.INGEST, source_id=source_id)
    child_a = jq.enqueue(JobType.NORMALIZE, source_id=source_id, parent_id=parent)
    child_b = jq.enqueue(JobType.TRANSCRIBE, source_id=source_id, parent_id=parent)

    assert jq.cancel(parent) is True
    for job_id in (parent, child_a, child_b):
        assert (jq.get_job(job_id) or {})["status"] == "cancelled"

    assert jq.is_cancelled(child_a) is True
    # Already terminal — cancelling again is a no-op, not an error.
    assert jq.cancel(parent) is False


def test_cancelled_jobs_are_not_claimable(config: Config) -> None:
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())
    jq.cancel(job_id)
    assert jq.claim_next() is None


def test_retry_resets_a_failed_job(config: Config) -> None:
    job_id = jq.enqueue(JobType.TRANSCRIBE, source_id=_make_source())
    jq.claim_next()
    jq.mark_failed(job_id, "nope", retryable=False)

    assert jq.retry(job_id) is True
    job = jq.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["attempts"] == 0

    # Retrying something already queued is meaningless.
    assert jq.retry(job_id) is False


def test_claim_next_specific_targets_one_job(config: Config) -> None:
    a = jq.enqueue(JobType.NORMALIZE, source_id=_make_source("A"))
    b = jq.enqueue(JobType.NORMALIZE, source_id=_make_source("B"))

    claimed = jq.claim_next_specific(b)
    assert claimed is not None and claimed.id == b
    assert jq.claim_next_specific(b) is None, "already running"

    remaining = jq.claim_next()
    assert remaining is not None and remaining.id == a


def test_queue_depth_and_listing(config: Config) -> None:
    ids = [jq.enqueue(JobType.NORMALIZE, source_id=_make_source(f"S{i}")) for i in range(3)]
    assert jq.queue_depth() == 3

    jq.claim_next()
    assert jq.queue_depth() == 2
    assert len(jq.list_jobs(active_only=True)) == 3  # one running, two queued

    jq.mark_succeeded(ids[0])
    assert len(jq.list_jobs(active_only=True)) == 2
    assert len(jq.list_jobs()) == 3
