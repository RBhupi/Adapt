# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Ingress source plugins and registry.

A source is a swappable, registered-by-name plugin that acquires raw scans into
the store (via the injected acquisition gateway) and feeds the resulting queue
messages to the processor. The orchestrator resolves the source named by
``config.source`` and constructs it uniformly with
(config, result_queue, acquire).

Built-in sources:
- ``aws_nexrad``      — AwsNexradDownloader (download from S3, realtime/historical)
- ``local_directory`` — LocalDirectorySource (acquire already-present files in order)
- ``stored_run``      — StoredRunSource (the scans a run of the store already has)

Adding a source: implement the ScanSource interface (see contracts/source.py) and
call ``source_registry.register(name, cls)``.
"""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from adapt.configuration.schemas.errors import ConfigError
from adapt.contracts.source import ScanSource
from adapt.downloaders.nexrad import parse_scan_time
from adapt.modules.acquisition.module import AwsNexradDownloader
from adapt.utils.time import to_scan_iso

logger = logging.getLogger(__name__)

__all__ = [
    "LocalDirectorySource",
    "SourceRegistry",
    "StoredRunSource",
    "local_directory_files",
    "source_registry",
]


class SourceRegistry:
    """Name → source class. Resolved by the orchestrator from ``config.source``."""

    def __init__(self) -> None:
        self._sources: dict[str, type[ScanSource]] = {}

    def register(self, name: str, source_class: type[ScanSource]) -> None:
        if name in self._sources:
            raise RuntimeError(f"Source '{name}' is already registered.")
        self._sources[name] = source_class

    def get(self, name: str) -> type[ScanSource]:
        if name not in self._sources:
            raise KeyError(f"Source '{name}' is not registered.")
        return self._sources[name]

    def names(self) -> list[str]:
        return list(self._sources)


def local_directory_files(config) -> list[Path]:
    """The scans of ``config.source_dir`` the run processes, in time order.

    Hidden files (``.DS_Store`` and the like) are ignored. Every other file must
    be a Level-II volume of ``config.downloader.radar`` named
    ``RADARYYYYMMDD_HHMMSS...``; only those whose scan time lies within
    ``run.start_time .. run.end_time`` (inclusive) are processed.

    Raises
    ------
    ConfigError
        The folder is missing, holds a file not named for the radar with a
        scan time, or holds no scan within the window; the message says which.
    """
    folder = Path(config.source_dir)
    if not folder.is_dir():
        raise ConfigError(f"run.source_dir {folder} is not a folder")
    window = tuple(_window_edge(config.downloader, key) for key in ("start_time", "end_time"))
    radar = config.downloader.radar.upper()
    wrong: list[str] = []
    timed: list[tuple[datetime, Path]] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
        try:
            if not path.name.upper().startswith(radar):
                raise ValueError(path.name)
            timed.append((parse_scan_time(path.name), path))
        except ValueError:
            wrong.append(path.name)
    if wrong:
        raise ConfigError(
            f"run.source_dir {folder} holds {len(wrong)} file(s) that are not Level-II "
            f"volumes of radar {radar} named RADARYYYYMMDD_HHMMSS...: {', '.join(wrong[:10])}"
            f"{', ...' if len(wrong) > 10 else ''} — move them out (hidden files are ignored)"
        )
    inside = [path for when, path in timed if window[0] <= when <= window[1]]
    if not inside:
        span = (
            f"its {len(timed)} scans span {to_scan_iso(timed[0][0])} .. {to_scan_iso(timed[-1][0])}"
            if timed
            else "the folder holds no scans"
        )
        raise ConfigError(
            f"no scan in run.source_dir {folder} lies within run.start_time "
            f"{config.downloader.start_time} .. run.end_time {config.downloader.end_time}; {span}"
        )
    if len(inside) < len(timed):
        logger.info(
            "%d of %d scans in %s are outside the run window and are skipped",
            len(timed) - len(inside),
            len(timed),
            folder,
        )
    return inside


def _window_edge(downloader, key: str) -> datetime:
    value = getattr(downloader, key)
    if value is None:
        raise ConfigError(
            f"run.{key} is required for source: local_directory — the window of scans to process"
        )
    when = datetime.fromisoformat(value)
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


class LocalDirectorySource(threading.Thread):
    """Acquire the scans of ``config.source_dir`` within the run window, in time order.

    A finite source: once every file is acquired into the store and queued it
    reports complete. Use when the files were downloaded by an external process
    and only need processing. Building it checks the folder (``check``), so a
    folder the run cannot process is refused before the run is recorded.
    """

    def __init__(self, config, result_queue=None, acquire=None) -> None:
        super().__init__(daemon=True, name="LocalDirectorySource")
        if not config.source_dir:
            raise ValueError(
                "source_dir is required for the 'local_directory' source. "
                "Set source_dir in your config to the directory of files to process."
            )
        if acquire is None:
            raise ValueError("LocalDirectorySource requires the acquisition gateway")
        self._files = local_directory_files(config)
        self._result_queue = result_queue
        self._acquire = acquire
        self._stop_event = threading.Event()
        self._min_file_size = config.downloader.min_file_size
        self._total = 0
        self._queued = 0
        self._complete = threading.Event()

    @classmethod
    def check(cls, config) -> None:
        """Refuse a folder the run cannot process (see ``local_directory_files``)."""
        local_directory_files(config)

    def stop(self) -> None:
        self._stop_event.set()

    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def is_historical_complete(self) -> bool:
        return self._complete.is_set()

    def get_historical_progress(self) -> tuple[int, int]:
        return self._queued, self._total

    def run(self) -> None:
        files = []
        for path in self._files:
            size = path.stat().st_size
            if size < self._min_file_size:
                logger.warning(
                    "Skipping %s: %d bytes is under downloader.min_file_size %d",
                    path.name,
                    size,
                    self._min_file_size,
                )
                continue
            files.append(path)
        self._total = len(files)
        for path in files:
            if self.stopped():
                break
            if self._result_queue is not None:
                # Loud failure on an unparseable stamp: the store cannot register
                # a scan without its observation time (no wall-clock substitute).
                scan_time = parse_scan_time(path.name)
                message = self._acquire.acquire_file(
                    path, source_uri=str(path), scan_time=scan_time
                )
                self._result_queue.put(message)
            self._queued += 1
        self._complete.set()
        logger.info("LocalDirectorySource queued %d/%d files", self._queued, self._total)


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


source_registry = SourceRegistry()
source_registry.register("aws_nexrad", AwsNexradDownloader)
source_registry.register("local_directory", LocalDirectorySource)
source_registry.register("stored_run", StoredRunSource)
