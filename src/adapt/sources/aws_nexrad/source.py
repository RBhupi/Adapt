# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""AWS S3 NEXRAD Level-II file discovery and download.

Realtime: checks the radar name once, takes the newest scans of the recent
window, then lists only the keys after the last one seen, on a schedule set by
the age of the last volume published (``schedule.next_check_at``). While the
processor is behind only the newest volume is taken; the others are skipped
and logged, and too many skips stop the run. Historical: lists and queues the
window one UTC day at a time. Deduplicates via the catalog (by source URI).
"""

import contextlib
import io
import logging
import queue
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import ClassVar

from adapt.contracts.source import ScanQueue, SourceRun
from adapt.downloaders import ArchiveScan, NexradS3
from adapt.sources.aws_nexrad.config import AwsNexradOptions
from adapt.sources.aws_nexrad.schedule import next_check_at

__all__ = ["AwsNexradDownloader", "Seams"]

logger = logging.getLogger(__name__)

_TICK_SECONDS = 1  # realtime loop: how soon a stop, an empty queue or a check time is noticed


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Seams:
    """How the downloader reaches the archive and the clock. Tests run it offline
    by subclassing with fakes: ``seams = Seams(connect=lambda: fake_conn)``."""

    connect: Callable[[], NexradS3] = NexradS3  # the archive connection, made once
    clock: Callable[[], datetime] = _utc_now  # the current UTC time
    sleep: Callable[[float], None] = time.sleep


class AwsNexradDownloader(threading.Thread):
    """Downloads NEXRAD Level-II files from AWS S3 in realtime or historical mode.

    **Realtime Mode:** Refuses a radar with no data today or yesterday, takes the
    newest ``latest_files`` of the last ``latest_minutes``, then looks for each
    new volume on the arrival schedule. Newest wins while processing is behind;
    ``max_skipped_scans`` skips stop the run (a restart begins new tracks).

    **Historical Mode:** Lists and queues the window (start_time to end_time)
    one UTC day at a time, then exits. A window without volumes fails.

    **AWS S3 Bucket:** Files stored at
    `s3://unidata-nexrad-level2/{YYYY}/{MM}/{DD}/{radar}/`
    Example: `s3://unidata-nexrad-level2/2025/03/05/KDIX/KDIX20250305_000310_V06`

    **Deduplication:** Maintains set of known files to avoid re-downloading.
    Safe to restart mid-execution.

    **Queue Communication:** Puts one acquired-scan message per new file on
    the queue — ``{artifact_id, scan_id, scan_time, queued_at}`` — built by
    the injected acquisition gateway, which commits the raw bytes into the
    store at the source boundary. Downstream processor can begin work
    immediately (streaming architecture).

    **File Size Filtering:** Ignores files under ``options.min_file_size``
    (corrupted downloads or metadata-only files).

    **Thread Safety:** Safe to call status methods while run() is executing.
    Uses locks for shared state (_known_files, historical tracking).

    Example usage (typically called by orchestrator)::

        downloader = AwsNexradDownloader(run, options, processor_queue, gateway)
        downloader.start()
        ...
        downloader.stop()
        downloader.join(timeout=10)
    """

    source_name: ClassVar[str] = "aws_nexrad"
    summary: ClassVar[str] = "NEXRAD Level-II scans downloaded from the AWS archive"
    options_class: ClassVar[type[AwsNexradOptions]] = AwsNexradOptions
    seams: ClassVar[Seams] = Seams()

    def __init__(self, run: SourceRun, options: AwsNexradOptions, queue: ScanQueue, acquire):
        """Initialize downloader.

        Parameters
        ----------
        run : SourceRun
            The run's radar, mode and window.

        options : AwsNexradOptions
            This source's validated options.

        queue : ScanQueue
            Queue for acquired-scan messages. Processor reads from this queue;
            it holds the downloader while it is full (``wait_for_room``).

        acquire : acquisition gateway
            Injected by the runtime; commits raw scans into the store and
            registers them for the run. The source never touches persistence
            itself.

        The archive connection and the clock come from the class's ``seams``.

        Notes
        -----
        The S3 bucket is public; downloads use anonymous (unsigned) requests
        and require no AWS credentials.
        """

        super().__init__(daemon=True)

        self.radar = run.radar
        self.mode = run.mode
        self.start_time = run.start_time
        self.end_time = run.end_time
        self._options = options
        self.max_fetch_retries = options.max_fetch_retries
        self.latest_files = options.latest_files
        self.latest_minutes = options.latest_minutes
        self.max_skipped_scans = options.max_skipped_scans

        self.result_queue = queue
        self._acquire = acquire
        self.conn = self.seams.connect()
        self._clock = self.seams.clock
        self._sleep = self.seams.sleep

        self._stop_event = threading.Event()
        # Sources already handed to the processor this run (by source URI).
        self._known_files: set[str] = set()
        self._known_files_lock = threading.Lock()
        self._min_file_size = options.min_file_size
        # Store-external scratch space for in-flight S3 downloads; every file is
        # moved into the store (or deleted) immediately after download.
        self._staging_dir = Path(tempfile.mkdtemp(prefix="adapt_download_"))

        # Realtime discovery: the last key listed, its publication time, the newest
        # volume waiting for the processor to be free, and how many were passed over.
        self._last_key: str | None = None
        self._last_published: datetime | None = None
        self._candidate: ArchiveScan | None = None
        self._skipped = 0

        # Historical mode tracking
        self._historical_complete = threading.Event()
        self._expected_scans = 0
        self._processed_scans = 0

        self.name = f"Downloader-{self.radar}"

    # ========================================================================
    # Thread control
    # ========================================================================

    @classmethod
    def check(cls, run: SourceRun, options: AwsNexradOptions) -> None:
        """Nothing to refuse offline: the archive is reached only when the run starts."""

    def stop(self):
        """Signal the downloader thread to stop gracefully.

        Calling this method sets the internal stop event, which causes the
        `run()` main loop to exit. The thread will finish its current download
        task before stopping (not immediate).

        Safe to call from any thread. Can be called multiple times.

        Examples
        --------
        >>> downloader = AwsNexradDownloader(run, options, queue, gateway)
        >>> downloader.start()
        >>> time.sleep(60)
        >>> downloader.stop()  # Signal thread to exit
        >>> downloader.join()  # Wait for thread termination
        """
        self._stop_event.set()

    def stopped(self) -> bool:
        """Check if a stop request has been issued.

        Returns
        -------
        bool
            True if `stop()` has been called, False otherwise.

        Notes
        -----
        This is non-blocking and reflects whether the stop event has been set.
        The thread may still be running even if this returns True (it finishes
        its current task before exiting).

        Examples
        --------
        >>> if downloader.stopped():
        ...     print("Stop was requested")
        """
        return self._stop_event.is_set()

    def is_historical_complete(self) -> bool:
        """Check if historical download has finished.

        In historical mode, this indicates whether all scans in the time range
        have been downloaded and queued. In realtime mode, this always returns False.

        Returns
        -------
        bool
            True if all available scans in the start_time to end_time range
            have been processed.

        Notes
        -----
        "Complete" means scans have been QUEUED, not necessarily processed
        by the pipeline. The main loop will exit automatically when this is True.

        Examples
        --------
        >>> while not downloader.is_historical_complete():
        ...     downloader.run()  # Wait for completion
        """
        return self._historical_complete.is_set()

    def get_historical_progress(self) -> tuple:
        """Get progress of historical download as (processed, expected) counts.

        Returns
        -------
        tuple of (int, int)
            First element: number of scans successfully processed/queued.
            Second element: total number of scans expected in time range.

        Notes
        -----
        Only meaningful in historical mode. In realtime mode, expected is always 0.
        Progress is (processed, expected) where processed <= expected.
        This can be used to display download progress to the user.

        Examples
        --------
        >>> processed, expected = downloader.get_historical_progress()
        >>> print(f"Downloaded {processed}/{expected} scans")

        >>> while not downloader.is_historical_complete():
        ...     processed, expected = downloader.get_historical_progress()
        ...     print(f"Progress: {processed}/{expected}")
        ...     time.sleep(10)
        """
        return self._processed_scans, self._expected_scans

    def run(self):
        """Main thread loop - automatically invoked when thread starts.

        This is the primary execution method called by threading.Thread.start().
        Do NOT call directly; use thread.start() instead.

        Historical: lists and queues the window day by day, then returns.
        Realtime: runs until ``stop()``. A misspelled radar, a historical window
        without volumes, a historical listing that fails its retries, or too many
        skipped realtime volumes raise, ending the thread; the orchestrator then
        finalizes the run as failed. The staging folder is always removed.
        """
        logger.info("Starting %s in %s mode", self.name, self.mode)
        try:
            if self.mode == "historical":
                self._run_historical()
            else:
                self._run_realtime()
        finally:
            try:
                shutil.rmtree(self._staging_dir)
            except OSError as exc:
                logger.warning("Could not remove the staging folder %s: %s", self._staging_dir, exc)

        logger.info("Stopped %s", self.name)

    # ========================================================================
    # Historical: the window one UTC day at a time
    # ========================================================================

    def _run_historical(self) -> None:
        """List each day of the window and queue its volumes before listing the next."""
        start, end = self._parse_time_range()
        end = min(end, self._clock())
        day = start.date()
        while day <= end.date() and not self.stopped():
            midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
            first = max(start, midnight)
            last = min(end, midnight + timedelta(days=1) - timedelta(seconds=1))
            scans = self._listed(
                partial(self.conn.get_avail_scans_in_range, first, last, self.radar)
            )
            logger.info("Historical %s: %d volumes", day, len(scans))
            self._expected_scans += len(scans)
            self._process_scans(scans)
            day += timedelta(days=1)
        if self.stopped():
            return
        if self._expected_scans == 0:
            raise ValueError(
                f"no volumes of {self.radar} in the AWS archive between {start.isoformat()} "
                f"and {end.isoformat()}; check the radar name and the run's window"
            )
        self._historical_complete.set()
        logger.info("Historical download complete: %d volumes", self._processed_scans)

    # ========================================================================
    # Realtime: arrival schedule, newest wins
    # ========================================================================

    def _run_realtime(self) -> None:
        """Check the radar, then look for volumes on the arrival schedule until stopped."""
        self._require_radar()
        next_check = self._clock()
        while not self.stopped():
            if self._clock() >= next_check:
                try:
                    self._discover()
                except Exception:
                    logger.exception(
                        "Looking for new volumes failed; trying again at the next check"
                    )
                next_check = next_check_at(self._last_published, self._clock(), self._options)
            self._take_candidate()
            if self._skipped >= self.max_skipped_scans:
                message = (
                    f"Realtime processing fell behind: skipped {self._skipped} volumes of "
                    f"{self.radar} (max_skipped_scans={self.max_skipped_scans}); stopping the "
                    "run so it can be restarted with new tracks"
                )
                logger.error(message)
                raise RuntimeError(message)
            self._sleep(_TICK_SECONDS)

    def _require_radar(self) -> None:
        """Refuse a radar with no data in the archive yesterday or today (UTC)."""
        today = self._clock().date()
        days = (today - timedelta(days=1), today)
        for day in days:
            ymd = (f"{day:%Y}", f"{day:%m}", f"{day:%d}")
            radars = self._listed(partial(self.conn.get_avail_radars, *ymd))
            if self.radar in radars:
                return
        raise ValueError(
            f"radar {self.radar!r} has no data in the AWS archive on {days[0]} or {days[1]}; "
            "check the radar name"
        )

    def _discover(self) -> None:
        """List the volumes published since the last check; the newest becomes the candidate."""
        now = self._clock()
        if self._last_key is None:
            start = now - timedelta(minutes=self.latest_minutes)
            window = self._listed(
                lambda: self.conn.get_avail_scans_in_range(start, now, self.radar)
            )
            if window:
                self._seen(window[-1])
                self._process_scans(window[-self.latest_files :])
            return
        last_key = self._last_key
        new = self._listed(lambda: self.conn.get_scans_after(self.radar, last_key, now))
        if not new:
            return
        self._seen(new[-1])
        passed = ([self._candidate] if self._candidate is not None else []) + new[:-1]
        if passed:
            self._skip(passed)
        self._candidate = new[-1]

    def _seen(self, scan: ArchiveScan) -> None:
        self._last_key = scan.key
        self._last_published = scan.published

    def _take_candidate(self) -> None:
        """Download and queue the candidate once no scan is waiting for the processor."""
        if self._candidate is None or not self.result_queue.empty():
            return
        scan, self._candidate = self._candidate, None
        self._process_scans([scan])

    def _skip(self, scans: list[ArchiveScan]) -> None:
        """Log the volumes passed over because a newer one arrived while processing was behind."""
        self._skipped += len(scans)
        names = [scan.key.rsplit("/", 1)[-1] for scan in scans]
        span = names[0] if len(names) == 1 else f"{names[0]} … {names[-1]}"
        logger.warning(
            "Skipped %d volume%s (%s): processing is behind; skipped %d of max_skipped_scans=%d",
            len(names),
            "" if len(names) == 1 else "s",
            span,
            self._skipped,
            self.max_skipped_scans,
        )

    # ========================================================================
    # Scan fetching and processing
    # ========================================================================

    def _put_until_stopped(self, message: dict, timeout: float = 1.0) -> bool:
        """Put with a bounded wait so a stop request is never ignored.

        The queue's hysteresis pause normally guarantees room, so this only ever waits
        if something else filled the queue. Returns False if stopped first.
        """
        while not self.stopped():
            try:
                self.result_queue.put(message, timeout=timeout)
                return True
            except queue.Full:
                continue
        return False

    def _parse_time_range(self) -> tuple:
        """Parse ISO timestamps to datetime objects."""
        if self.start_time is None or self.end_time is None:
            raise ValueError("a historical aws_nexrad run needs run.start_time and run.end_time")
        start = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(self.end_time.replace("Z", "+00:00"))
        return start, end

    def _listed(self, fetch: Callable[[], list]) -> list:
        """Call one archive listing, retrying failures with linear backoff.

        Tries ``max_fetch_retries`` times; the last failure is raised (a historical
        run fails; a realtime run logs it and tries again at its next check).
        """
        for attempt in range(1, self.max_fetch_retries + 1):
            try:
                return fetch()
            except Exception as e:
                if attempt == self.max_fetch_retries:
                    logger.error("AWS listing failed after %d attempts: %s", attempt, e)
                    raise
                logger.warning(
                    "AWS listing attempt %d/%d failed: %s; retrying",
                    attempt,
                    self.max_fetch_retries,
                    e,
                )
                self._sleep(attempt)
        raise AssertionError("unreachable: max_fetch_retries >= 1")

    def _process_scans(self, scans: list) -> list:
        """Acquire each scan into the store (downloading only when needed) and queue it."""
        acquired = []
        queued = 0

        for scan in scans:
            if self.stopped():
                break

            source_uri = str(scan.key)
            with self._known_files_lock:
                already_queued = source_uri in self._known_files
            if already_queued:
                continue

            # Hold here (not after the download) so the queue depth is exactly
            # how far downloads run ahead of processing.
            self.result_queue.wait_for_room(self.stopped)
            if self.stopped():
                break

            if self._acquire.is_acquired(source_uri):
                message = self._acquire.acquire_existing(source_uri, scan_time=scan.scan_time)
            else:
                local = self._download_scan(scan)
                if local is None:
                    logger.warning("Failed to download: %s", scan.key)
                    continue
                try:
                    message = self._acquire.acquire_file(
                        local, source_uri=source_uri, scan_time=scan.scan_time
                    )
                finally:
                    local.unlink(missing_ok=True)
                acquired.append(source_uri)

            if not self._put_until_stopped(message):
                break
            with self._known_files_lock:
                self._known_files.add(source_uri)
            queued += 1

        if queued > 0 or acquired:
            logger.info("Queued %d files (%d new downloads)", queued, len(acquired))

        if self.mode == "historical":
            self._processed_scans += queued

        return acquired

    def _download_scan(self, scan) -> Path | None:
        """Download one scan into the scratch dir; return the path or None."""
        try:
            # Contain any stdout the conn's download() may emit (we log our own
            # controlled "Downloaded: <name>" below). Logging uses stderr, so this
            # narrow stdout redirect never swallows our own output.
            with contextlib.redirect_stdout(io.StringIO()):
                results = self.conn.download([scan], self._staging_dir, keep_aws_folders=False)
            success = list(results.iter_success())
            if not success:
                return None
            temp_file = Path(success[0].filepath)
            if not temp_file.exists():
                return None
            if temp_file.stat().st_size < self._min_file_size:
                logger.warning("Downloaded file below minimum size, discarding: %s", temp_file.name)
                temp_file.unlink(missing_ok=True)
                return None
            logger.info("Downloaded: %s", temp_file.name)
            return temp_file
        except Exception as e:
            logger.error("Download failed: %s - %s", scan.key, e)
            return None
