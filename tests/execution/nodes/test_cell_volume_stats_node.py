# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Node-level tests for CellVolumeStatsModule — covers the three review fixes:

A) configure adds the run's tracked field to the module's own options.
B) run() on a scan without cells returns an empty frame that PASSES the output contract.
C) a datetime scan_time round-trips through the writer to the canonical join key.
"""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

from adapt.configuration.schemas.errors import ConfigError  # noqa: E402
from adapt.contracts import check_cell_volume_stats  # noqa: E402
from adapt.execution.nodes.cell_volume_stats import CellVolumeStatsModule  # noqa: E402
from adapt.modules.cell_volume_stats.config import (  # noqa: E402
    CellVolumeStatsConfig,
    CellVolumeStatsOptions,
)
from adapt.utils.time import to_scan_iso  # noqa: E402
from tests.helpers.shared_config import SHARED  # noqa: E402


def _default_config() -> CellVolumeStatsConfig:
    return CellVolumeStatsModule.configure(CellVolumeStatsOptions(), SHARED)


@pytest.fixture(autouse=True, scope="module")
def _restore_default_registration():
    """cell_volume_stats is a default-pipeline module; ensure it stays registered
    for other test files even if a test here unregisters it."""
    yield
    from adapt.execution.module_registry import registry

    if "cell_volume_stats" not in registry:
        registry.register(CellVolumeStatsModule)


def _grid_3d() -> xr.Dataset:
    nz, ny, nx = 6, 4, 4
    dbz = np.full((nz, ny, nx), np.nan)
    dbz[:3, 1:3, 1:3] = 45.0  # a compact cell footprint at (y,x) 1:3
    return xr.Dataset(
        {"reflectivity": (["z", "y", "x"], dbz)},
        coords={
            "z": np.arange(nz) * 500.0,
            "y": np.arange(ny) * 1000.0,
            "x": np.arange(nx) * 1000.0,
        },
    )


def _segmented() -> xr.Dataset:
    labels = np.zeros((4, 4), dtype=np.int32)
    labels[1:3, 1:3] = 1
    return xr.Dataset(
        {"cell_labels": (["y", "x"], labels)},
        coords={"y": np.arange(4) * 1000.0, "x": np.arange(4) * 1000.0},
    )


class TestConfigureFixA:
    def test_configure_adds_the_tracked_field_to_the_modules_options(self):
        cfg = CellVolumeStatsModule.configure(
            CellVolumeStatsOptions(gap_tolerance_m=250.0), replace(SHARED, tracking_field="dbz")
        )
        assert isinstance(cfg, CellVolumeStatsConfig)
        assert cfg.reflectivity_var == "dbz"
        assert cfg.gap_tolerance_m == 250.0
        # polarimetric var names are the module's own options, not shared values
        assert cfg.zdr_var == "differential_reflectivity"

    def test_a_mistyped_param_is_refused_when_the_run_is_configured(self, make_config):
        with pytest.raises(ConfigError, match=r"params\.cell_volume_stats\.gap_tolerance — unrec"):
            make_config(params={"cell_volume_stats": {"gap_tolerance": 250.0}})


class TestEmptyPathFixB:
    def test_a_scan_without_cells_returns_contract_safe_empty(self):
        mod = CellVolumeStatsModule()
        ctx = {
            "cell_volume_stats_config": _default_config(),
            "grid_ds": _grid_3d(),
            "segmented_ds": _segmented() * 0,
            "run_id": "R1",
            "scan_time": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        }
        out = mod.run(ctx)
        df = out["cell_volume_stats_rows"]
        assert df.empty
        check_cell_volume_stats(df)  # must NOT raise


class TestRunAndWriteFixC:
    def test_datetime_scan_time_round_trips_to_join_key(self, tmp_path):
        mod = CellVolumeStatsModule()
        scan_time = datetime(2024, 1, 1, 12, 0, 0)  # naive, like NEXRAD strptime
        ctx = {
            "cell_volume_stats_config": _default_config(),
            "grid_ds": _grid_3d(),
            "segmented_ds": _segmented(),
            "run_id": "R1",
            "scan_time": scan_time,
        }
        out = mod.run(ctx)
        df = out["cell_volume_stats_rows"]
        assert not df.empty
        check_cell_volume_stats(df)

        from adapt.contracts.persistence import PersistenceMeta
        from adapt.persistence.products import SchemaLedger, TableWriter
        from adapt.persistence.store import Store, init_store

        root = init_store(tmp_path / "store")
        store = Store.open(root)
        coll = store.collection("KTST")
        writer = TableWriter(
            SchemaLedger(coll.products_path, coll.catalog, ()),
            CellVolumeStatsModule.persistence[0],
            owner_module="cell_volume_stats",
        )
        meta = PersistenceMeta(
            scan_time=scan_time.replace(tzinfo=UTC),
            scan_id="sid-vol-1",
            run_id="R1",
            source_file="",
            collection_id="KTST",
        )
        writer.write(df, meta)
        conn = sqlite3.connect(str(coll.products_path))
        try:
            sid, iso, unix = conn.execute(
                "SELECT scan_id, scan_time, scan_time_unix FROM cell_volume_stats LIMIT 1"
            ).fetchone()
            vol = conn.execute("SELECT cell_volume_km3 FROM cell_volume_stats").fetchone()[0]
        finally:
            conn.close()
        store.close()
        assert sid == "sid-vol-1"  # the join key, stamped by the writer
        assert iso == to_scan_iso(scan_time)
        assert unix == int(scan_time.replace(tzinfo=UTC).timestamp())
        assert vol > 0  # the cell has volume


class TestNodeDeclarations:
    def test_it_reads_the_grid_and_the_cells_but_not_their_tracks(self):
        assert {"grid_ds", "segmented_ds"} <= set(CellVolumeStatsModule.inputs)
        assert "tracked_cells" not in CellVolumeStatsModule.inputs
        assert CellVolumeStatsModule.persistence[0].table == "cell_volume_stats"
        assert CellVolumeStatsModule.persistence[0].primary_key == (
            "run_id",
            "scan_id",
            "cell_label",
        )

    def test_registered(self):
        from adapt.execution.module_registry import registry
        from adapt.execution.pipeline_builder import _ensure_modules_registered

        _ensure_modules_registered()
        assert "cell_volume_stats" in registry
        # cell_volume_stats is a default module — leave it registered.
