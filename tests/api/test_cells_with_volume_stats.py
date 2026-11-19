# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A run's cells carry what other modules wrote for them: volume statistics and
lightning, whenever those were written."""

import pandas as pd
import pytest

from adapt.api.store_client import StoreClient
from adapt.contracts.persistence import PersistenceMeta
from adapt.execution.nodes.xlma_stat import XlmaStatModule
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store
from tests.helpers.radar_store import (
    COLLECTION,
    RUN,
    SCAN_TIMES,
    add_volume_stats,
    build_store,
)
from tests.helpers.tracking_store import VIEWS

pytestmark = pytest.mark.unit


def _first_cell(built) -> pd.DataFrame:
    """The rows of cell 1 over the run's three scans, in time order."""
    with StoreClient(built.root) as client:
        cells = client.cells(RUN, COLLECTION)
    return cells[cells["cell_label"] == 1].sort_values("scan_time")


def test_volume_statistics_of_a_scan_are_in_its_cells(tmp_path):
    built = build_store(tmp_path, volume_stats=True)

    cell = _first_cell(built)

    assert list(cell["cell_top_m"]) == [9500.0, 9600.0, 9700.0]
    assert list(cell["cell_uid"]) == ["uid-a"] * 3


def test_cells_have_no_volume_columns_when_no_module_wrote_them(tmp_path):
    built = build_store(tmp_path, volume_stats=False)

    assert "cell_top_m" not in _first_cell(built).columns


def test_volume_statistics_written_late_are_in_the_cells(tmp_path):
    built = build_store(tmp_path, volume_stats=False)

    add_volume_stats(built, RUN, 1)

    tops = list(_first_cell(built)["cell_top_m"])
    assert pd.isna(tops[0])
    assert tops[1] == 9600.0
    assert pd.isna(tops[2])


def test_lightning_of_a_scans_cell_is_in_its_cells(tmp_path):
    """xlma_stat run over the stored run: its scan rows have the cells' key."""
    built = build_store(tmp_path, volume_stats=True)
    store = Store.open(built.root)
    meta = PersistenceMeta(
        scan_time=SCAN_TIMES[1],
        scan_id=built.scan_ids[1],
        run_id=RUN,
        source_file="KTST20260601_120537_V06",
        collection_id=COLLECTION,
    )
    lightning = pd.DataFrame({"cell_label": [1], "xlma_flash_count": [7], "xlma_n_minutes": [5]})

    StoreOutputRouter(store.collection(COLLECTION), VIEWS).persist(
        [XlmaStatModule], {"xlma_stat_scan_rows": lightning}, meta
    )
    store.close()

    cell = _first_cell(built)
    counts = list(cell["xlma_flash_count"])
    assert pd.isna(counts[0]) and counts[1] == 7 and pd.isna(counts[2])
    assert list(cell["cell_top_m"]) == [9500.0, 9600.0, 9700.0]
