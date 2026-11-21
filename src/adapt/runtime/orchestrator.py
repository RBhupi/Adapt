# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Multi-threaded pipeline orchestration.

Coordinates downloader and processor threads with queue-based inter-thread
communication. Manages lifecycle, monitoring, and graceful shutdown. Owns the
single run lifecycle in the store registry and refuses to run against an
uninitialized root.
"""

import json
import logging
import queue
import random
import sys
import threading
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from adapt.contracts.execution_history import RunStart, RunSummary
from adapt.persistence.errors import StoreError
from adapt.persistence.execution_history import StoreExecutionHistory
from adapt.persistence.store import Collection, Store
from adapt.persistence.store_registry import RunStart as StoreRunStart
from adapt.persistence.store_registry import StoreRegistry
from adapt.runtime.acquire import StoreAcquirer
from adapt.runtime.console_status import ConsoleStatus
from adapt.runtime.history_handler import HistoryLogHandler
from adapt.runtime.logging_setup import configure_logging, shutdown_logging
from adapt.runtime.observability import build_observability, obs_settings
from adapt.runtime.processor import RadarProcessor
from adapt.runtime.provenance import capture_provenance, config_hash, module_provenance
from adapt.runtime.run_reporter import RunReporter
from adapt.runtime.scan_queue import BackpressureQueue
from adapt.runtime.sources import source_registry

if TYPE_CHECKING:
    from adapt.configuration.schemas.internal import InternalConfig
    from adapt.contracts.observability import Observability
    from adapt.contracts.source import ScanSource

__all__ = ["PipelineOrchestrator"]

logger = logging.getLogger(__name__)

# Span names that wrap modules rather than being modules themselves; excluded from
# the per-module summary breakdown (they share the module_duration_seconds histogram).
_WRAPPER_SPANS = frozenset({"pipeline", "scan"})

# Monitor-loop cadence; also the live status-line tick (spinner + elapsed) interval.
_STATUS_TICK_SECONDS = 0.5

# Shutdown grace per worker: how long stop() waits for a thread to exit after
# signalling it, before warning that it may be stuck. The processor cannot be
# interrupted mid-scan, so it needs long enough to finish the in-flight scan
# (ingest dominates, tens of seconds) and persist its data before exiting; a
# warning after this grace means the thread is genuinely wedged, not just busy.
_PROCESSOR_SHUTDOWN_GRACE_SECONDS = 60.0
_DOWNLOADER_SHUTDOWN_GRACE_SECONDS = 5.0


class PipelineOrchestrator:
    """Manages the multi-threaded radar processing pipeline.

    This is the main entry point for running ``adapt``. It coordinates two
    worker threads (downloader, processor) using queues for inter-thread
    communication. The orchestrator handles startup, monitoring, and graceful
    shutdown of the processing pipeline.

    **Pipeline Architecture:**

    1. **Downloader Thread**: Discovers and downloads NEXRAD Level-II files
       from AWS in realtime or historical mode.

    2. **Processor Thread**: Processes downloaded files through the full
       scientific pipeline:
       - Load and regrid Level-II data to Cartesian grid
       - Segment cells using configurable threshold method
       - Compute motion projections (frame 2 and beyond)
       - Extract cell-level statistics
       - Persist segmentation to NetCDF and statistics to SQLite
       - Commit every artifact and table into the store


    **Modes:**

    - **Realtime**: Polls for latest files within a rolling time window
      (e.g., "files from last 60 minutes"). Useful for operational monitoring.

    - **Historical**: Downloads all files within a fixed time range
      (start_time to end_time). Useful for batch reprocessing and research.

    **Queue Management:**

    Inter-thread queues have configurable size limits (default 100 items).
    Larger queues enable higher throughput but use more memory. Smaller
    queues provide backpressure (slow down downloader if processor falls behind).

    **Resumability:**

    After every complete scan the processor saves a checkpoint. A run started
    again with its id goes on from it: complete scans are skipped, the scan
    that was in flight (or failed last) is processed again, and the modules
    continue from the state they had — the result is the same as if the run
    had never stopped.

    **Logging:**

    All output goes to both console and log file (logs/{radar}_pipeline.log).
    Log level controlled via config: "DEBUG", "INFO", "WARNING", "ERROR".

    Example usage::

        from adapt.configuration.pipeline_file import load_pipeline_file
        from adapt.configuration.pipeline_run import run_config
        from adapt.runtime.orchestrator import PipelineOrchestrator

        path = Path("nexrad.yaml")  # a pipeline file in an initialized store
        config = run_config(load_pipeline_file(path), path)
        status = PipelineOrchestrator(config).start(max_runtime=60)  # stop after 60 min
    """

    def __init__(
        self,
        config: "InternalConfig",
        max_queue_size: int | None = None,
        close_repository_on_stop: bool = True,
        skip_failed: bool = False,
    ):
        """Initialize orchestrator with fully resolved runtime configuration.

        Parameters
        ----------
        config : InternalConfig
            Fully resolved runtime configuration (from ``pipeline_run.run_config``).
            Already contains all directory paths, run ID, and validated settings.

        max_queue_size : int, optional
            Override for ``config.downloader.max_queue_size``. Left unset the
            configured value is used.
        """
        self.config = config
        self.max_queue_size = (
            max_queue_size if max_queue_size is not None else config.downloader.max_queue_size
        )

        # Queue for source -> processor communication. A source that fetches
        # ahead is held at this level and resumes at queue_resume_fraction of it
        # (BackpressureQueue.wait_for_room), so in normal operation a put() never
        # blocks and the processor is never starved.
        self.downloader_queue = BackpressureQueue(
            self.max_queue_size, config.downloader.queue_resume_fraction
        )

        # Fail fast: the pipeline never runs against an uninitialized root.
        self.store = Store.open(config.base_dir)

        # Threads (created in start())
        self.downloader: ScanSource | None = None
        self.processor: RadarProcessor | None = None

        # Store composition (built in start())
        self.run_id = config.run_id
        self.registry: StoreRegistry | None = None
        self.collection: Collection | None = None
        self.history: StoreExecutionHistory | None = None

        # Lifecycle state
        self._stop_event = False
        # External stop request (Ctrl+C / SIGTERM via request_stop()); separate from
        # the stop() guard. A requested stop finalizes the run as 'cancelled'.
        self._stop_requested = False
        self._failed = False  # a worker thread died, or start() raised after registration
        # Set right after the run row is committed (begun or resumed): from then on
        # the run is visible to readers of the store, e.g. a dashboard following it.
        self.run_registered = threading.Event()
        # Terminal status start() returns; replaced by the status the registry
        # records — stays 'failed' only if that record could not be written.
        self._run_status = "failed"
        self._start_time: float | None = None
        self._max_duration: float | None = None
        self._close_repository_on_stop = close_repository_on_stop
        # A resumed run tries a scan it failed before again, unless told not to.
        self._skip_failed = skip_failed

        # Telemetry (built in start()); kept here so stop() is safe before start().
        self._obs: Observability | None = None
        self._root_span: AbstractContextManager | None = None
        self._root_trace_id = ""
        self._history_handler: HistoryLogHandler | None = None
        self._reporter: RunReporter | None = None
        self._status: ConsoleStatus | None = None
        self.summary: RunSummary | None = None  # the end-of-run summary, once finalized

    def _build_observability(self) -> "Observability":
        """Build the telemetry provider from the resolved config.

        Injects production clocks/rng here (the only place wall-clock reads are
        legal for telemetry). Trace ids are random per run.
        """
        return build_observability(
            obs_settings(self.config.logging),
            clock=time.perf_counter,
            wall_clock=lambda: datetime.now(UTC),
            rng=random.Random(),
        )

    def _enabled_module_names(self) -> tuple[str, ...]:
        """The processor's enabled per-scan modules (the header lists them)."""
        assert self.processor is not None
        return self.processor.module_names

    def _record_run_start(self, radar: str) -> None:
        """Begin the ONE run lifecycle in the registry and print the console header."""
        assert self.registry is not None
        assert self._reporter is not None
        assert self.processor is not None
        prov = capture_provenance()
        modules = self._enabled_module_names()
        config_json = self.config.model_dump_json()
        try:
            self.registry.get_run(self.run_id)
        except StoreError:
            self.registry.begin_run(
                StoreRunStart(
                    run_id=self.run_id,
                    collection_id=radar,
                    config_hash=config_hash(config_json),
                    config_json=config_json,
                    pipeline_version=prov.software_version,
                    environment_json=json.dumps(
                        {
                            "git_commit": prov.git_commit,
                            "hostname": prov.hostname,
                            "username": prov.username,
                            "python_version": prov.python_version,
                            "platform": prov.platform,
                            "enabled_modules": list(modules),
                            "mode": self.config.mode,
                            "source": self.config.source,
                        }
                    ),
                )
            )
        else:
            # --run-id continuation: reopen the ONE run record.
            self.registry.resume_run(self.run_id)
        self.registry.record_module_provenance(self.run_id, module_provenance(self.config))
        self.run_registered.set()
        header = RunStart(
            run_id=self.run_id,
            pipeline=self.config.source,
            pipeline_version=prov.software_version,
            site=radar,
            dataset=radar,
            instrument="NEXRAD",
            mode=self.config.mode,
            start_time=datetime.now(UTC),
            configuration_hash=config_hash(config_json),
            configuration_file=self.config.pipeline.file,
            provenance=prov,
            enabled_modules=modules,
        )
        self._reporter.header(header)
        self._reporter.methods(self.processor.method_lines)

    def _finalize_history(self) -> None:
        """Print the run summary, then persist history.

        The console summary is built from in-memory telemetry + the drained handler
        counts and printed FIRST, so it never depends on a database write succeeding
        (a DB failure during shutdown is logged loudly but cannot hide the summary).
        """
        if self._obs is None or self.history is None:
            return
        warnings: list = []
        errors: list = []
        if self._history_handler is not None:
            warnings, errors = self._history_handler.drain()

        summary = self._build_run_summary(
            self._outcome(), warnings=len(warnings), errors=len(errors)
        )
        if self._reporter is not None:
            self._reporter.summary(summary)

        # Persist after the user-facing summary is out. Failures here are reported
        # loudly but must not abort the orderly shutdown.
        try:
            if warnings:
                self.history.record_warnings(self.run_id, warnings)
            if errors:
                self.history.record_errors(self.run_id, errors)
            self.summary = summary
            self._run_status = self.history.finalize_run(summary)
        except Exception:
            logger.exception("Failed to persist execution history on shutdown")

    def _outcome(self) -> str:
        """The run's summary status: failed, else cancelled (stop requested), else success."""
        violated = self.processor is not None and self.processor.contract_violated()
        if self._failed or violated:
            return "failed"
        return "cancelled" if self._stop_requested else "success"

    def _build_run_summary(self, status: str, *, warnings: int, errors: int) -> RunSummary:
        """Aggregate the end-of-run summary from in-memory telemetry metrics.

        warnings/errors are passed in (drained from the history handler) so the
        summary never reads the database — keeping it robust during shutdown.
        """
        assert self._obs is not None
        m = self._obs.metrics
        scan_times = m.histogram_values("scan_processing_time")
        totals = m.histogram_totals_by_label("module_duration_seconds", "stage")
        counts = m.histogram_counts_by_label("module_duration_seconds", "stage")
        # "pipeline" (root) and "scan" are wrapper spans, not modules — exclude them
        # from the per-module breakdown so the table shows only real stages.
        module_stats = tuple(
            (name, counts.get(name, 0), total)
            for name, total in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            if name not in _WRAPPER_SPANS
        )
        slowest = tuple((name, total) for name, _calls, total in module_stats[:3])
        duration = (time.time() - self._start_time) if self._start_time else 0.0
        return RunSummary(
            run_id=self.run_id,
            status=status,
            end_time=datetime.now(UTC),
            duration_seconds=duration,
            files_processed=int(m.counter_total("files_processed_total")),
            scans_processed=len(scan_times),
            objects_detected=int(m.counter_total("cells_detected_total")),
            warnings=warnings,
            errors=errors,
            average_scan_time=(sum(scan_times) / len(scan_times)) if scan_times else 0.0,
            maximum_scan_time=max(scan_times) if scan_times else 0.0,
            slowest_stages=slowest,
            module_stats=module_stats,
            failures=0 if self.processor is None else self.processor.failed_scans,
        )

    def _create_source(self):
        """Resolve and construct the ingress source named by ``config.source``.

        Sources are swappable plugins (download, local directory, …) registered in
        adapt.runtime.sources. All are constructed with the same signature and
        acquire raw scans through the injected store gateway.
        """
        assert self.collection is not None
        source_cls = source_registry.get(self.config.source)
        return source_cls(
            config=self.config,
            result_queue=self.downloader_queue,
            acquire=StoreAcquirer(self.collection, self.run_id),
        )

    def start(self, max_runtime: int | None = None) -> str:
        """Start the pipeline and run until completion or a stop request.

        This is a blocking call that builds the processor and the ingress source,
        records the run (setting ``run_registered``), starts both worker
        threads, then enters a monitoring loop. The loop logs status every 30
        seconds and handles mode-specific exit conditions.

        **Realtime Mode:** Runs until ``request_stop()`` or max_runtime is exceeded.

        **Historical Mode:** Automatically exits when all files within the
        start_time/end_time range are queued and processed.

        All output (console + file) logged to logs/pipeline_{radar}.log
        at the level specified in config["logging"]["level"].

        Parameters
        ----------
        max_runtime : int, optional
            Maximum runtime in minutes (realtime mode only).
            If None, runs until ``request_stop()``.
            Ignored in historical mode (uses file completion instead).

        Returns
        -------
        str
            The run's terminal status: ``completed``; ``cancelled`` after
            ``request_stop()``; ``failed`` after a contract violation, a worker
            thread dying, or an error raised once the run was recorded.

        Raises
        ------
        Exception
            Whatever failed during startup or monitoring, after stop() ran. A
            processor or source that cannot be built fails before the run is
            recorded, so it never leaves a run 'running'.

        Examples
        --------
        Run for 60 minutes in realtime mode::

            orch = PipelineOrchestrator(config)
            status = orch.start(max_runtime=60)

        Run historical mode until all files processed::

            orch = PipelineOrchestrator(config)  # mode="historical" in config
            status = orch.start()  # Runs until all files between start/end processed
        """
        radar = self.config.downloader.radar

        # Logging is configured first so that everything below it — repository
        # construction, telemetry setup, and any failure in either — lands in the
        # run's log file rather than being emitted before any handler exists.
        # Live status line (spinner) — TTY only; self-disables when piped or quiet.
        settings = obs_settings(self.config.logging)
        self._status = ConsoleStatus(
            sys.stderr,
            enabled=settings.console_logs and sys.stderr.isatty(),
            clock=time.monotonic,
        )
        log_path = self.store.root / "logs" / f"pipeline_{radar}.log"
        configure_logging(settings, log_path, console_status=self._status)
        self._history_handler = HistoryLogHandler()
        logging.getLogger().addHandler(self._history_handler)
        self._reporter = RunReporter()

        # Store composition: collection domain, registry, run lifecycle.
        self.collection = self.store.collection(radar)
        self.registry = StoreRegistry.get_instance(self.store.root)
        self.registry.register_collection(radar, source_kind=self.config.source)
        self.history = StoreExecutionHistory(self.registry)

        # Build telemetry and open the root pipeline span. The root trace id is handed
        # to the processor thread (contextvars do not cross threads) so the whole run
        # shares one trace.
        self._obs = self._build_observability()
        self._root_span = self._obs.span("pipeline", pipeline_id=self.run_id, dataset_id=radar)
        self._root_span.__enter__()
        self._root_trace_id = self._obs.current().trace_id

        self._start_time = time.time()
        self._max_duration = max_runtime * 60 if max_runtime else None

        logger.info(
            "Pipeline started | run=%s radar=%s mode=%s",
            self.run_id,
            radar,
            self.config.mode.upper(),
        )

        try:
            # Build both workers before the run is recorded: a module set or source
            # that cannot be built fails here and never leaves a run 'running'.
            self.processor = RadarProcessor(
                input_queue=self.downloader_queue,
                config=self.config,
                collection=self.collection,
                registry=self.registry,
                run_id=self.run_id,
                history=self.history,
                observability=self._obs,
                root_trace_id=self._root_trace_id,
                reporter=self._reporter,
                skip_failed=self._skip_failed,
            )
            self.downloader = self._create_source()

            # Open the ONE run record and print the console header, then start
            # both workers.
            self._record_run_start(radar)
            self.downloader.start()
            self.processor.start()

            mode = self.config.mode
            logger.debug("Pipeline running in %s mode. Press Ctrl+C to stop.", mode.upper())
            self._main_loop(mode)
        except Exception:
            self._failed = True  # finalizes a recorded run as 'failed' in stop()
            raise
        finally:
            self.stop()
        return self._run_status

    def _main_loop(self, mode: str):
        """Monitoring loop: check exit conditions and log status."""
        assert self.downloader is not None
        assert self.processor is not None
        assert self._start_time is not None
        last_status_time = time.time()

        while True:
            # 0. Honour an external stop request or a prior stop() (e.g. from the CLI
            #    after SIGTERM/SIGINT). request_stop() lets the orchestrator's OWN thread
            #    run finalize+summary, instead of a daemon that the process kills on exit.
            if self._stop_requested or self._stop_event:
                break

            # 1. Historical completion check (must run before downloader death check)
            if mode == "historical" and self._check_historical_complete():
                break

            # 2. Check for thread failures or self-stops (e.g. ContractViolation)
            if self.processor.stopped():
                logger.critical(
                    "Processor has stopped (likely due to contract violation). Exiting."
                )
                break

            if not self.processor.is_alive():
                logger.critical("Processor thread died unexpectedly. Exiting.")
                self._failed = True
                break

            if not self.downloader.is_alive():
                if mode == "historical" and self.downloader.is_historical_complete():
                    logger.info("Downloader exited after historical completion.")
                    break
                logger.critical("Downloader thread died unexpectedly. Exiting.")
                self._failed = True
                break

            # 3. Mode-specific exit conditions
            if mode == "realtime" and self._max_duration:
                elapsed = time.time() - self._start_time
                if elapsed > self._max_duration:
                    logger.info("Max duration reached")
                    break

            # 4. Status logging (every 30s, file only) + live status line (every tick)
            if time.time() - last_status_time > 30:
                self._log_status()
                last_status_time = time.time()

            if self._status is not None:
                self._status.set(self.processor.current_activity() or "waiting for next scan")
                self._status.tick()

            time.sleep(_STATUS_TICK_SECONDS)

        if self._status is not None:
            self._status.clear()  # leave the terminal clean before stop()/summary

    def _check_historical_complete(self) -> bool:
        """Check if historical mode is complete. Returns True to exit."""
        assert self.downloader is not None
        downloader_complete = self.downloader.is_historical_complete()

        if not downloader_complete:
            return False

        processed, expected = self.downloader.get_historical_progress()
        logger.info("Downloader complete: %d/%d files queued", processed, expected)
        bp = self.downloader_queue.backpressure_stats()
        logger.info(
            "Downloader backpressure: paused %d time(s), %.0f s total (high %d / low %d)",
            bp["pauses"],
            bp["paused_seconds"],
            bp["queue_high"],
            bp["queue_low"],
        )

        # Stop the downloader (ends ingestion) so the queue can only shrink.
        self.downloader.stop()
        logger.info("Stopping downloader thread...")
        self.downloader.join(timeout=_DOWNLOADER_SHUTDOWN_GRACE_SECONDS)
        if self.downloader.is_alive():
            logger.warning(
                "Downloader did not stop within %.0fs — thread may be stuck",
                _DOWNLOADER_SHUTDOWN_GRACE_SECONDS,
            )

        # Drain the queue into the processor (aborts at once on a stop request).
        # The processor keeps consuming here; it is stopped by stop() once the
        # monitoring loop exits, so it is never torn down mid-drain.
        self._drain_queue(self.downloader_queue, "processor")

        logger.info("Historical mode complete")
        return True

    def _drain_queue(self, q: queue.Queue, name: str, timeout: int = 300):
        """Wait for the queue to drain, aborting at once on a stop request.

        On normal historical completion this blocks until the processor has
        consumed every queued file. A stop request (Ctrl+C / SIGTERM) sets
        ``_stop_requested``; the remaining backlog is then abandoned so shutdown
        is prompt instead of grinding through the full queue. Abandoned files
        stay marked "downloaded" (not "analyzed") and resume on the next run.
        """
        wait_count = 0
        last_size = q.qsize()

        while q.qsize() > 0:
            if self._stop_requested or self._stop_event:
                logger.info("Stop requested — abandoning %d queued %s file(s)", q.qsize(), name)
                break

            current_size = q.qsize()
            if current_size == last_size:
                wait_count += 1
            else:
                wait_count = 0  # Reset if progress is being made
                last_size = current_size

            logger.info("Waiting for %s queue: %d remaining", name, current_size)
            time.sleep(1)

            # Abort only on a genuine stall: no item consumed for `timeout`
            # seconds. There is deliberately NO cap on total elapsed time — a
            # healthy processor working through a full queue of 20 slow scans
            # can legitimately take longer than any fixed budget, and the old
            # absolute cap silently dropped the tail of a run (22 of 34 scans
            # processed on KHTX 2021-05-04, no error, run marked completed).
            if wait_count > timeout:
                logger.warning(
                    "%s queue stalled: no progress for %d s with %d file(s) remaining — "
                    "abandoning them (they resume on the next run)",
                    name,
                    wait_count,
                    current_size,
                )
                break

    def request_stop(self) -> None:
        """Interrupt the run: the monitoring loop exits and the run is 'cancelled'.

        Called from the CLI on SIGINT/SIGTERM. Distinct from ``stop()`` so signalling
        the loop never trips ``stop()``'s "already finalized" guard — finalize +
        summary then run on the joined (non-daemon) orchestrator thread, not a daemon
        the process kills on exit.
        """
        self._stop_requested = True
        if self.processor is not None:
            self.processor.stop()  # the in-flight scan finishes; no new scan starts

    def stop(self):
        """Stop the pipeline gracefully and finalize all results.

        Called automatically when start() exits (either by user interrupt or
        mode-specific completion). Safe to call multiple times.

        **Operations:**

        1. Signals all worker threads to stop
        2. Waits for each thread to finish (bounded grace periods)
        3. Finalizes the run record in the store registry
        4. Closes store connections and logs the runtime

        Notes
        -----
        This method should complete within ~20 seconds. If a thread does not
        respond to the stop signal within its timeout, a warning is logged but
        the shutdown continues (graceful degradation).

        Files processed before the final save() will have their statistics
        in the SQLite database. The database is safe to query even while the
        pipeline is running (uses WAL mode).
        """
        if self._stop_event:
            return

        self._stop_event = True

        # Close the root pipeline span if it was opened in start().
        if self._root_span is not None:
            self._root_span.__exit__(None, None, None)
            self._root_span = None

        # Stop the downloader (ends ingestion), then the processor. The processor
        # runs its in-flight scan to completion — including saving its data — before
        # it exits, so _stop_processor() announces that wait, confirms a clean stop,
        # and warns only when the thread is genuinely stuck past its grace.
        if self.downloader and self.downloader.is_alive():
            self.downloader.stop()
            self.downloader.join(timeout=_DOWNLOADER_SHUTDOWN_GRACE_SECONDS)
            if self.downloader.is_alive():
                logger.warning(
                    "Downloader did not stop within %.0fs — thread may be stuck",
                    _DOWNLOADER_SHUTDOWN_GRACE_SECONDS,
                )
        self._stop_processor()

        # Execution history: flush captured warnings/errors, finalize the ONE run
        # record in the registry, and print the console summary. A run that was
        # never recorded (startup failed first) has nothing to finalize.
        if self.run_registered.is_set():
            self._finalize_history()

        # Release store handles (collections + registry). SqliteStore.close() is
        # reopen-safe, so cached registry instances survive a later start().
        if self._close_repository_on_stop:
            self.store.close()
            if self.registry is not None:
                self.registry.close()

        elapsed = time.time() - self._start_time if self._start_time else 0
        logger.info("Pipeline stopped. Runtime: %.1fs", elapsed)

        # Last: the run owns its log file, so releasing it is part of stopping.
        # Nothing may log after this — a later start() reconfigures from scratch.
        shutdown_logging()

    def _stop_processor(self) -> None:
        """Stop the processor, letting it finish its in-flight scan, with clear status.

        The processor cannot be interrupted mid-scan; on shutdown it runs the
        current scan to completion (including saving its data) and then exits.
        When a scan is in flight this announces the wait and confirms a clean stop
        on success; it warns only if the thread is still alive past the grace
        period, which means it is genuinely stuck rather than merely busy.
        """
        proc = self.processor
        if proc is None or not proc.is_alive():
            return
        busy = proc.current_activity() is not None
        if busy:
            self._report("Waiting for the scan in progress to finish and save its data...")
        proc.stop()
        proc.join(timeout=_PROCESSOR_SHUTDOWN_GRACE_SECONDS)
        if proc.is_alive():
            logger.warning(
                "Processor did not stop within %.0fs — thread may be stuck; abandoning on exit",
                _PROCESSOR_SHUTDOWN_GRACE_SECONDS,
            )
        elif busy:
            self._report("Last scan processed and data saved — shutdown clean.")

    def _report(self, message: str) -> None:
        """Emit a user-facing shutdown-status line to the quiet console (and log)."""
        if self._reporter is not None:
            self._reporter.progress(message)
        else:
            logger.info(message)

    def _log_status(self):
        """Log current pipeline status."""
        mode = self.config.mode
        hist_status = ""
        if mode == "historical" and self.downloader:
            processed, expected = self.downloader.get_historical_progress()
            hist_status = f" [{processed}/{expected}]"

        logger.info(
            "Status: D=%s P=%s Q=%d%s",
            "UP" if self.downloader and self.downloader.is_alive() else "DOWN",
            "UP" if self.processor and self.processor.is_alive() else "DOWN",
            self.downloader_queue.qsize(),
            hist_status,
        )

    def close_store(self) -> None:
        """Close the store's collection handles if open."""
        self.store.close()
