# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Selection outputs: a JSONL log of a run's selections and the current-selection file.

Both live at user-configured paths outside the Adapt store — consumers never
write into the store's databases. The parent directory must exist.
"""

import json
from pathlib import Path

from adapt.consumers.target_selection.selection import ScanSelection
from adapt.utils.files import atomic_write_text
from adapt.utils.time import to_scan_iso


def start_selection_log(path: str | Path) -> None:
    """Create the JSONL log empty, replacing any earlier one.

    Every ``tse run`` replays its run from the first scan, so the log is that
    invocation's record of the run: rewritten, never appended to twice.
    """
    atomic_write_text(path, "")


def append_selection(path: str | Path, scan_selection: ScanSelection) -> None:
    """Append one selection, named by its collection, run and scan, as a single JSON line.

    Raises ValueError when the scan has no selection: only selections are logged.
    """
    selection = scan_selection.selection
    if selection is None:
        raise ValueError(
            f"No selection to log at scan {scan_selection.scan_id} of run {scan_selection.run_id!r}"
        )
    record = {
        "collection": scan_selection.collection,
        "run_id": scan_selection.run_id,
        "scan_id": scan_selection.scan_id,
        "cell_uid": selection.cell_uid,
        "reason": selection.reason.value,
        "score": selection.score,
        "selection_time": to_scan_iso(selection.selection_time),
        "trajectory": [
            {"lat": p.lat, "lon": p.lon, "lead_seconds": p.lead_seconds}
            for p in selection.trajectory
        ],
        "observation_window": [to_scan_iso(t) for t in selection.observation_window],
        "predicted_hulls": selection.predicted_hulls,
    }
    # newline="\n": the log is a data product, so its bytes must not depend on the
    # platform's line-ending translation.
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record) + "\n")


def write_current_selection(path: str | Path, scan_selection: ScanSelection) -> None:
    """Atomically replace ``path`` with the decision at one scan (nulls when none)."""
    selection = scan_selection.selection
    record = {
        "collection": scan_selection.collection,
        "run_id": scan_selection.run_id,
        "scan_id": scan_selection.scan_id,
        "scan_time": to_scan_iso(scan_selection.scan_time),
        "cell_uid": None if selection is None else selection.cell_uid,
        "reason": None if selection is None else selection.reason.value,
        "score": None if selection is None else selection.score,
    }
    atomic_write_text(path, json.dumps(record, indent=2) + "\n")
