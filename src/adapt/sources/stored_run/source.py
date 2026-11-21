# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The ``stored_run`` source: the scans a run of the store already has."""

import logging
import threading

logger = logging.getLogger(__name__)

__all__ = ["StoredRunSource"]


class StoredRunSource(threading.Thread):
    """Queue the scans a stored run already has, in time order.

    A finite source that acquires nothing: the raw volumes are in the store.
    The run's modules then run over each scan again, reading from the store
    what the modules not listed this time stored before.
    """

    def __init__(self, config, result_queue=None, acquire=None) -> None:
        super().__init__(daemon=True, name="StoredRunSource")
        if acquire is None:
            raise ValueError("StoredRunSource requires the acquisition gateway")
        self._result_queue = result_queue
        self._acquire = acquire
        self._stop_event = threading.Event()
        self._total = 0
        self._queued = 0
        self._complete = threading.Event()

    @classmethod
    def check(cls, config) -> None:
        """Nothing to check before the run is opened: its scans are in the store."""

    def stop(self) -> None:
        self._stop_event.set()

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def is_historical_complete(self) -> bool:
        return self._complete.is_set()

    def get_historical_progress(self) -> tuple[int, int]:
        return self._queued, self._total

    def run(self) -> None:
        messages = self._acquire.stored_scans()
        self._total = len(messages)
        for message in messages:
            if self.stopped():
                break
            if self._result_queue is not None:
                self._result_queue.put(message)
            self._queued += 1
        self._complete.set()
        logger.info("StoredRunSource queued %d/%d scans", self._queued, self._total)
