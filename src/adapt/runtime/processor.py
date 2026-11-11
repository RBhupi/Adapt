# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar data processor thread.

Reads acquired-scan messages from the source queue, resolves the raw object
through the store, and runs every enabled per-scan module as one graph
(``scan_graph``). The graph is walked in two passes: the core modules, then —
after the scan's core outputs are stored — the extended ones. A module that
needs more prior scans than are usable is skipped, and so is everything
downstream of it; the processor names no module.

Responsibilities of this class (orchestration only):
- Queue management: pop acquired-scan messages, mark task done
- Completed-scan skip via the collection catalog (scan_is_complete)
- Rolling history: provide ``prior_scans``, bounded by the time-gap limit
- Persistence handoff: route module-declared outputs through the StoreOutputRouter
- Stop/start lifecycle
"""

import logging
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import ContractViolation, PersistenceMeta
from adapt.contracts.observability import Observability
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.module_registry import registry as module_registry
from adapt.execution.pipeline_builder import (
    _ensure_modules_registered,
    resolve_enabled_modules,
    scan_graph,
)
from adapt.modules.base import EXTENDED_PHASE, POSTPROCESS_PHASE
from adapt.persistence.errors import StoreError
from adapt.persistence.output_router import StoreOutputRouter
from adapt.runtime.diagnostics import silence_hdf5_errors
from adapt.runtime.observability import disabled_observability

if TYPE_CHECKING:
    from adapt.configuration.schemas.internal import InternalConfig
    from adapt.persistence.execution_history import StoreExecutionHistory
    from adapt.persistence.store import Collection
    from adapt.persistence.store_registry import StoreRegistry

__all__ = ["RadarProcessor"]

logger = logging.getLogger(__name__)

# The history key the processor provides. It must never enter a scan's result
# (and hence the rolling history), or each entry would retain its predecessor
# and every scan's grids would stay alive for the whole run.
_PRIOR_SCANS = "prior_scans"


class RadarProcessor(threading.Thread):
    """Worker thread that runs each scan through the one per-scan module graph.

    For each queued scan it provides the scan identity, every module config and
    ``prior_scans`` (the usable previous scans), runs the core modules, stores
    their outputs, then runs the extended modules and stores theirs.
    ``GraphExecutor`` enforces the contracts at every edge; the processor itself
    performs no validation.

    Example usage (called by PipelineOrchestrator)::

        processor = RadarProcessor(
            input_queue=downloader_queue,
            config=config,
            collection=collection,
            registry=registry,
            run_id=run_id,
            history=history,
        )
        processor.start()
        ...
        processor.stop()
    """

    def __init__(
        self,
        input_queue: queue.Queue,
        config: "InternalConfig",
        *,
        collection: "Collection",
        registry: "StoreRegistry",
        run_id: str,
        history: "StoreExecutionHistory",
        name: str = "RadarProcessor",
        observability: Observability | None = None,
        root_trace_id: str = "",
        reporter=None,
    ):
        super().__init__(daemon=True, name=name)

        self.input_queue = input_queue
        self.config = config
        self.collection = collection
        self.registry = registry
        self.run_id = run_id
        self.history = history
        # Telemetry provider, injected by the orchestrator. Absent -> disabled (off),
        # so the rest of this class calls it unconditionally with no `if obs` branches.
        self._obs: Observability = (
            observability if observability is not None else disabled_observability()
        )
        self._root_trace_id = root_trace_id
        # Console reporter (injected). Absent -> no per-scan progress line.
        self._reporter = reporter
        # Observational "what am I doing now" for the live status line (display only).
        self._current_scan_id: str | None = None
        self._stop_event = threading.Event()
        self._contract_violated = False
        self.output_lock = threading.Lock()

        # One graph over every enabled per-scan module. Module instances live as
        # long as the processor (stateful projector/tracker persist across scans).
        _ensure_modules_registered()
        modules = resolve_enabled_modules(module_registry.create_modules(), modules=config.modules)
        logger.info("Enabled modules: [%s]", ", ".join(m.name for m in modules))

        rogue = sorted(m.name for m in modules if m.pipeline_phase >= POSTPROCESS_PHASE)
        if rogue:
            raise ValueError(
                f"Post-process module(s) {', '.join(rogue)} cannot run in the live "
                "pipeline; list them under 'postprocessing:' in the pipeline file"
            )

        self._module_configs = resolve_module_configs(config, [m.name for m in modules])
        nodes = scan_graph(modules)
        core = [n for n in nodes if n.module.pipeline_phase < EXTENDED_PHASE]
        extended = [n for n in nodes if n.module.pipeline_phase >= EXTENDED_PHASE]
        self._core_modules = [n.module for n in core]
        self._extended_modules = [n.module for n in extended]
        self._core_executor = GraphExecutor(core, observability=self._obs)
        # Extended modules run after the scan's core outputs are stored.
        self._extended_executor: GraphExecutor | None = (
            GraphExecutor(extended, observability=self._obs) if extended else None
        )
        self._router = StoreOutputRouter(collection)

        logger.info("RadarProcessor core: [%s]", ", ".join(m.name for m in self._core_modules))
        if extended:
            logger.info(
                "RadarProcessor post-persistence: [%s]",
                ", ".join(m.name for m in self._extended_modules),
            )

        # Rolling history of completed scan contexts, newest last.
        self._scan_history: list[dict] = []
        self._max_history = config.processor.max_history
        # Prior scans the most demanding module needs (none without modules).
        # ``prior_scans`` always reaches back at least one scan: detection's seed
        # carry reads the last one.
        self._prior_needed = max((m.required_history for m in modules), default=1) - 1
        # One bound for every stage that pairs frames (see global_.max_scan_gap_minutes):
        # beyond it no seed is carried, no projection runs and tracking resets.
        self._max_time_gap_minutes = config.global_.max_scan_gap_minutes
        self._last_skipped = False

    @property
    def module_names(self) -> tuple[str, ...]:
        """Enabled per-scan modules: core, then extended, each in registration order."""
        return tuple(m.name for m in (*self._core_modules, *self._extended_modules))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def stop(self):
        """Signal the processor to stop after the current file finishes."""
        self._stop_event.set()

    def stopped(self) -> bool:
        """True if stop() has been called or a ContractViolation forced stop."""
        return self._stop_event.is_set()

    def contract_violated(self) -> bool:
        """True once a ContractViolation forced this processor to stop."""
        return self._contract_violated

    def current_activity(self) -> str | None:
        """Short 'what am I doing now' for the live status line; None when idle."""
        scan_id = self._current_scan_id
        return f"processing {scan_id}" if scan_id else None

    def run(self):
        """Main processor loop (runs in thread).

        Binds this thread's correlation context once — contextvars do not cross
        threads, so the root trace id is handed in from the orchestrator and
        re-bound here so every log/span emitted on this thread shares one trace.
        """
        # HDF5 error stacks are thread-local: silence libhdf5's stderr dumps on THIS
        # worker thread, where all the NetCDF/HDF5 I/O happens.
        silence_hdf5_errors()
        logger.info("Processor started, waiting for files...")
        with self._obs.bind(
            trace_id=self._root_trace_id,
            pipeline_id=self.run_id,
            dataset_id=self.config.downloader.radar,
            worker_id="processor",
        ):
            self._run_loop()
        logger.info("Processor stopped")

    def _run_loop(self):
        _skip_count = 0
        while not self.stopped():
            try:
                filepath = self.input_queue.get(timeout=1)
            except queue.Empty:
                if _skip_count:
                    logger.info("Skipped %d already-analyzed files", _skip_count)
                    _skip_count = 0
                continue

            try:
                skipped = self.process_file(filepath)
                if skipped is True and self._last_skipped:
                    _skip_count += 1
                else:
                    if _skip_count:
                        logger.info("Skipped %d already-analyzed files", _skip_count)
                        _skip_count = 0
            except Exception:
                logger.exception("Failed to process file: %s", filepath)
            finally:
                self.input_queue.task_done()

        if _skip_count:
            logger.info("Skipped %d already-analyzed files", _skip_count)

    # ── Per-file processing ───────────────────────────────────────────────────

    def process_file(self, filepath) -> bool:
        """Process one queued scan: skip it if complete, else run the module graph.

        Returns
        -------
        bool
            True if processed or already complete, False on error.
        """
        if not isinstance(filepath, dict):
            raise TypeError(
                "processor queue message must be a dict with "
                "'artifact_id', 'scan_id', 'scan_time', 'queued_at' — got "
                f"{type(filepath).__name__}: {filepath!r}"
            )
        message = filepath
        queued_at = message.get("queued_at")
        scan_id = message.get("scan_id")
        queued_scan_time = message.get("scan_time")
        artifact_id = message.get("artifact_id")
        if not scan_id or not artifact_id:
            raise ValueError(
                f"queue message is missing scan identity (scan_id={scan_id!r}, "
                f"artifact_id={artifact_id!r}) — the source must acquire raw objects "
                "into the store before queueing"
            )

        if self.collection.catalog.scan_is_complete(self.run_id, scan_id):
            self._last_skipped = True
            return True
        self._last_skipped = False

        raw = self.collection.catalog.get_artifact(artifact_id)
        if raw is None:
            raise StoreError(f"Queued artifact '{artifact_id}' is not cataloged")
        filepath = str(self.collection.objects_dir / raw["object_name"])
        file_id = Path(raw["original_filename"] or raw["object_name"]).stem

        queue_wait_s = (time.time() - queued_at) if queued_at else None
        logger.info("Processing: %s", Path(filepath).name)

        # Publish the current activity for the live status line; cleared on every exit.
        self._current_scan_id = scan_id
        try:
            # Bind scan context and open the scan span; module spans nest under it and
            # every log on this path carries scan_id. Disabled provider -> no-ops.
            with self._obs.bind(scan_id=scan_id):
                with self._obs.span("scan") as scan_span:
                    ok = self._run_scan(
                        filepath, file_id, queue_wait_s, scan_span, scan_id, queued_scan_time
                    )
                # The scan span has closed; split this scan's drained spans into the module
                # spans (one module_history batch) and the scan span itself (carries n_cells).
                all_spans = self._obs.drain_spans()
                modules = [s for s in all_spans if s.name != "scan"]
                if modules:
                    self.history.record_modules(
                        self.run_id, scan_id, modules, recorded_at=datetime.now(UTC)
                    )
                # One controlled console line per scan, built from the captured telemetry
                # (stage timings + cell count) — never printed from inside a module.
                if self._reporter is not None and modules:
                    scan_rec = next((s for s in all_spans if s.name == "scan"), None)
                    n_cells = int(scan_rec.metadata.get("n_cells", 0)) if scan_rec else 0
                    self._reporter.scan(file_id, modules, n_cells)
                return ok
        finally:
            self._current_scan_id = None

    def _run_scan(
        self, filepath, file_id, queue_wait_s, scan_span, scan_id, queued_scan_time
    ) -> bool:
        """Execute the scientific pipeline for one scan (inside the scan span)."""
        try:
            t0 = time.perf_counter()

            # ── Build base context with all module configs ─────────────────
            base_ctx: dict = {
                "nexrad_file": filepath,
                # The source boundary owns scan identity: scan_id (sha256 of
                # the raw bytes) is the uid-v2 birth input and the join key;
                # scan_time is normalized once so every consumer (modules,
                # persistence, history) sees tz-aware UTC.
                "scan_id": scan_id,
                "scan_time": self._normalize_scan_time(queued_scan_time),
                **self._module_configs,
            }

            # ── One pass over the core modules. ``prior_scans`` is the usable
            # history; a module needing more than it holds is skipped by the
            # executor, with everything downstream of it.
            prior = self._usable_prior_scans(base_ctx["scan_time"], Path(filepath).name)
            core = self._core_executor.run({**base_ctx, _PRIOR_SCANS: prior}, history=len(prior))
            # ``result`` becomes the next history entry: it must not hold its
            # predecessors, or every scan's grids would be retained.
            result = {k: v for k, v in core.context.items() if k != _PRIOR_SCANS}

            scan_time = base_ctx["scan_time"]
            elapsed_s = time.perf_counter() - t0

            # Record the collection location from the first scan (kept after that)
            grid_ds = result.get("grid_ds") or result.get("grid_ds_2d")
            if grid_ds is not None:
                lat = grid_ds.attrs.get("radar_latitude")
                lon = grid_ds.attrs.get("radar_longitude")
                if lat is not None and lon is not None:
                    self.registry.ensure_collection_location(
                        self.config.downloader.radar, lat=float(lat), lon=float(lon)
                    )

            # ── Accumulate scan in rolling history ─────────────────────────
            # base_ctx AFTER result: source-owned keys (scan_time) stay authoritative.
            self._scan_history.append({**result, **base_ctx})
            if len(self._scan_history) > self._max_history:
                self._scan_history.pop(0)

            # ── Persist results: every module declared its specs; the router
            # writes them mechanically (no module names in this class). The scan
            # row was registered by the source at acquisition; completeness
            # follows from the router's product links.
            meta = PersistenceMeta(
                scan_time=scan_time,
                scan_id=scan_id,
                run_id=self.run_id,
                source_file=str(filepath),
                collection_id=self.config.downloader.radar,
            )
            self._router.persist(self._core_modules, result, meta)

            # ── Extended modules: the same graph, after the core write. They
            # follow the core pass's skips; the writer stamps scan identity and
            # time from ``meta``, modules never carry identity columns.
            if self._extended_executor is not None:
                extended = self._extended_executor.run(
                    {**result, _PRIOR_SCANS: prior}, history=len(prior), skipped=core.skipped
                )
                self._router.persist(self._extended_modules, extended.context, meta)

            cell_stats = result.get("cell_stats")
            n_cells = len(cell_stats) if cell_stats is not None else 0
            logger.info(
                "Processed: %d cells | %.1fs%s",
                n_cells,
                elapsed_s,
                f" queue={queue_wait_s:.1f}s" if queue_wait_s is not None else "",
            )

            radar = self.config.downloader.radar
            self._obs.metrics.incr("files_processed_total", dataset_id=radar)
            self._obs.metrics.incr("cells_detected_total", value=float(n_cells))
            self._obs.metrics.observe("scan_processing_time", elapsed_s)
            self._obs.metrics.gauge("queue_depth", self.input_queue.qsize())
            if queue_wait_s is not None:
                self._obs.metrics.observe("download_latency", queue_wait_s)
            scan_span.set(n_cells=n_cells)
            return True

        except ContractViolation as e:
            logger.critical("CRITICAL: Pipeline contract violated: %s. Stopping pipeline.", e)
            self._contract_violated = True
            self.stop()
            self._record_scan_failure(scan_id)
            return False

        except Exception as e:
            # One context-rich failure line + exactly one traceback. scan_id rides the
            # bound context; the failing stage is on the exception's notes (added by
            # GraphExecutor). elapsed/error_type are structured for the JSON log.
            elapsed = time.perf_counter() - t0
            logger.error(
                "Scan failed | scan=%s elapsed=%.2fs %s: %s",
                file_id,
                elapsed,
                type(e).__name__,
                e,
                exc_info=True,
                extra={"elapsed_s": round(elapsed, 3), "error_type": type(e).__name__},
            )
            self._record_scan_failure(scan_id)
            return False

    # ── Rolling history helpers ───────────────────────────────────────────────

    def _usable_prior_scans(self, scan_time, file_name: str) -> list[dict]:
        """Previous scans this scan may use, oldest first; says why when it has too few.

        Empty on the first scan and when the gap to the last scan exceeds the
        multi-scan bound.
        """
        time_gap_valid, time_gap_minutes = self._validate_time_gap(scan_time)
        if len(self._scan_history) < self._prior_needed:
            logger.info(
                "Waiting for history: need %d prior scans, have %d (%s)",
                self._prior_needed,
                len(self._scan_history),
                file_name,
            )
        elif self._prior_needed and not time_gap_valid:
            logger.warning(
                "Time gap %.1f min > %.1f min, skipping multi-scan modules.",
                time_gap_minutes,
                self._max_time_gap_minutes,
            )
        if not time_gap_valid:
            return []
        return self._scan_history[-max(1, self._prior_needed) :]

    def _validate_time_gap(self, current_scan_time=None):
        """Return (valid, gap_minutes) between the most recent history entry and current scan."""
        if not self._scan_history:
            return False, 0.0
        time1 = self._scan_history[-1].get("scan_time")
        time2 = current_scan_time
        if time1 is None or time2 is None:
            return False, 0.0
        gap_minutes = (time2 - time1).total_seconds() / 60.0
        return abs(gap_minutes) <= self._max_time_gap_minutes, gap_minutes

    # ── Persistence helpers ───────────────────────────────────────────────────

    @staticmethod
    def _normalize_scan_time(scan_time):
        """Return a tz-aware (UTC) scan_time so catalog isoformat comparisons match."""
        if scan_time is not None and scan_time.tzinfo is None:
            return scan_time.replace(tzinfo=UTC)
        return scan_time

    def _record_scan_failure(self, scan_id: str) -> None:
        """Mark the scan failed in the catalog; never mask the original error."""
        try:
            self.collection.catalog.mark_scan_failed(self.run_id, scan_id)
        except StoreError as exc:
            logger.error("Could not record scan failure for '%s': %s", scan_id, exc)
