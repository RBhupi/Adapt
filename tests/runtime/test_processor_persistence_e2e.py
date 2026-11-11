# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""End-to-end persistence smoke: process_file -> OutputRouter -> repository.

Science modules are faked; persistence is real. After two scans the catalog
must contain the analysis NetCDF (with the uid LUT), the analysis2d Parquet
rows, and the tracking tables — all routed purely from module-declared specs,
and all discoverable through the generic read API.
"""

import queue
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from adapt.modules.tracking.tables import Lifecycle
from adapt.runtime.processor import RadarProcessor
from tests.helpers.fake_graph import fake_graph
from tests.helpers.queue_msg import msg as _msg
from tests.helpers.tracking_store import going_on, no_adjacency, tracking_outputs

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

_T1 = datetime(2024, 5, 18, 12, 0, 0, tzinfo=UTC)
_T2 = datetime(2024, 5, 18, 12, 5, 0, tzinfo=UTC)


def _fake_ds():
    labels = np.zeros((4, 4), dtype=int)
    labels[0, 0] = 1
    return xr.Dataset(
        {
            "reflectivity": (("y", "x"), np.ones((4, 4))),
            "cell_labels": (("y", "x"), labels),
        },
        coords={"x": np.arange(4), "y": np.arange(4)},
        attrs={"z_level_m": 2000},
    )


def test_process_file_persists_all_declared_outputs(
    monkeypatch, pipeline_config, store_env, tmp_path
):
    proc = RadarProcessor(
        queue.Queue(),
        pipeline_config,
        collection=store_env.collection,
        registry=store_env.registry,
        run_id=store_env.run_id,
        history=store_env.history,
    )

    scan_times = [_T1, _T2]

    def _fake_single(context):
        return {
            "grid_ds": _fake_ds(),
            "grid_ds_2d": _fake_ds(),
            "segmented_ds": _fake_ds(),
            "scan_time": scan_times.pop(0),
            "num_cells": 1,
        }

    tracked = pd.DataFrame(
        {"cell_label": [1], "cell_uid": ["uid-1"], "area": [10.0], "max_reflectivity": [45.0]}
    )
    tracking_tables, _ = tracking_outputs(
        tracked,
        going_on(tracked),
        no_adjacency(),
        scan_id="second-scan",
        scan_time=_T2,
        lifecycle=Lifecycle(),
    )
    fake_multi_result = {
        "projected_ds": _fake_ds(),
        "analysis_ds": _fake_ds(),
        "cell_stats": pd.DataFrame({"cell_label": [1], "area_sqkm": [25.0]}),
        "cell_adjacency": pd.DataFrame(
            {"cell_label_a": [], "cell_label_b": [], "touching_boundary_pixels": []}
        ),
        **tracking_tables,
        "scan_time": _T2,
    }

    fake_graph(proc, monkeypatch, _fake_single, lambda ctx: fake_multi_result)

    first = _msg(store_env, tmp_path, "TEST_20240518_120000", scan_time=_T1)
    second = _msg(store_env, tmp_path, "TEST_20240518_120500", scan_time=_T2)
    assert proc.process_file(first) is True
    assert proc.process_file(second) is True

    # Analysis NetCDF artifact written from tracking's declared analysis_ds spec.
    catalog = store_env.collection.catalog
    nc_items = catalog.list_artifacts(run_id=store_env.run_id, artifact_type="segmentation2d")
    assert len(nc_items) == 1
    ds = xr.open_dataset(store_env.collection.objects_dir / nc_items[0]["object_name"])
    assert ds.attrs["radar"] == "TEST_RADAR"
    assert ds.attrs["scan_id"] == second["scan_id"]
    ds.close()
    assert catalog.parents_of(nc_items[0]["artifact_id"]) == [second["artifact_id"]]

    # Table rows from analysis's declared ProductTableWrite specs.
    import sqlite3

    conn = sqlite3.connect(store_env.collection.products_path)
    try:
        stats_rows = conn.execute("SELECT COUNT(*) FROM cell_stats").fetchone()[0]
    finally:
        conn.close()
    assert stats_rows >= 1

    # Tracking tables from tracking's declared specs, read through the view.
    conn = sqlite3.connect(store_env.collection.products_path)
    try:
        uids = conn.execute(
            "SELECT cell_uid FROM cells_by_scan WHERE run_id = ? AND scan_id = ?",
            (store_env.run_id, second["scan_id"]),
        ).fetchall()
    finally:
        conn.close()
    assert uids == [("uid-1",)]

    # The second scan is complete: its pass ended with no module failing.
    assert catalog.scan_is_complete(store_env.run_id, second["scan_id"])
