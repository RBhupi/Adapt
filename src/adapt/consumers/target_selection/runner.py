# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Headless TSE: one engine decision per complete scan of a run, written to a file.

Reads only through StoreClient; writes only the current-selection file (and the
optional decision log). Radar-agnostic — acting on the selection is another
consumer's job.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from adapt.api import ScanRef, StoreClient
from adapt.consumers.target_selection.config import TSEConfig, config_hash
from adapt.consumers.target_selection.engine import TargetSelectionEngine
from adapt.consumers.target_selection.repository_source import build_snapshot
from adapt.consumers.target_selection.selection import ScanDecision
from adapt.consumers.target_selection.snapshot import Snapshot
from adapt.consumers.target_selection.writer import (
    append_decision,
    start_decision_log,
    write_current_selection,
)
from adapt.utils.time import to_scan_iso


@dataclass(frozen=True)
class TSEJob:
    """Which run to select on, where to write, and whether to follow it live."""

    collection: str
    run_id: str
    selection_path: Path
    follow: bool
    poll_seconds: float


def run(client: StoreClient, config: TSEConfig, job: TSEJob) -> int:
    """Select on every complete scan of ``job.run_id``; return the scans processed.

    A scan is taken once its tracking rows are committed too: following waits
    for them, and in a run that has ended their absence raises RuntimeError.
    With ``job.follow``, keep polling for new scans until that run stops running
    (other runs of the collection are irrelevant). An unknown run raises
    StoreError; a run of another collection raises ValueError.
    """
    owner = client.run(job.run_id).collection_id
    if owner != job.collection:
        raise ValueError(
            f"Run '{job.run_id}' belongs to collection '{owner}', not '{job.collection}'"
        )
    engine = TargetSelectionEngine(config)
    rules_hash = config_hash(config)
    log_path = config.output.decision_log
    if log_path is not None:
        start_decision_log(log_path)
    watermark: ScanRef | None = None
    count = 0
    while True:
        # Checked before the fetch, so a scan completed just before the run ended
        # is still fetched on this pass.
        status = client.run(job.run_id).status
        refs = client.scans_since(job.collection, job.run_id, after=watermark)
        ready = _tracked_prefix(client, job.collection, refs)
        if len(ready) < len(refs) and status != "running":
            pending = refs[len(ready)]
            raise RuntimeError(
                f"Scan {pending.scan_id} ({to_scan_iso(pending.scan_time)}) of run "
                f"{job.run_id!r} is complete but has no tracking rows, and the run is "
                f"{status!r}: they will never be written"
            )
        latest: ScanDecision | None = None
        for ref in ready:
            decision = engine.decide(_snapshot_at(client, config, job.collection, ref))
            latest = ScanDecision(job.collection, job.run_id, ref.scan_id, rules_hash, decision)
            if log_path is not None:
                append_decision(log_path, latest)
        if latest is not None:
            # Only the newest decision is current; a reader must never act on a
            # scan the replay has already moved past.
            write_current_selection(job.selection_path, latest)
            watermark = ready[-1]
        count += len(ready)
        if not job.follow:
            return count
        if not refs or len(ready) < len(refs):
            if status != "running":
                return count
            time.sleep(job.poll_seconds)


def _tracked_prefix(client: StoreClient, collection: str, refs: list[ScanRef]) -> list[ScanRef]:
    """The leading ``refs`` whose tracking rows are committed.

    A scan is complete once segmentation2d is linked, which the tracking node
    does just before committing the scan's cells_by_scan rows; and a complete
    scan always has cells (one without any never completes).
    """
    for i, ref in enumerate(refs):
        if client.cells_at_scan(ref.run_id, ref.scan_id, collection).empty:
            return refs[:i]
    return refs


def _snapshot_at(client: StoreClient, config: TSEConfig, collection: str, ref: ScanRef) -> Snapshot:
    """The engine's view of exactly scan ``ref``."""
    snapshot = build_snapshot(
        client,
        ref.run_id,
        collection,
        growth_window_scans=config.snapshot.growth_window_scans,
        trend_fields=config.snapshot.trend_fields,
        track_summary=config.snapshot.track_summary,
        at=ref.scan_time,
    )
    if snapshot.scan_time != ref.scan_time:
        raise RuntimeError(
            f"Snapshot for scan {ref.scan_id} of run {ref.run_id!r} is at "
            f"{to_scan_iso(snapshot.scan_time)}, not the scan time {to_scan_iso(ref.scan_time)}"
        )
    return snapshot
