"""Podcast generation job handler."""

from __future__ import annotations

import logging

from ..db.enums import JobType, PodcastStatus
from ..db.models import Podcast
from ..db.session import session_scope
from ..errors import QuicklearnError
from ..jobs.worker import JobContext, register
from ..pipeline.generate import podcast as podcast_gen

log = logging.getLogger(__name__)


@register(JobType.GENERATE_PODCAST)
async def handle_generate_podcast(ctx: JobContext) -> None:
    podcast_id = ctx.payload.get("podcast_id")
    if not isinstance(podcast_id, int):
        raise ValueError("Podcast job is missing podcast_id")

    ctx.progress(2, "Preparing")
    try:
        await podcast_gen.generate_podcast(
            podcast_id,
            job_id=ctx.job_id,
            on_progress=lambda done, total: ctx.sub_progress(
                20, 65, done, total, f"Recording line {done} of {total}"
            ),
        )
    except QuicklearnError as exc:
        _fail(podcast_id, exc.message)
        raise
    except Exception as exc:
        _fail(podcast_id, str(exc))
        raise

    ctx.progress(100, "Ready")


def _fail(podcast_id: int, message: str) -> None:
    with session_scope() as session:
        podcast = session.get(Podcast, podcast_id)
        if podcast is not None:
            podcast.status = PodcastStatus.FAILED
            podcast.error = message[:2000]
