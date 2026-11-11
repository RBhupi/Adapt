# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""TrackingModule emits analysis_ds with the uid LUTs and remembers state.

``registration_minutes`` carries the *previous* scan's labels advected forward,
so ``analysis_ds`` needs a second LUT (``registration_cell_uid``) indexed by
previous-scan label. The tracking node builds it from the previous scan's
tracked_cells, which it remembers across run() calls (module instances persist
across files).
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from adapt.execution.nodes.tracking import TrackingModule

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


def _projected_ds() -> xr.Dataset:
    labels = np.zeros((4, 4), dtype=np.int32)
    labels[0, 0] = 1
    reg_minutes = np.zeros((2, 4, 4), dtype=np.int32)
    reg_minutes[:, 1, 1] = 1
    return xr.Dataset(
        {
            "reflectivity": (("y", "x"), np.ones((4, 4), dtype=np.float32)),
            "cell_labels": (("y", "x"), labels),
            "registration_minutes": (("minute", "y", "x"), reg_minutes),
        },
        coords={
            "x": np.arange(4),
            "y": np.arange(4),
            "minute": np.array(["2024-05-18T19:01", "2024-05-18T19:02"], dtype="datetime64[ns]"),
        },
    )


def _tracker_returning(monkeypatch, results):
    """Stand in for the tracker: each scan's tracker returns the next preset
    (tracked_cells, cell_events) pair."""
    from adapt.contracts import TrackingDecisions, TrackingScan
    from adapt.execution.nodes import tracking as node
    from adapt.modules.tracking.module import TrackerState

    pending = list(results)

    class _Tracker:
        state = TrackerState()

        def __init__(self, config, state):
            pass

        def track(self, ds_projected, cell_stats_df, *, scan_id):
            return pending.pop(0)

        @staticmethod
        def decisions():
            scan = TrackingScan(None, "FIRST_SCAN", *([0] * 17))
            return TrackingDecisions(scan, (), (), (), (), ())

    monkeypatch.setattr(node, "CellTracker", _Tracker)


def _tracked(uid: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"cell_label": [1], "cell_uid": [uid], "area": [4.0], "max_reflectivity": [40.0]}
    )


def _context(ds: xr.Dataset, state=None) -> dict:
    return {
        "tracking_config": None,
        "scan_id": "abc123def4567890",
        "scan_time": datetime(2024, 5, 18, 19, 0, tzinfo=UTC),
        "projected_ds": ds,
        "cell_stats": pd.DataFrame({"cell_label": [1]}),
        "cell_adjacency": pd.DataFrame(
            columns=["cell_label_a", "cell_label_b", "touching_boundary_pixels"]
        ),
        "tracking_state": state,
    }


def _run_scans(monkeypatch, tracked_per_scan):
    """One new module per scan, each handed the state the last one returned."""
    _tracker_returning(monkeypatch, [(t, pd.DataFrame()) for t in tracked_per_scan])
    outputs, state = [], None
    for _ in tracked_per_scan:
        outputs.append(TrackingModule().run(_context(_projected_ds(), state)))
        state = outputs[-1]["tracking_state"]
    return outputs


def test_analysis_ds_uses_previous_scan_tracking_for_registration_lut(monkeypatch):
    """The LUT on scan t's analysis_ds maps scan t-1's labels (carried state)."""
    t1, t2 = _tracked("uid-t1"), _tracked("uid-t2")

    out1, out2 = _run_scans(monkeypatch, [t1, t2])

    # Scan t1: no previous tracking yet -> no registration LUT.
    assert "registration_cell_uid" not in out1["analysis_ds"].data_vars
    assert list(out1["analysis_ds"]["cell_uid"].values.astype(str)) == ["NONE", "uid-t1"]

    # Scan t2: registration LUT must map t1's labels; cell_uid maps t2's.
    assert list(out2["analysis_ds"]["registration_cell_uid"].values.astype(str)) == [
        "NONE",
        "uid-t1",
    ]
    assert list(out2["analysis_ds"]["cell_uid"].values.astype(str)) == ["NONE", "uid-t2"]


def test_empty_tracking_does_not_advance_registration_state(monkeypatch):
    """An empty scan neither adds LUTs nor overwrites the remembered mapping."""
    t1, t3 = _tracked("uid-t1"), _tracked("uid-t3")
    empty = pd.DataFrame(columns=["cell_label", "cell_uid"])

    _, out2, out3 = _run_scans(monkeypatch, [t1, empty, t3])

    assert "cell_uid" not in out2["analysis_ds"].data_vars
    # State still maps t1 (the empty scan did not overwrite it).
    assert list(out3["analysis_ds"]["registration_cell_uid"].values.astype(str)) == [
        "NONE",
        "uid-t1",
    ]


def test_input_projected_ds_not_mutated(monkeypatch):
    """analysis_ds is a copy; the dataset in scan history keeps no LUT vars."""
    _tracker_returning(monkeypatch, [(_tracked("u1"), pd.DataFrame())])
    ds = _projected_ds()
    out = TrackingModule().run(_context(ds))
    assert "cell_uid" not in ds.data_vars
    assert "cell_uid" in out["analysis_ds"].data_vars
