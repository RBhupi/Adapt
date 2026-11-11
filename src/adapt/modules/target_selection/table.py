# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""One scan's decision as the row the store keeps (the ``selection`` output).

One row per scan and rule: the target (or none), why, its score, what ended the
previous target, and — as JSON text — the ranked candidates, the target's
projected trajectory and its observation window.
"""

import json

import pandas as pd

from adapt.modules.target_selection.selection import Decision
from adapt.utils.frames import REAL, TEXT, typed_frame
from adapt.utils.time import to_scan_iso

__all__ = ["selection_frame"]

_COLUMNS = (
    ("config_hash", TEXT),  # the rules that decided
    ("cell_uid", TEXT),  # the target; none when no cell qualifies
    ("reason", TEXT),
    ("score", REAL),
    ("stop_reason", TEXT),
    ("previous_cell_uid", TEXT),
    ("candidates_json", TEXT),
    ("trajectory_json", TEXT),
    ("observation_window_json", TEXT),
)


def selection_frame(decision: Decision, rules_hash: str) -> pd.DataFrame:
    """The one-row ``selection`` frame of ``decision``, made under the rules ``rules_hash``."""
    selection = decision.selection
    candidates = [{"cell_uid": c.cell_uid, "score": c.score} for c in decision.candidates]
    trajectory = (
        []
        if selection is None
        else [
            {"lat": p.lat, "lon": p.lon, "lead_seconds": p.lead_seconds}
            for p in selection.trajectory
        ]
    )
    window = (
        None
        if selection is None
        else [None if t is None else to_scan_iso(t) for t in selection.observation_window]
    )
    row = {
        "config_hash": rules_hash,
        "cell_uid": None if selection is None else selection.cell_uid,
        "reason": None if selection is None else selection.reason.value,
        "score": None if selection is None else selection.score,
        "stop_reason": None if decision.stop_reason is None else decision.stop_reason.value,
        "previous_cell_uid": decision.previous_cell_uid,
        "candidates_json": json.dumps(candidates),
        "trajectory_json": json.dumps(trajectory),
        "observation_window_json": json.dumps(window),
    }
    return typed_frame([row], _COLUMNS)
