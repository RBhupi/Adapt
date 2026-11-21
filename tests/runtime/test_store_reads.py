# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A declared store read gives the run's rows of a table or a view.

Real tables and the real ``cells_by_scan`` view, written by the real router
(``tests/helpers/tracking_store``); the reads are the ones target selection
declares, so these pin what a module is handed.
"""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from adapt.contracts import PersistenceMeta, ProductTableWrite, StoreRead
from adapt.execution.pipeline_builder import (
    _ensure_modules_registered,
    product_views,
    store_read_tables,
)
from adapt.modules.cell_volume_stats.node import CellVolumeStatsModule
from adapt.modules.xlma_stat.node import XlmaStatModule
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store_registry import RunStart
from adapt.runtime.store_inputs import ScanAt, read_rows
from tests.helpers.tracking_store import VIEWS, TrackedScans

pytestmark = pytest.mark.unit

T0 = datetime(2024, 5, 18, 12, 0, tzinfo=UTC)
CELLS = StoreRead(key="cells_so_far", source="cells_by_scan", up_to_scan=True)
TRACKS = StoreRead(key="tracks_so_far", source="cell_tracks", up_to_scan=False)
OTHER_RUN = "other-run"


@pytest.fixture(autouse=True)
def _catalog():
    _ensure_modules_registered()  # the reads name tables of catalog modules


def _scan(store_env, scan_id: str, minute: int, run_id: str | None = None) -> ScanAt:
    return ScanAt(
        store_env.collection, run_id or store_env.run_id, scan_id, T0 + timedelta(minutes=minute)
    )


def _write(scans: TrackedScans, scan_id: str, minute: int, uids: list[str], labels=None):
    """Store one tracked scan whose cells (labels 1.. unless given) go on as ``uids``."""
    labels = labels or list(range(1, len(uids) + 1))
    scans.write(
        scan_id,
        T0 + timedelta(minutes=minute),
        stats=pd.DataFrame({"cell_label": labels, "cell_area_sqkm": [10.0] * len(uids)}),
        tracked=pd.DataFrame(
            {
                "cell_label": labels,
                "cell_uid": uids,
                "area": [10.0] * len(uids),
                "max_reflectivity": [45.0] * len(uids),
            }
        ),
    )


def _begin_other_run(store_env) -> None:
    store_env.registry.begin_run(
        RunStart(
            run_id=OTHER_RUN,
            collection_id="TEST_RADAR",
            config_hash="other-hash",
            config_json="{}",
            pipeline_version="0",
            environment_json="{}",
        )
    )


def _three_scans(store_env) -> None:
    """Scans s1, s2, s3 at minutes 0, 5, 10: uid-1 throughout, uid-2 born at s3."""
    scans = TrackedScans(store_env.collection, store_env.run_id)
    _write(scans, "s1", 0, ["uid-1"])
    _write(scans, "s2", 5, ["uid-1"])
    _write(scans, "s3", 10, ["uid-1", "uid-2"])


def test_a_read_up_to_the_scan_gives_that_scan_and_the_ones_before_and_no_later_one(store_env):
    _three_scans(store_env)

    rows = read_rows(CELLS, _scan(store_env, "s2", 5))

    assert list(rows["scan_id"]) == ["s1", "s2"]


def test_a_read_of_the_rows_as_they_stand_gives_every_row_of_the_run(store_env):
    _three_scans(store_env)

    rows = read_rows(TRACKS, _scan(store_env, "s1", 0))

    assert sorted(rows["cell_uid"]) == ["uid-1", "uid-2"]


def test_rows_of_another_run_are_left_out(store_env):
    _three_scans(store_env)
    _begin_other_run(store_env)
    _write(TrackedScans(store_env.collection, OTHER_RUN), "o1", 0, ["uid-other"])

    cells = read_rows(CELLS, _scan(store_env, "s3", 10))
    tracks = read_rows(TRACKS, _scan(store_env, "s3", 10))

    assert set(cells["run_id"]) == {store_env.run_id}
    assert "uid-other" not in set(cells["cell_uid"]) | set(tracks["cell_uid"])


def test_rows_come_oldest_scan_first_then_in_key_order(store_env):
    """Scan ids that sort against time, and cells stored in reverse label order."""
    scans = TrackedScans(store_env.collection, store_env.run_id)
    _write(scans, "z-first", 0, ["uid-b", "uid-a"], labels=[2, 1])
    _write(scans, "a-second", 5, ["uid-b", "uid-a"], labels=[2, 1])

    rows = read_rows(CELLS, _scan(store_env, "a-second", 5))

    assert list(zip(rows["scan_id"], rows["cell_label"], strict=True)) == [
        ("z-first", 1),
        ("z-first", 2),
        ("a-second", 1),
        ("a-second", 2),
    ]


def test_before_any_scan_has_written_the_source_the_read_is_empty(store_env):
    for read in (CELLS, TRACKS):
        rows = read_rows(read, _scan(store_env, "s1", 0))

        assert rows.empty
        assert list(rows.columns) == []


def test_a_run_without_rows_reads_empty_where_another_run_made_the_table(store_env):
    _begin_other_run(store_env)
    _write(TrackedScans(store_env.collection, OTHER_RUN), "o1", 0, ["uid-other"])

    for read in (CELLS, TRACKS):
        assert read_rows(read, _scan(store_env, "s1", 5)).empty


def _write_same_key_tables(store_env, scan_id: str, minute: int) -> None:
    """Rows of the scan's cell 1 in the other tables with the view's key."""
    router = StoreOutputRouter(store_env.collection, VIEWS)
    meta = PersistenceMeta(
        scan_time=T0 + timedelta(minutes=minute),
        scan_id=scan_id,
        run_id=store_env.run_id,
        source_file=scan_id,
        collection_id=store_env.collection.collection_id,
    )
    tables = [
        SimpleNamespace(name=module.name, persistence=(spec,), column=column)
        for module, column in (
            (CellVolumeStatsModule, "probe_volume"),
            (XlmaStatModule, "probe_flashes"),
        )
        for spec in module.persistence
        if isinstance(spec, ProductTableWrite) and "cell_label" in spec.primary_key
    ]
    result = {
        table.persistence[0].key: pd.DataFrame({"cell_label": [1], table.column: [2.0]})
        for table in tables
    }
    router.persist(tables, result, meta)


def test_a_view_read_carries_the_columns_of_every_table_with_its_key(store_env):
    scans = TrackedScans(store_env.collection, store_env.run_id)
    _write(scans, "s1", 0, ["uid-1"])
    _write_same_key_tables(store_env, "s1", 0)

    rows = read_rows(CELLS, _scan(store_env, "s1", 0))

    assert rows.loc[0, "cell_uid"] == "uid-1"  # cell_identity
    assert rows.loc[0, "cell_area_sqkm"] == 10.0  # cell_stats
    assert rows.loc[0, "probe_volume"] == 2.0  # cell_volume_stats
    assert rows.loc[0, "probe_flashes"] == 2.0  # xlma_stat_scan


def test_the_tables_named_for_a_view_are_the_ones_the_store_joins(store_env):
    """The "same primary key" rule lives in the store and in the graph: they agree."""
    scans = TrackedScans(store_env.collection, store_env.run_id)
    _write(scans, "s1", 0, ["uid-1"])
    _write_same_key_tables(store_env, "s1", 0)
    (view,) = (v for v in product_views() if v.name == "cells_by_scan")

    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?", (view.name,)
        ).fetchone()
    finally:
        conn.close()

    joined = set(re.findall(r"\b(?:FROM|JOIN)\s+(\w+)", sql)) - set(view.reads)
    named = {table.table for table in store_read_tables(view.name)} - set(view.reads)
    assert joined == named
