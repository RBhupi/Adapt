# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Ingress source contract.

A source is the pipeline's ingress role: it produces a stream of scan file paths
(from the cloud, a local directory, a watched folder, …) and pushes them into the
processor queue. It is NOT a graph transform — it has no upstream context and it
drives the loop rather than being pulled by it.

Every source is built the same way, ``source_cls(run, options, queue, acquire)``:
the run's values (``SourceRun``), its own validated options (``None`` for a
source without options), the queue to the processor (``ScanQueue``) and the
acquisition gateway into the store.

The Protocols are structural: a source need not import or subclass anything to
qualify (which keeps sources free of runtime imports). ``ScanSource`` holds
methods only, so a class can be checked against it with ``issubclass``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceRun:
    """The values of a run that its source is handed."""

    radar: str
    mode: str  # "realtime" | "historical"
    start_time: str | None  # ISO 8601 UTC, as the run config holds it
    end_time: str | None
    source_dir: str | None


class ScanQueue(Protocol):
    """What a source sees of the queue to the processor."""

    def put(self, item: object, block: bool = True, timeout: float | None = None) -> None:
        """Hand one acquired-scan message to the processor."""

    def wait_for_room(self, stopped: Callable[[], bool]) -> None:
        """Hold a source that fetches ahead until the queue has room or ``stopped()``."""


@runtime_checkable
class ScanSource(Protocol):
    """Interface the orchestrator drives. Implementations are usually threads."""

    def __init__(self, run: SourceRun, options: Any, queue: ScanQueue, acquire: Any) -> None:
        """Build the source; a source may take further arguments, keyword-only."""

    @classmethod
    def check(cls, run: SourceRun, options: Any) -> None:
        """Refuse a run this source cannot run, before the run is recorded."""

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
