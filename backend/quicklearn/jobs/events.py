"""In-process pub/sub for job progress and streaming generation.

Feeds the ``/ws/jobs`` and ``/ws/chat/{id}`` endpoints. PRD §3.3 is specific that the UI must
show real stage names — "Transcribing — 40%", "Generating section 3 of 8" — and never an
indefinite spinner, because a long job with no visible progress is indistinguishable from a
hung one.

Each subscriber gets a bounded queue. If a slow client stops draining, its oldest events are
dropped rather than growing the queue without limit: a stalled browser tab must not become a
memory leak in the server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

EventType = Literal[
    "job.created",
    "job.progress",
    "job.succeeded",
    "job.failed",
    "job.cancelled",
    "source.updated",
    "note.section",
    "chat.token",
    "chat.done",
    "podcast.progress",
]

MAX_QUEUE = 256


@dataclass(slots=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    #: Optional routing key, so a chat subscriber isn't woken by every job tick.
    topic: str = "jobs"

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "topic": self.topic, **self.data}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._lock = asyncio.Lock()
        #: The loop the bus belongs to. Worker threads publish into it from outside.
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self, topic: str = "jobs") -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE)
        async with self._lock:
            self._subscribers.setdefault(topic, set()).add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[Event], topic: str = "jobs") -> None:
        async with self._lock:
            subs = self._subscribers.get(topic)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(topic, None)

    def publish(self, event: Event) -> None:
        """Publish from within the event loop."""
        for queue in list(self._subscribers.get(event.topic, ())):
            if queue.full():
                # Drop the oldest so the newest state still arrives. Progress events are
                # snapshots, not a ledger — losing an intermediate percentage is harmless.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def publish_threadsafe(self, event: Event) -> None:
        """Publish from a worker thread (``asyncio.to_thread`` context)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # pragma: no cover - loop shut down mid-publish
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self.publish, event)

    async def stream(self, topic: str = "jobs") -> AsyncIterator[Event]:
        queue = await self.subscribe(topic)
        try:
            while True:
                yield await queue.get()
        finally:
            await self.unsubscribe(queue, topic)

    def subscriber_count(self, topic: str = "jobs") -> int:
        return len(self._subscribers.get(topic, ()))


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def emit(event_type: EventType, topic: str = "jobs", **data: Any) -> None:
    """Publish, working from either a thread or the event loop.

    Pipeline stages run partly in threads (ffmpeg, PDF parsing) and partly on the loop, and
    making every call site know which it is would be a persistent source of bugs.
    """
    event = Event(type=event_type, data=data, topic=topic)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _bus.publish_threadsafe(event)
    else:
        _bus.publish(event)
