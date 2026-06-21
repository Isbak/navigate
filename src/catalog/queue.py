"""The artifact queue that decouples discovery from persistence.

The scanner (producer) discovers files and pushes :class:`~catalog.events.Artifact`
records onto the queue. A database writer (consumer) drains the queue, upserts
each record, and publishes a scan event. Keeping a real queue between the two
stages makes the ``scanner -> artifact queue -> database`` pipeline explicit and
lets the consumer run on its own thread with its own SQLite connection.
"""

from __future__ import annotations

import queue
from collections.abc import Iterator

from .events import Artifact

# Sentinel placed on the queue to signal the consumer that production is done.
_SENTINEL = object()


class ArtifactQueue:
    """A thin, typed wrapper around :class:`queue.Queue`."""

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)

    def put(self, artifact: Artifact) -> None:
        self._queue.put(artifact)

    def close(self) -> None:
        """Signal that no more artifacts will be produced."""

        self._queue.put(_SENTINEL)

    def drain(self) -> Iterator[Artifact]:
        """Yield artifacts until the queue is closed.

        Blocks waiting for items, so it is safe to run on a consumer thread
        while a producer is still pushing work.
        """

        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                yield item
            finally:
                self._queue.task_done()
