# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Decision outputs: a JSONL log with one line per scan, and the current-selection file.

Both live in the output directory the launcher gives target selection,
``<root>/consumers/<run_id>/target_selection/``, under the fixed names below —
consumers never write into the store's databases. The directory must exist.

The current-selection file is the contract with radar command: ``SCHEMA``
changes whenever its keys do.
"""

import json
from pathlib import Path
from typing import Any

from adapt.consumers.target_selection.selection import ScanDecision
from adapt.utils.files import atomic_write_text
from adapt.utils.time import to_scan_iso

SCHEMA = 1
SELECTION_FILE = "current_selection.json"
DECISION_LOG_FILE = "decisions.jsonl"


def start_decision_log(path: str | Path) -> None:
    """Create the JSONL log empty, replacing any earlier one.

    Every target selection run replays its run from the first scan, so the log
    is that invocation's record of the run: rewritten, never appended to twice.
    """
    atomic_write_text(path, "")


def append_decision(path: str | Path, scan_decision: ScanDecision) -> None:
    """Append one scan's decision — with or without a target — as a single JSON line."""
    decision = scan_decision.decision
    selection = decision.selection
    record = _summary(scan_decision) | {
        "candidates": [{"cell_uid": c.cell_uid, "score": c.score} for c in decision.candidates],
        "trajectory": []
        if selection is None
        else [
            {"lat": p.lat, "lon": p.lon, "lead_seconds": p.lead_seconds}
            for p in selection.trajectory
        ],
        "observation_window": None
        if selection is None
        else [None if t is None else to_scan_iso(t) for t in selection.observation_window],
    }
    # newline="\n": the log is a data product, so its bytes must not depend on the
    # platform's line-ending translation.
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record) + "\n")


def write_current_selection(path: str | Path, scan_decision: ScanDecision) -> None:
    """Atomically replace ``path`` with the decision at one scan (cell nulls when none)."""
    record = {"schema": SCHEMA} | _summary(scan_decision)
    atomic_write_text(path, json.dumps(record, indent=2) + "\n")


def _summary(scan_decision: ScanDecision) -> dict[str, Any]:
    decision = scan_decision.decision
    selection = decision.selection
    return {
        "collection": scan_decision.collection,
        "run_id": scan_decision.run_id,
        "scan_id": scan_decision.scan_id,
        "scan_time": to_scan_iso(decision.scan_time),
        "config_hash": scan_decision.config_hash,
        "cell_uid": None if selection is None else selection.cell_uid,
        "reason": None if selection is None else selection.reason.value,
        "score": None if selection is None else selection.score,
        "stop_reason": None if decision.stop_reason is None else decision.stop_reason.value,
        "previous_cell_uid": decision.previous_cell_uid,
    }
