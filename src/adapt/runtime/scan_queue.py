# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The queue of acquired scans between the source and the processor.

Backpressure with hysteresis: a source that fetches ahead waits at the high
level and resumes once the queue has drained to the low level. The processor is
never paused — only the source waits.
"""

import logging
import queue
import time
from collections.abc import Callable

__all__ = ["BackpressureQueue"]

logger = logging.getLogger(__name__)


class BackpressureQueue(queue.Queue[object]):
    """The queue of acquired scans between the source and the processor."""

    def __init__(
        self,
        max_queue_size: int,
        resume_fraction: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(maxsize=max_queue_size)
        self._high = max_queue_size
        self._low = max(1, int(max_queue_size * resume_fraction))
        self._sleep = sleep
        self._pauses = 0
        self._paused_seconds = 0.0

    def wait_for_room(self, stopped: Callable[[], bool], poll_seconds: float = 0.5) -> None:
        """Block the calling source while the queue is at its high level.

        Returns as soon as the queue has drained to the low level, or ``stopped()``
        is true. The two levels are deliberately apart so a full queue does not
        turn into one fetch per consumed scan.
        """
        if self.qsize() < self._high:
            return
        self._pauses += 1
        t0 = time.monotonic()
        logger.info(
            "Queue at %d/%d — pausing downloads until it drains to %d",
            self.qsize(),
            self._high,
            self._low,
        )
        while not stopped() and self.qsize() > self._low:
            self._sleep(poll_seconds)
        waited = time.monotonic() - t0
        self._paused_seconds += waited
        if not stopped():
            logger.info("Queue at %d — resuming downloads after %.0f s", self.qsize(), waited)

    def backpressure_stats(self) -> dict[str, float]:
        """How often, and for how long, the source waited on the processor."""
        return {
            "queue_high": self._high,
            "queue_low": self._low,
            "pauses": self._pauses,
            "paused_seconds": round(self._paused_seconds, 1),
        }
