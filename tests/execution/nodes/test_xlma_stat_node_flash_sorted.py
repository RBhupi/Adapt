# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""XlmaStatModule.run() on flash-sorted NetCDF input and one scan's analysis dataset.

End-to-end through the node: synthetic flash-sorted file + the scan's minute
masks -> both table frames, attributed to the cell at the radar origin.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from adapt.execution.nodes.xlma_stat import XlmaStatModule
from adapt.modules.xlma_stat.config import XlmaStatConfig
from tests.helpers.analysis_nc import cell_block, make_analysis_ds
from tests.helpers.lma import write_flash_sorted_nc

pytestmark = pytest.mark.unit

pytest.importorskip("pyproj")  # geo projection of flash lon/lat onto the grid


def _analysis_ds(tracked_before: bool = True):
    """Scan 12:01:30 after 11:58:30; the cell sits on the radar through minute 12:00."""
    return make_analysis_ds(
        "2024-05-18T12:01:30",
        "2024-05-18T11:58:30",
        cell_labels=cell_block(col=9),
        cell_uids=["CELL_A"],
        minute_labels={
            "2024-05-18T11:59:00": cell_block(col=9),
            "2024-05-18T12:00:00": cell_block(col=9),
            "2024-05-18T12:01:00": cell_block(col=9),
        },
        registration_uids=["CELL_A"] if tracked_before else None,
    ).assign_attrs(radar_latitude=40.0, radar_longitude=-88.0)


def _run_context(input_dir, **overrides) -> dict:
    return {
        "xlma_stat_config": XlmaStatConfig(input_dir=str(input_dir)),
        "analysis_ds": _analysis_ds(),
        "scan_time": datetime(2024, 5, 18, 12, 1, 30, tzinfo=UTC),
        **overrides,
    }


def test_run_on_flash_sorted_netcdf_produces_both_tables(tmp_path):
    # 3 flashes 10 s apart from 12:00:00 — all inside the 12:00 minute mask
    write_flash_sorted_nc(tmp_path / "LYLOUT_240518_120000_3600_map.nc", "2024-05-18T12:00:00", 3)

    result = XlmaStatModule().run(_run_context(tmp_path))

    minutes = result["xlma_stat_minutes_rows"]
    scan = result["xlma_stat_scan_rows"]
    assert (minutes["cell_uid"] == "CELL_A").all()
    assert int(minutes["flash_count"].sum()) == 3
    assert (scan["cell_uid"] == "CELL_A").all()
    assert pd.Timestamp(scan.iloc[0]["scan_time"]) == pd.Timestamp("2024-05-18T12:01:30")
    assert scan.iloc[0]["n_minutes"] == 3


def test_non_netcdf_files_are_ignored(tmp_path):
    write_flash_sorted_nc(tmp_path / "a.nc", "2024-05-18T12:00:00", 2)
    (tmp_path / "LYLOUT_240518_120000.dat").write_text("ascii sources", encoding="utf-8")

    result = XlmaStatModule().run(_run_context(tmp_path))

    assert int(result["xlma_stat_minutes_rows"]["flash_count"].sum()) == 2


def test_a_scan_that_owns_no_minute_masks_has_no_rows(tmp_path):
    """The previous scan was never tracked: nothing to attribute lightning to yet."""
    write_flash_sorted_nc(tmp_path / "a.nc", "2024-05-18T12:00:00", 1)

    result = XlmaStatModule().run(
        _run_context(tmp_path, analysis_ds=_analysis_ds(tracked_before=False))
    )

    assert result["xlma_stat_minutes_rows"].empty
    assert result["xlma_stat_scan_rows"].empty


def test_files_arriving_between_scans_are_read(tmp_path):
    """A live run sees the flash files that land while it runs."""
    module = XlmaStatModule()
    write_flash_sorted_nc(tmp_path / "a.nc", "2024-05-18T12:00:00", 1)
    first = module.run(_run_context(tmp_path))
    write_flash_sorted_nc(tmp_path / "b.nc", "2024-05-18T12:00:30", 2)

    second = module.run(_run_context(tmp_path))

    assert int(first["xlma_stat_minutes_rows"]["flash_count"].sum()) == 1
    assert int(second["xlma_stat_minutes_rows"]["flash_count"].sum()) == 3
