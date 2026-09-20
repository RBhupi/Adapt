# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The tracker records every candidate it considered and why each cell ended.

Synthetic 8x8 scans (1000 m pixels) with analytically known gate outcomes.
"""

import numpy as np
import pytest

from adapt.modules.tracking.module import CellTracker
from tests.modules.tracking.test_tracker_quality import (
    _cell_stats,
    _make_config,
    _one_cell_scan,
    _synthetic_ds,
)

pytestmark = pytest.mark.unit

T0 = np.datetime64("2024-01-01T12:00:00")
T1 = np.datetime64("2024-01-01T12:05:00")


def _cells_scan(time, spans: dict[int, tuple[int, int]], proj_labels=None):
    """Cells as {label: (col_start, col_stop)} on rows 2-3 of an 8x8 grid."""
    labels = np.zeros((8, 8), dtype=np.int32)
    rows = []
    for lab, (start, stop) in spans.items():
        labels[2:4, start:stop] = lab
        rows.append(
            {
                "id": lab,
                "area": float(2 * (stop - start)),
                "cx": (start + stop - 1) / 2 * 1000.0,
                "cy": 2500.0,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            }
        )
    return _synthetic_ds(time, labels, proj_labels=proj_labels), _cell_stats(time, rows)


def test_first_scan_is_a_frame_with_no_pairs_and_born_cells():
    tracker = CellTracker(_make_config())
    tracker.track(*_one_cell_scan(T0, 2), scan_id="scan0")
    d = tracker.decisions()
    assert d.frame.reset_code == "FIRST_SCAN"
    assert (d.frame.n_prev, d.frame.n_curr, d.frame.n_pairs) == (0, 1, 0)
    assert [u.reason for u in d.unmatched] == ["FIRST_SCAN"]


def test_perfect_continuation_is_one_propagated_pair():
    tracker = CellTracker(_make_config())
    tracker.track(*_one_cell_scan(T0, 2), scan_id="scan0")
    tracker.track(*_one_cell_scan(T1, 3), scan_id="scan1")
    d = tracker.decisions()
    (pair,) = d.candidates
    assert pair.outcome == "CONTINUE"
    assert pair.match_method == "PROPAGATED"
    assert pair.last_stage == "ASSIGNMENT"
    assert pair.opc == pair.ocp == 1.0
    assert pair.cost_rank == 1
    assert pair.speed_ms == pytest.approx(1000.0 / 300.0)
    assert d.frame.n_propagated == 1 and d.frame.n_termination == 0
    assert d.unmatched == ()


def test_overlap_rejection_is_recorded_with_its_numbers():
    """Hull left at x=2, cell at x=4: the 1 km buffer generates the pair, the
    un-buffered hull has zero intersection, so the overlap gate rejects it."""
    tracker = CellTracker(_make_config())
    ds0, stats0 = _one_cell_scan(T0, 2)
    tracker.track(ds0, stats0, scan_id="scan0")
    tracker.track(*_one_cell_scan(T1, 4, proj_labels=ds0["cell_labels"].values), scan_id="scan1")
    d = tracker.decisions()
    (pair,) = d.candidates
    assert (pair.outcome, pair.last_stage) == ("REJECTED_OVERLAP", "OVERLAP")
    assert pair.intersection_px == 0 and pair.opc == 0.0
    assert pair.cost is None
    prev, curr = d.unmatched
    assert (prev.side, prev.reason, prev.best_candidate_label) == (
        "prev",
        "ALL_REJECTED_OVERLAP",
        1,
    )
    assert (curr.side, curr.reason) == ("curr", "ALL_REJECTED_OVERLAP")
    assert d.frame.n_termination == 1 and d.frame.n_initiation == 1


def test_kinematic_rejection_records_speed_and_code():
    tracker = CellTracker(_make_config(max_speed_ms=5.0))
    tracker.track(*_one_cell_scan(T0, 2), scan_id="scan0")
    tracker.track(*_one_cell_scan(T1, 6), scan_id="scan1")  # 13.3 m/s > 5
    (pair,) = tracker.decisions().candidates
    assert pair.overlap_passed is True
    assert (pair.outcome, pair.kinematic_code) == ("REJECTED_KINEMATIC", "VELOCITY_EXCEEDED")
    assert pair.speed_ms == pytest.approx(4000.0 / 300.0)
    assert tracker.decisions().unmatched[0].reason == "ALL_REJECTED_KINEMATIC"


def test_ambiguous_component_records_hungarian_ranks_and_the_loser():
    """A (cols 1-3) and B (cols 4-6) vs X (cols 2-4) and Y (cols 5-7): X overlaps
    both, so nothing is forced and one 2x2 component goes to Hungarian."""
    tracker = CellTracker(_make_config())
    ds0, stats0 = _cells_scan(T0, {1: (1, 4), 2: (4, 7)})
    tracker.track(ds0, stats0, scan_id="scan0")
    tracker.track(
        *_cells_scan(T1, {1: (2, 5), 2: (5, 8)}, proj_labels=ds0["cell_labels"].values),
        scan_id="scan1",
    )
    d = tracker.decisions()
    by_pair = {(c.prev_cell_label, c.curr_cell_label): c for c in d.candidates}
    assert {(1, 1), (2, 1), (2, 2)} <= set(by_pair)
    assert by_pair[(1, 1)].outcome == "CONTINUE" and by_pair[(2, 2)].outcome == "CONTINUE"
    assert by_pair[(2, 1)].outcome == "LOST_ASSIGNMENT"
    # A's buffered hull may graze Y: that pair exists only as an overlap rejection.
    others = set(by_pair) - {(1, 1), (2, 1), (2, 2)}
    assert all(by_pair[p].outcome == "REJECTED_OVERLAP" for p in others)
    assert by_pair[(2, 1)].cost_rank == 2 and by_pair[(2, 2)].cost_rank == 1
    assert {c.match_method for c in d.candidates if c.outcome == "CONTINUE"} == {"HUNGARIAN"}
    assert by_pair[(1, 1)].component_n_prev == 2 and by_pair[(1, 1)].component_n_curr == 2
    assert d.frame.n_hungarian == 2


def test_split_test_is_recorded_pass_or_fail():
    """A 10-px parent splits into a 4-px and a 6-px piece. The parent continues
    into the larger piece (cheaper); the born 4-px piece covers 0.4 of the
    parent's hull, exactly split_overlap, and is recorded as a passing test."""
    tracker = CellTracker(_make_config())
    ds0, stats0 = _cells_scan(T0, {1: (1, 6)})
    tracker.track(ds0, stats0, scan_id="scan0")
    tracker.track(
        *_cells_scan(T1, {1: (1, 3), 2: (3, 6)}, proj_labels=ds0["cell_labels"].values),
        scan_id="scan1",
    )
    d = tracker.decisions()
    tests = {(t.kind, t.tested_cell_label): t for t in d.split_merge_tests}
    born = [u for u in d.unmatched if u.side == "curr"]
    assert d.frame.n_split == 1 and born[0].reason == "SPLIT_CHILD"
    assert tests[("SPLIT", born[0].cell_label)].passed is True
    assert tests[("SPLIT", born[0].cell_label)].threshold == 0.4


def test_gap_reset_marks_every_cell_reset():
    tracker = CellTracker(_make_config(max_tracking_gap_minutes=10.0))
    tracker.track(*_one_cell_scan(T0, 2), scan_id="scan0")
    tracker.track(*_one_cell_scan(np.datetime64("2024-01-01T12:20:00"), 2), scan_id="scan1")
    d = tracker.decisions()
    assert d.frame.reset_code == "TRACK_GAP_EXCEEDED"
    assert d.candidates == ()
    assert {u.reason for u in d.unmatched} == {"RESET"}
    assert d.frame.n_termination == 1 and d.frame.n_initiation == 1
