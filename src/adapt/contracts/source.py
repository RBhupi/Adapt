# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Ingress source contract.

A source is the pipeline's ingress role: it produces a stream of scan file paths
(from the cloud, a local directory, a watched folder, …) and pushes them into the
processor queue. It is NOT a graph transform — it has no upstream context and it
drives the loop rather than being pulled by it.

This Protocol is structural: a source need not import or subclass anything to
qualify (which keeps acquisition modules free of runtime imports). It documents
the methods the orchestrator depends on.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScanSource(Protocol):
    """Interface the orchestrator drives. Implementations are usually threads."""

    @classmethod
    def check(cls, config) -> None:
        """Refuse a ``config`` this source cannot run, before the run is recorded."""

    def start(self) -> None:
        """Begin producing file paths into the result queue (non-blocking)."""

    def stop(self) -> None:
        """Signal the source to stop after the current item."""

    def is_alive(self) -> bool:
        """True while the source thread is running."""

    def join(self, timeout: float | None = None) -> None:
        """Wait for the source thread to finish."""

    def is_historical_complete(self) -> bool:
        """True once a finite source has queued all its items (always False for realtime)."""

    def get_historical_progress(self) -> tuple[int, int]:
        """(queued, total) for a finite source."""
