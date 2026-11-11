# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar data processor thread.

Reads acquired-scan messages from the source queue, resolves the raw object
through the store, and runs every enabled per-scan module as one graph
(``scan_graph``), storing each module's outputs as soon as it finishes. A
module that needs more prior scans than are usable is skipped, and so is
everything downstream of it; a module that raises fails the scan, which then
counts as a scan that never arrived. The processor names no module.

A run over a stored run (``source: stored_run``) walks that run's scans again:
its listed modules run, and what they read from modules not listed this time
is read back from the store.

After every complete scan the processor saves a checkpoint — what the modules
carry to their next scan, and the earlier outputs they read back — so a run
resumed with its id goes on exactly as if it had never stopped.

Responsibilities of this class (orchestration only):
- Queue management: pop acquired-scan messages, mark task done
- Completed-scan skip via the collection catalog (scan_is_complete)
- Rolling history: provide ``prior_scans``, bounded by the time-gap limit
- Module state: hand each module what it returned for its next scan
- Checkpoint: save both after each complete scan; restore them on resume
- Persistence handoff: route module-declared outputs through the StoreOutputRouter
- Record what became of every module in each scan (done, skipped, failed)
- Stop/start lifecycle
"""

import logging
import pickle
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.contracts import ContractViolation, PersistenceMeta
from adapt.contracts.observability import Observability
from adapt.execution.graph.executor import GraphExecutor, GraphRun
from adapt.execution.module_registry import registry as module_registry
from adapt.execution.pipeline_builder import (
    _ensure_modules_registered,
    carried_keys,
    resolve_enabled_modules,
    scan_graph,
)
from adapt.persistence.checkpoints import (
    discard_other_checkpoints,
    read_checkpoint,
    write_checkpoint,
)
from adapt.persistence.errors import StoreError
from adapt.persistence.output_router import StoreOutputRouter
from adapt.runtime.diagnostics import silence_hdf5_errors
from adapt.runtime.observability import disabled_observability
from adapt.runtime.store_inputs import NOT_STORED, ScanAt, read_store_input
from adapt.utils.time import from_scan_iso

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
    ``prior_scans`` (the usable previous scans), runs the modules and stores
    each one's outputs when it finishes. ``GraphExecutor`` enforces the
    contracts at every edge; the processor itself performs no validation.

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
        skip_failed: bool = False,
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

        # A run over a stored run processes every scan again (its listed modules
        # are what is new) and may read any stored output; a live run reads from
        # the store only what no module produces.
        self._stored_run = config.source == "stored_run"
        self._module_configs = resolve_module_configs(config, [m.name for m in modules])
        nodes = scan_graph(modules, self._module_configs, stored_run=self._stored_run)
        self._modules = [n.module for n in nodes]
        self._executor = GraphExecutor(nodes, observability=self._obs)
        self._router = StoreOutputRouter(collection)

        # Rolling history, newest last: of each complete scan, its identity and
        # the outputs modules read back from earlier scans (``prior_inputs``).
        self._scan_history: list[dict] = []
        self._max_history = config.processor.max_history
        self._prior_keys = frozenset(key for m in modules for key in m.prior_inputs)
        # What each module returned for its next scan (None before it has run).
        self._carried_keys = carried_keys(modules)
        self._carried: dict = dict.fromkeys(self._carried_keys)
        # Resuming: a scan the run failed earlier is tried again unless told not to.
        self._skip_failed = skip_failed
        # The scan time of the checkpoint this run goes on from, if it has one.
        self._resumed_after: datetime | None = None
        # Prior scans the most demanding module needs (none without modules).
        # ``prior_scans`` always reaches back at least one scan: detection's seed
        # carry reads the last one.
        self._prior_needed = max((m.required_history for m in modules), default=1) - 1
        # One bound for every stage that pairs frames (see global_.max_scan_gap_minutes):
        # beyond it no seed is carried, no projection runs and tracking resets.
        self._max_time_gap_minutes = config.global_.max_scan_gap_minutes
        self._last_skipped = False
        if not self._stored_run:
            self._restore_checkpoint()

    @property
    def module_names(self) -> tuple[str, ...]:
        """Enabled per-scan modules, in registration order."""
        return tuple(m.name for m in self._modules)

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

        if not self._stored_run and self._already_settled(scan_id, queued_scan_time):
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
                "run_id": self.run_id,
                "scan_id": scan_id,
                "scan_time": self._normalize_scan_time(queued_scan_time),
                **self._module_configs,
            }

            # ── One pass over the modules. ``prior_scans`` is the usable history;
            # a module needing more than it holds is skipped by the executor,
            # with everything downstream of it. Every module declared how its
            # outputs are stored; the router writes them as the module finishes
            # (no module names in this class).
            scan_time = base_ctx["scan_time"]
            prior = self._usable_prior_scans(scan_time, Path(filepath).name)
            meta = PersistenceMeta(
                scan_time=scan_time,
                scan_id=scan_id,
                run_id=self.run_id,
                source_file=str(filepath),
                collection_id=self.config.downloader.radar,
            )
            scan = ScanAt(self.collection, self.run_id, scan_id, scan_time)
            run = self._executor.run(
                {**base_ctx, **self._carried, _PRIOR_SCANS: prior},
                history=len(prior),
                before_node=lambda node, context: _read_from_store(node, context, scan),
                after_node=lambda node, context: self._router.persist([node.module], context, meta),
            )
            elapsed_s = time.perf_counter() - t0
            statuses = _statuses(self.module_names, run)
            if run.failed:
                # A failed scan is a scan that never arrived: it does not join
                # the history and the modules' state stays as it was, so the
                # next scan pairs with the last good one.
                self.collection.catalog.record_scan_pass(self.run_id, scan_id, statuses)
                for name, exc in run.failed.items():
                    logger.error(
                        "Scan failed | scan=%s module=%s elapsed=%.2fs %s: %s",
                        file_id,
                        name,
                        elapsed_s,
                        type(exc).__name__,
                        exc,
                        exc_info=exc,
                        extra={"elapsed_s": round(elapsed_s, 3), "error_type": type(exc).__name__},
                    )
                return False

            # ``result`` becomes the next history entry: it must not hold its
            # predecessors, or every scan's grids would be retained.
            result = {k: v for k, v in run.context.items() if k != _PRIOR_SCANS}

            # Record the collection location from the first scan (kept after that)
            grid_ds = result.get("grid_ds") or result.get("grid_ds_2d")
            if grid_ds is not None:
                lat = grid_ds.attrs.get("radar_latitude")
                lon = grid_ds.attrs.get("radar_longitude")
                if lat is not None and lon is not None:
                    self.registry.ensure_collection_location(
                        self.config.downloader.radar, lat=float(lat), lon=float(lon)
                    )

            # ── The scan is complete: it joins the history and the modules'
            # state moves on — once both are checkpointed with its completion.
            entry = {key: result[key] for key in self._prior_keys if key in result}
            history = [*self._scan_history, {**entry, "scan_id": scan_id, "scan_time": scan_time}]
            history = history[-self._max_history :]
            carried = {key: result[key] for key in self._carried_keys}
            self._complete_scan(scan_id, statuses, history, carried)
            self._scan_history, self._carried = history, carried

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
            # What broke outside a module (the store, the catalog): one
            # context-rich failure line + exactly one traceback. scan_id rides
            # the bound context; elapsed/error_type are structured for the JSON log.
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

    # ── Checkpoint and resume ─────────────────────────────────────────────────

    def _complete_scan(self, scan_id: str, statuses: dict, history: list, carried: dict) -> None:
        """Mark the scan complete and, in the same catalog transaction, make the
        checkpoint of ``history`` and ``carried`` (as they are after it) the run's
        current one."""
        if self._stored_run:  # a pass over a stored run leaves that run's checkpoint alone
            self.collection.catalog.record_scan_pass(self.run_id, scan_id, statuses)
            return
        payload = pickle.dumps(
            {"history": history[-max(1, self._prior_needed) :], "carried": carried},
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        name = write_checkpoint(self.collection, self.run_id, scan_id, payload)
        self.collection.catalog.record_scan_pass(self.run_id, scan_id, statuses, checkpoint=name)
        discard_other_checkpoints(self.collection, self.run_id, keep=name)

    def _restore_checkpoint(self) -> None:
        """Go on from the run's checkpoint, if it has one: the history and the
        modules' state as they were after the last complete scan."""
        row = self.collection.catalog.run_checkpoint(self.run_id)
        if row is None:
            return
        saved = pickle.loads(read_checkpoint(self.collection, row["object_name"]))  # noqa: S301
        missing = sorted(self._carried_keys - set(saved["carried"]))
        if missing:
            raise StoreError(
                f"Run '{self.run_id}' cannot go on from its checkpoint: it holds no state "
                f"for {', '.join(missing)} — the run's modules have changed since"
            )
        self._scan_history = list(saved["history"])
        self._carried = {key: saved["carried"][key] for key in self._carried_keys}
        self._resumed_after = from_scan_iso(row["scan_time"])
        logger.info(
            "Resuming run %s after scan %s (%s)",
            self.run_id,
            row["scan_id"],
            row["scan_time"],
            extra={"console": True},
        )

    def _already_settled(self, scan_id: str, scan_time) -> bool:
        """Whether this run is done with the scan: it is complete, the run went
        past it before it stopped, or it failed and is not to be tried again."""
        scan = self.collection.catalog.get_scan(self.run_id, scan_id)
        if scan is None:
            raise StoreError(f"Queued scan '{scan_id}' is not registered for run '{self.run_id}'")
        if scan["status"] == "complete":
            return True
        when = self._normalize_scan_time(scan_time)
        if self._resumed_after is not None and when is not None and when <= self._resumed_after:
            return True
        return scan["status"] == "failed" and self._skip_failed

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
        """Mark a scan whose pass broke off as failed; never mask the original error."""
        try:
            self.collection.catalog.mark_scan_failed(self.run_id, scan_id)
        except StoreError as exc:
            logger.error("Could not record scan failure for '%s': %s", scan_id, exc)


def _read_from_store(node, context: dict, scan: ScanAt) -> str | None:
    """Add to ``context`` the inputs of ``node`` that no module of this pass
    produced; say which one the store does not hold for the scan, if any."""
    for key in node.inputs:
        if key in context:
            continue
        value = read_store_input(key, scan)
        if value is NOT_STORED:
            return f"input '{key}' is not stored for this scan"
        context[key] = value
    return None


def _statuses(modules: tuple[str, ...], run: GraphRun) -> dict[str, tuple[str, str | None]]:
    """Each module's (status, reason) for the catalog, from one pass over the graph."""
    statuses: dict[str, tuple[str, str | None]] = dict.fromkeys(modules, ("done", None))
    statuses.update({name: ("skipped", reason) for name, reason in run.skipped.items()})
    statuses.update(
        {name: ("failed", f"{type(exc).__name__}: {exc}") for name, exc in run.failed.items()}
    )
    return statuses
