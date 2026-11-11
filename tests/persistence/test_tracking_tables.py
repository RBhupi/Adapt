# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The tracking tables in products.db: identity, events, lifecycle and the
``cells_by_scan`` view over them, written as the pipeline writes them."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from adapt.persistence.errors import StoreError
from adapt.persistence.products import SchemaLedger
from adapt.persistence.store import Store, init_store
from tests.helpers.tracking_store import TrackedScans

pytestmark = pytest.mark.unit

SCAN_TIME = datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC)
RUN = "run-1"


def _stats(labels, extra_cols=None):
    data = {"cell_label": labels, "cell_area_sqkm": [10.0] * len(labels)}
    data.update(extra_cols or {})
    return pd.DataFrame(data)


def _tracked(pairs):
    return pd.DataFrame(
        {
            "cell_label": [label for label, _ in pairs],
            "cell_uid": [uid for _, uid in pairs],
            "area": [10.0] * len(pairs),
            "max_reflectivity": [45.0] * len(pairs),
        }
    )


@pytest.fixture
def collection(tmp_path):
    root = init_store(tmp_path / "store")
    store = Store.open(root)
    yield store.collection("KILX")
    store.close()


@pytest.fixture
def ledger(collection):
    return SchemaLedger(collection.products_path, collection.catalog)


@pytest.fixture
def scans(collection):
    return TrackedScans(collection, RUN)


def _write(scans, scan_id, offset_s=0, stats=None, tracked=None, events=None):
    """One scan; unless ``events`` says otherwise, every cell simply goes on."""
    scans.write(
        scan_id,
        SCAN_TIME + timedelta(seconds=offset_s),
        stats=_stats([1]) if stats is None else stats,
        tracked=_tracked([(1, "u1")]) if tracked is None else tracked,
        events=events,
    )


def _rows(collection, table, where="1=1", order="scan_time"):
    conn = sqlite3.connect(collection.products_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = f"SELECT * FROM {table} WHERE {where}"
        return pd.DataFrame([dict(r) for r in conn.execute(sql).fetchall()])
    finally:
        conn.close()


def _columns(collection, table):
    conn = sqlite3.connect(collection.products_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestFreezeOnFirstWrite:
    def test_first_write_creates_the_tables_and_the_view(self, collection, ledger, scans):
        _write(scans, "s1", stats=_stats([1], {"custom_metric": [3.5]}))

        for table in ("cell_identity", "cell_events", "cell_tracks"):
            frozen = ledger.frozen(table)
            assert frozen is not None, table
            assert frozen.owner_module == "tracking"

        cbs_cols = _columns(collection, "cells_by_scan")
        assert "custom_metric" in cbs_cols
        assert {"run_id", "scan_id", "scan_time", "cell_uid", "cell_label"} <= cbs_cols

    def test_counts_and_flags_freeze_as_integers(self, ledger, scans):
        _write(scans, "s1")

        identity = dict(ledger.frozen("cell_identity").columns)
        tracks = dict(ledger.frozen("cell_tracks").columns)
        assert identity["is_initiated_here"] == identity["n_adjacent_cells"] == "INTEGER"
        assert tracks["n_scans"] == "INTEGER"
        assert identity["age_seconds"] == tracks["duration_seconds"] == "REAL"

    def test_a_scan_with_the_view_is_linked_to_it(self, collection, scans):
        _write(scans, "s1")

        assert "cells_by_scan" in collection.catalog.scan_products(RUN, "s1")

    def test_duplicate_label_within_scan_raises(self, scans):
        _write(scans, "s1")

        with pytest.raises(ValueError, match="duplicate"):
            _write(
                scans,
                "s2",
                offset_s=300,
                stats=_stats([1, 2]),
                tracked=_tracked([(1, "u1"), (1, "u2")]),
            )

    def test_new_stats_column_after_freeze_raises(self, scans):
        _write(scans, "s1")

        with pytest.raises(StoreError, match="late_arrival"):
            _write(scans, "s2", offset_s=300, stats=_stats([1], {"late_arrival": [1.0]}))


class TestScience:
    def test_track_continuity_and_age_across_scans(self, collection, scans):
        _write(scans, "s1")
        _write(scans, "s2", offset_s=300)

        history = _rows(collection, "cells_by_scan", "cell_uid = 'u1'").sort_values("scan_time")
        tracks = _rows(collection, "cell_tracks")
        assert list(history["scan_id"]) == ["s1", "s2"]
        assert history["age_seconds"].tolist() == [0.0, 300.0]
        assert tracks.loc[0, "n_scans"] == 2
        assert tracks.loc[0, "duration_seconds"] == 300.0

    def test_writing_a_scan_again_does_not_count_it_twice(self, collection, scans):
        _write(scans, "s1")
        again = TrackedScans(collection, RUN)  # a retried scan: same rows, same keys
        _write(again, "s1")

        assert len(_rows(collection, "cell_events")) == 1
        assert _rows(collection, "cell_tracks").loc[0, "n_scans"] == 1
        assert len(_rows(collection, "cells_by_scan")) == 1

    def test_a_split_marks_its_source_in_the_previous_scan(self, collection, scans):
        _write(scans, "s1")
        events = pd.DataFrame(
            [
                {
                    "event_type": "SPLIT",
                    "source_cell_uid": "u1",
                    "target_cell_uid": "u2",
                    "source_cell_label": 1,
                    "target_cell_label": 2,
                    "cost": 0.1,
                    "is_dominant": True,
                    "event_group_id": "g1",
                }
            ]
        )
        _write(
            scans,
            "s2",
            offset_s=300,
            stats=_stats([1, 2]),
            tracked=_tracked([(1, "u1"), (2, "u2")]),
            events=events,
        )

        s1_cells = _rows(collection, "cells_by_scan", "scan_id = 's1'")
        s2_cells = _rows(collection, "cells_by_scan", "scan_id = 's2'").set_index("cell_uid")
        events_df = _rows(collection, "cell_events", "event_type = 'SPLIT'")
        tracks = _rows(collection, "cell_tracks").set_index("cell_uid")
        assert s1_cells.loc[0, "is_split_source_here"] == 1
        assert s2_cells.loc["u2", "is_split_target_here"] == 1
        assert s2_cells.loc["u1", "is_split_source_here"] == 0
        assert events_df.loc[0, "source_scan_id"] == "s1"
        assert events_df.loc[0, "target_scan_id"] == "s2"
        assert tracks.loc["u2", "origin_type"] == "SPLIT"
        assert tracks.loc["u2", "origin_primary_parent_cell_uid"] == "u1"


def _event(event_type, source, target=None, source_scan=None, target_label=None):
    return {
        "event_type": event_type,
        "source_cell_uid": source,
        "target_cell_uid": target,
        "source_cell_label": 1,
        "target_cell_label": target_label,
        "source_scan_id": source_scan,
        "source_scan_time": None if source_scan is None else SCAN_TIME,
        "cost": None,
        "is_dominant": False,
        "event_group_id": f"{event_type}:{source}",
    }


class TestLatentTracks:
    """A track that vanishes is kept latent; its end or resumption is decided scans
    later and refers back to the scan it was last observed in."""

    def _vanish(self, scans):
        _write(scans, "s1")
        _write(
            scans,
            "s2",
            offset_s=300,
            stats=_stats([2]),
            tracked=_tracked([(2, "u2")]),
            events=pd.DataFrame([_event("LATENT", "u1", source_scan="s1")]),
        )

    def _third_scan(self, scans, event, tracked=((2, "u2"),)):
        _write(
            scans,
            "s3",
            offset_s=600,
            stats=_stats([label for label, _ in tracked]),
            tracked=_tracked(list(tracked)),
            events=pd.DataFrame([event]),
        )

    def test_a_latent_track_is_not_terminated(self, collection, scans):
        self._vanish(scans)

        s1 = _rows(collection, "cells_by_scan", "scan_id = 's1'")
        tracks = _rows(collection, "cell_tracks").set_index("cell_uid")
        assert s1.loc[0, "is_terminated_after_here"] == 0
        assert tracks.loc["u1", "termination_type"] == "ACTIVE_AT_END"

    def test_expiry_terminates_at_the_last_observed_scan(self, collection, scans):
        self._vanish(scans)
        self._third_scan(scans, _event("TERMINATION", "u1", source_scan="s1"))

        s1 = _rows(collection, "cells_by_scan", "scan_id = 's1'")
        events = _rows(collection, "cell_events", "source_cell_uid = 'u1'").set_index("event_type")
        tracks = _rows(collection, "cell_tracks").set_index("cell_uid")
        assert s1.loc[0, "is_terminated_after_here"] == 1
        assert events.loc["TERMINATION", "source_scan_id"] == "s1"
        assert events.loc["TERMINATION", "target_scan_id"] is None
        assert tracks.loc["u1", "termination_type"] == "TERMINATION"

    def test_an_expired_merge_source_ends_merged_into_its_survivor(self, collection, scans):
        self._vanish(scans)
        self._third_scan(scans, _event("TERMINATION", "u1", "u2", source_scan="s1"))

        tracks = _rows(collection, "cell_tracks").set_index("cell_uid")
        assert tracks.loc["u1", "termination_type"] == "MERGED"
        assert tracks.loc["u1", "terminated_into_cell_uid"] == "u2"

    def test_a_resumed_track_continues_its_age(self, collection, scans):
        self._vanish(scans)
        self._third_scan(
            scans,
            _event("RESUMED", "u1", "u1", source_scan="s1", target_label=1),
            tracked=((1, "u1"), (2, "u2")),
        )

        history = _rows(collection, "cells_by_scan", "cell_uid = 'u1'").sort_values("scan_time")
        events = _rows(collection, "cell_events", "source_cell_uid = 'u1'").set_index("event_type")
        assert history["age_seconds"].tolist() == [0.0, 600.0]
        assert events.loc["RESUMED", "source_scan_id"] == "s1"
        assert events.loc["RESUMED", "target_scan_id"] == "s3"


class TestStrictTrackedColumns:
    def test_missing_max_reflectivity_column_raises(self, scans):
        # A tracked_cells frame without max_reflectivity must raise loudly,
        # never write 0.0 into cell_tracks (silent corruption).
        bad_tracked = pd.DataFrame({"cell_label": [1], "cell_uid": ["u1"], "area": [10.0]})
        with pytest.raises(KeyError, match="max_reflectivity"):
            _write(scans, "s1", tracked=bad_tracked)

    def test_missing_area_column_raises(self, scans):
        bad_tracked = pd.DataFrame(
            {"cell_label": [1], "cell_uid": ["u1"], "max_reflectivity": [45.0]}
        )
        with pytest.raises(KeyError, match="area"):
            _write(scans, "s1", tracked=bad_tracked)

    def test_a_cell_without_a_stats_row_keeps_its_identity(self, collection, scans):
        _write(scans, "s1", stats=_stats([1], {"custom_metric": [3.5]}))
        # Second scan: two cells tracked, only cell 1 has a stats row.
        stats = _stats([1], {"custom_metric": [4.5]})
        _write(scans, "s2", offset_s=300, stats=stats, tracked=_tracked([(1, "u1"), (2, "u2")]))

        rows = _rows(collection, "cells_by_scan", "scan_id = 's2'").set_index("cell_uid")
        assert set(rows.index) == {"u1", "u2"}
        assert rows.loc["u1", "custom_metric"] == 4.5
        assert pd.isna(rows.loc["u2", "custom_metric"])
