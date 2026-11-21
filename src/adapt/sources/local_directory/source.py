# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The ``local_directory`` source: the scans of a folder, acquired in time order."""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from adapt.downloaders.nexrad import parse_scan_time
from adapt.utils.time import to_scan_iso

logger = logging.getLogger(__name__)

__all__ = ["LocalDirectorySource", "local_directory_files"]


def local_directory_files(config) -> list[Path]:
    """The scans of ``config.source_dir`` the run processes, in time order.

    Hidden files (``.DS_Store`` and the like) are ignored. Every other file must
    be a Level-II volume of ``config.downloader.radar`` named
    ``RADARYYYYMMDD_HHMMSS...``; only those whose scan time lies within
    ``run.start_time .. run.end_time`` (inclusive) are processed.

    Raises
    ------
    ValueError
        The folder is missing, holds a file not named for the radar with a
        scan time, or holds no scan within the window; the message says which.
    """
    folder = Path(config.source_dir)
    if not folder.is_dir():
        raise ValueError(f"run.source_dir {folder} is not a folder")
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
        raise ValueError(
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
        raise ValueError(
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
        raise ValueError(
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

    source_name: ClassVar[str] = "local_directory"
    summary: ClassVar[str] = "The Level-II files of run.source_dir, in time order"

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
