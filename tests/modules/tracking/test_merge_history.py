# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A merge target records which cell it absorbed and how old that cell was.

A merge ends the absorbed cell's track while the survivor keeps its own uid and
age, so without these columns the longer history is unrecoverable from the
cell's identity row. Identity is deliberately not transferred — the survivor's
own ``cell_uid`` and ``age_seconds`` are untouched.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from adapt.modules.tracking.tables import Lifecycle, scan_tables
from adapt.utils.time import from_scan_iso
from tests.helpers.tracking_store import no_adjacency

pytestmark = pytest.mark.unit

SCAN_TIME = datetime(2025, 6, 17, 16, 0, 0, tzinfo=UTC)


def _cell(uid: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"cell_label": 1, "cell_uid": uid, "area": 10.0, "max_reflectivity": 45.0}]
    )


def _seen_before(first_seen: dict[str, str]) -> Lifecycle:
    """The lifecycle after each of these tracks was first seen at its time."""
    tracks: dict[str, dict] = {}
    for uid, seen in first_seen.items():
        earlier = scan_tables(
            _cell(uid),
            pd.DataFrame(),
            no_adjacency(),
            scan_id=f"scan-of-{uid}",
            scan_time=from_scan_iso(seen),
            lifecycle=Lifecycle(),
        )
        tracks.update(earlier.lifecycle.tracks)
    return Lifecycle(tracks=tracks)


def _survivor_row(events: list[dict], first_seen: dict[str, str]) -> pd.Series:
    """The identity row of the one cell of the scan, given earlier tracks' first scans."""
    tables = scan_tables(
        _cell("SURVIVOR"),
        pd.DataFrame(events),
        no_adjacency(),
        scan_id="S",
        scan_time=SCAN_TIME,
        lifecycle=_seen_before(first_seen),
    )
    return tables.identity.iloc[0]


def _merge(source_uid: str) -> dict:
    return {
        "event_type": "MERGE",
        "source_cell_uid": source_uid,
        "target_cell_uid": "SURVIVOR",
        "target_cell_label": 1,
        "event_group_id": f"MERGE:{source_uid}",
    }


def test_merge_records_the_absorbed_cell_and_its_lifetime():
    row = _survivor_row(
        [_merge("ABSORBED")],
        {"SURVIVOR": "2025-06-17T15:55:00Z", "ABSORBED": "2025-06-17T15:00:00Z"},
    )
    assert row["merged_from_cell_uid"] == "ABSORBED"
    assert row["merged_from_age_seconds"] == pytest.approx(3600.0)
    # identity is NOT transferred: the survivor keeps its own, shorter age
    assert row["cell_uid"] == "SURVIVOR"
    assert row["age_seconds"] == pytest.approx(300.0)


def test_the_oldest_source_wins_when_several_merge_at_once():
    row = _survivor_row(
        [_merge("YOUNG"), _merge("OLD")],
        {
            "SURVIVOR": "2025-06-17T15:55:00Z",
            "YOUNG": "2025-06-17T15:50:00Z",
            "OLD": "2025-06-17T14:00:00Z",
        },
    )
    assert row["merged_from_cell_uid"] == "OLD"
    assert row["merged_from_age_seconds"] == pytest.approx(7200.0)


def test_a_cell_with_no_merge_carries_nulls():
    row = _survivor_row([], {"SURVIVOR": "2025-06-17T15:55:00Z"})
    assert row["merged_from_cell_uid"] is None
    assert pd.isna(row["merged_from_age_seconds"])
