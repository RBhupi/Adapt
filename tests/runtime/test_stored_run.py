# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Running a module over a run the store already has (``source: stored_run``).

The processor walks the stored run's scans again; a listed module reads from
the store what the modules not listed stored before. Here: xlma_stat over a
small stored run, fully synthetic. The key fixture is a *moving* cell: the
flash sits at the cell's projected mid-gap position, where both real scan masks
are out of attribution range — only the minute-resolution geometry attributes it.
"""

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from adapt.contracts.persistence import PersistenceMeta
from adapt.execution.nodes.tracking import TrackingModule
from adapt.persistence.output_router import StoreOutputRouter
from tests.helpers.analysis_nc import cell_block, make_analysis_ds
from tests.helpers.lma import write_flash_sorted_nc
from tests.helpers.pipeline_files import MODULES
from tests.helpers.queue_msg import msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

pytest.importorskip("pyproj")

LAT0, LON0 = 40.0, -88.0
_T = [datetime(2024, 5, 18, 19, minute, tzinfo=UTC) for minute in (0, 3, 6)]
# The analysis NetCDF, stored as tracking stores it.
_TRACKING_NC = SimpleNamespace(name="tracking", persistence=TrackingModule.persistence[:1])


def _moving_cell(scan: int):
    """Scans 19:00/19:03/19:06; the cell crosses the grid centre at minute 19:05.

    At 19:03 the cell is far west (cols 5-7), at 19:06 far east (cols 11-13);
    only the advected 19:05 mask (cols 9-11) covers the radar origin.
    """
    if scan == 1:
        ds = make_analysis_ds(
            "2024-05-18T19:03:00",
            "2024-05-18T19:00:00",
            cell_labels=cell_block(col=5),
            cell_uids=["uid-A"],
            minute_labels={
                "2024-05-18T19:01:00": cell_block(col=3),
                "2024-05-18T19:02:00": cell_block(col=4),
                "2024-05-18T19:03:00": cell_block(col=5),
            },
            registration_uids=None,  # first pair: previous scan never tracked
        )
    else:
        ds = make_analysis_ds(
            "2024-05-18T19:06:00",
            "2024-05-18T19:03:00",
            cell_labels=cell_block(col=11),
            cell_uids=["uid-A"],
            minute_labels={
                "2024-05-18T19:04:00": cell_block(col=7),
                "2024-05-18T19:05:00": cell_block(col=9),
                "2024-05-18T19:06:00": cell_block(col=11),
            },
            registration_uids=["uid-A"],
        )
    return ds.assign_attrs(radar_latitude=LAT0, radar_longitude=LON0)


@pytest.fixture
def stored_run(store_env, tmp_path):
    """Three scans acquired by the run; the last two have their analysis NetCDF."""
    router = StoreOutputRouter(store_env.collection)
    messages = [msg(store_env, tmp_path, f"raw-{i}", scan_time=t) for i, t in enumerate(_T)]
    for scan in (1, 2):
        meta = PersistenceMeta(
            scan_time=_T[scan],
            scan_id=messages[scan]["scan_id"],
            run_id=store_env.run_id,
            source_file=f"raw-{scan}",
            collection_id="TEST_RADAR",
        )
        router.persist([_TRACKING_NC], {"analysis_ds": _moving_cell(scan)}, meta)
    return messages


def _run_xlma_over_it(make_processor, make_config, store_env, tmp_path) -> list[bool]:
    lma_dir = tmp_path / "lma"
    lma_dir.mkdir(exist_ok=True)
    # two flashes in minute 19:05 at the radar origin — the cell's mid-gap position
    write_flash_sorted_nc(lma_dir / "LYLOUT_240518_190000_3600_map.nc", "2024-05-18T19:05:10", 2)
    (lma_dir / "LYLOUT_240518_190000.dat").write_text("raw ascii — ignored", encoding="utf-8")
    # A pass over a stored run: that run's config, running the pass's modules.
    config = make_config(
        modules=[*MODULES, "xlma_stat"], params={"xlma_stat": {"input_dir": str(lma_dir)}}
    ).model_copy(update={"source": "stored_run", "modules": ["xlma_stat"]})
    proc = make_processor(config=config)
    return [proc.process_file(message) for message in store_env.acquirer.stored_scans()]


def _rows(store_env, sql):
    conn = sqlite3.connect(store_env.collection.products_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_a_module_reads_what_it_needs_back_from_the_store(
    stored_run, make_processor, make_config, store_env, tmp_path
):
    assert _run_xlma_over_it(make_processor, make_config, store_env, tmp_path) == [True] * 3

    minutes = _rows(store_env, "SELECT * FROM xlma_stat_minutes")
    scans = _rows(store_env, "SELECT * FROM xlma_stat_scan")
    # The flash at 19:05 attributes to the moving cell via its minute mask —
    # both real scan masks are out of range at that moment.
    assert len(minutes) == 1
    m = minutes[0]
    assert m["cell_uid"] == "uid-A"
    assert m["time"] == "2024-05-18T19:05:00Z"
    assert m["source_scan_time"] == "2024-05-18T19:03:00Z"
    assert m["target_scan_time"] == "2024-05-18T19:06:00Z"
    assert m["interpolation_fraction"] == pytest.approx(2 / 3)
    assert (m["flash_count"], m["lightning_source_count"]) == (2, 4)

    assert len(scans) == 1
    s = scans[0]
    assert s["cell_uid"] == "uid-A"
    assert s["scan_time"] == "2024-05-18T19:06:00Z"
    assert s["scan_time_unix"] is not None
    assert s["flash_count"] == 2  # equals the sum of its member minute rows
    assert s["n_minutes"] == 3  # 19:04, 19:05 advected + 19:06 real mask
    assert s["n_lightning_minutes"] == 1
    assert all(r["run_id"] == store_env.run_id for r in [*minutes, *scans])


def test_a_scan_without_the_stored_input_skips_the_module_and_says_why(
    stored_run, make_processor, make_config, store_env, tmp_path
):
    _run_xlma_over_it(make_processor, make_config, store_env, tmp_path)

    first = store_env.collection.catalog.scan_modules(store_env.run_id, stored_run[0]["scan_id"])
    third = store_env.collection.catalog.scan_modules(store_env.run_id, stored_run[2]["scan_id"])
    assert first["xlma_stat"]["status"] == "skipped"
    assert first["xlma_stat"]["reason"] == "input 'analysis_ds' is not stored for this scan"
    assert third["xlma_stat"]["status"] == "done"


def test_running_over_the_stored_run_again_changes_nothing(
    stored_run, make_processor, make_config, store_env, tmp_path
):
    _run_xlma_over_it(make_processor, make_config, store_env, tmp_path)
    _run_xlma_over_it(make_processor, make_config, store_env, tmp_path)

    assert len(_rows(store_env, "SELECT * FROM xlma_stat_minutes")) == 1
    assert len(_rows(store_env, "SELECT * FROM xlma_stat_scan")) == 1


def test_a_live_run_cannot_take_a_modules_output_from_the_store(
    make_processor, make_config, tmp_path
):
    """A new scan has nothing stored yet: the producer must be listed."""
    write_flash_sorted_nc(tmp_path / "LYLOUT_240518_190000_3600_map.nc", "2024-05-18T19:05:10", 1)
    # A config no pipeline file gives (run_config refuses it): the processor refuses it too.
    config = make_config(
        modules=[*MODULES, "xlma_stat"], params={"xlma_stat": {"input_dir": str(tmp_path)}}
    ).model_copy(update={"modules": ["ingest", "xlma_stat"]})
    with pytest.raises(ValueError, match="'tracking' produces — list 'tracking' too"):
        make_processor(config=config)
