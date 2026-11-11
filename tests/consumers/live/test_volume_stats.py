# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Unit tests for _volume_stats — load + join of cell_volume_stats onto a track.

Loads go through the public API only (a fake StoreClient here); joins are pure.
"""

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

from adapt.consumers.live._volume_stats import (  # noqa: E402
    load_track_volume_stats,
    merge_volume_stats,
)


class _FakeClient:
    """StoreClient stand-in: tables() discovery + filtered table() reads."""

    def __init__(self, rows):
        self._df = pd.DataFrame(rows, columns=["run_id", "scan_id", "cell_label", "cell_top_m"])

    def tables(self, collection):
        names = ["cell_volume_stats"] if not self._df.empty else []
        return pd.DataFrame({"table_name": names})

    def table(self, name, collection, run_id=None, filters=None):
        df = self._df
        if run_id is not None:
            df = df[df["run_id"] == run_id]
        for column, value in (filters or {}).items():
            df = df[df[column].isin(value)]
        return df.reset_index(drop=True)


def _track(*scans):
    """A track's history: (scan_id, cell_label) per scan it was seen in."""
    return pd.DataFrame(
        {
            "scan_id": [scan_id for scan_id, _ in scans],
            "cell_label": [label for _, label in scans],
            "cell_uid": ["aaaa"] * len(scans),
        }
    )


def test_load_returns_the_rows_of_the_tracks_scans_only():
    client = _FakeClient(
        [
            ("run1", "sid-1", 1, 9000.0),
            ("run1", "sid-1", 2, 4000.0),
            ("run1", "sid-2", 3, 11000.0),
            ("run1", "sid-9", 1, 1000.0),  # a scan the track was not seen in
            ("run2", "sid-1", 1, 5000.0),  # another run
        ]
    )
    out = load_track_volume_stats(client, "KTST", "run1", _track(("sid-1", 1), ("sid-2", 3)))
    assert sorted(out["cell_top_m"]) == [4000.0, 9000.0, 11000.0]


def test_load_missing_table_returns_empty():
    out = load_track_volume_stats(_FakeClient([]), "KTST", "run1", _track(("sid-1", 1)))
    assert out.empty


def test_merge_joins_cloud_top_on_the_cells_label_at_each_scan():
    track = _track(("sid-1", 1), ("sid-2", 3)).assign(cell_area_sqkm=[10.0, 12.0])
    vol = pd.DataFrame(
        {
            "run_id": ["run1"] * 3,
            "scan_id": ["sid-1", "sid-1", "sid-2"],
            "cell_label": [1, 2, 3],  # label 2 at sid-1 is another cell
            "cell_top_m": [9000.0, 4000.0, 11000.0],
            "cell_area_sqkm": [0.0, 0.0, 0.0],
        }
    )
    out = merge_volume_stats(track, vol)
    assert list(out["cell_top_m"]) == [9000.0, 11000.0]
    # Overlapping columns are taken from the track frame, not duplicated.
    assert list(out["cell_area_sqkm"]) == [10.0, 12.0]


def test_merge_left_join_keeps_unmatched_track_rows():
    track = pd.DataFrame({"scan_id": ["s1", "s2"], "cell_label": [1, 1], "v": [1, 2]})
    vol = pd.DataFrame({"scan_id": ["s1"], "cell_label": [1], "cell_top_m": [9000.0]})
    out = merge_volume_stats(track, vol)
    assert len(out) == 2
    assert out["cell_top_m"].isna().sum() == 1


def test_merge_empty_volume_is_noop():
    track = pd.DataFrame({"scan_id": ["s1"], "v": [1]})
    out = merge_volume_stats(track, pd.DataFrame())
    pd.testing.assert_frame_equal(out, track)


def test_merge_nonempty_frame_without_scan_id_raises():
    # Only pre-identity data can produce volume rows lacking scan_id — that is
    # a recreate condition, never a silent no-op merge.
    track = pd.DataFrame({"scan_id": ["s1"], "v": [1]})
    vol = pd.DataFrame({"scan_time": ["2026-06-06T00:00:00Z"], "cell_top_m": [9000.0]})
    with pytest.raises(ValueError, match="Recreate"):
        merge_volume_stats(track, vol)
