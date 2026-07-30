"""Study-engine job handlers.

Only long-running study work belongs here. Reviewing a card and answering a quiz item happen
synchronously in the request — they are single-digit-millisecond database writes, and routing
them through a queue would add latency to the one interaction that must feel instant.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..db.models import Review
from ..db.session import session_scope
from ..settings import get_settings
from ..study import fsrs as fsrs_module

log = logging.getLogger(__name__)

# Quiz generation is registered in handlers/ingest.py alongside the other generation stages —
# registering it twice would just have one silently shadow the other.


async def optimize_fsrs_parameters() -> list[float]:
    """Fit FSRS parameters to the user's own review history and store them.

    CPU-bound and potentially slow, so it runs in a thread. Called directly by the API rather
    than queued, because the user is watching a button and expects a result or an error.
    """
    with session_scope() as session:
        reviews = list(session.execute(select(Review).order_by(Review.reviewed_at)).scalars())
        session.expunge_all()

    parameters = await asyncio.to_thread(fsrs_module.optimize_parameters, reviews)
    get_settings().set("fsrs.parameters", parameters)
    log.info("stored optimised FSRS parameters from %d reviews", len(reviews))
    return parameters
