# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The analysis node reports the scan's cell count: the cells it measured."""

import numpy as np
import pytest
import xarray as xr

from adapt.modules.analysis.node import AnalysisModule

pytestmark = pytest.mark.unit


def _projected(labels: np.ndarray) -> xr.Dataset:
    """A projected 2D scan with these cell labels, built as ``labeled_ds_with_extras`` is."""
    reflectivity = np.where(labels > 0, 40.0, 10.0).astype(np.float32)
    ds = xr.Dataset(
        {"reflectivity": (("y", "x"), reflectivity)},
        coords={"y": np.arange(labels.shape[0]), "x": np.arange(labels.shape[1])},
        attrs={"z_level_m": 2000},
    )
    ds["cell_labels"] = (("y", "x"), labels)
    ds["heading_x"] = (("y", "x"), np.ones_like(labels, dtype=np.float32))
    ds["heading_y"] = (("y", "x"), np.zeros_like(labels, dtype=np.float32))
    ds["differential_reflectivity"] = (("y", "x"), np.full_like(labels, 1.0, dtype=np.float32))
    ds["cell_projections"] = (("frame_offset", "y", "x"), np.stack([labels, labels], axis=0))
    ds = ds.assign_coords(frame_offset=[0, 1])
    return ds.assign_coords(time=np.datetime64("2024-01-01T00:00"))


def _run(labels: np.ndarray, config) -> dict:
    return AnalysisModule().run({"projected_ds": _projected(labels), "analysis_config": config})


def test_the_cell_count_is_the_number_of_cells_measured(make_analysis_config):
    labels = np.array(
        [
            [0, 0, 0, 0, 0, 0],
            [0, 1, 1, 0, 2, 0],
            [0, 1, 1, 0, 2, 0],
            [0, 0, 0, 0, 0, 0],
        ],
        dtype=np.int32,
    )

    result = _run(labels, make_analysis_config())

    assert result["scan_cell_count"] == 2
    assert result["scan_cell_count"] == len(result["cell_stats"])


def test_a_scan_without_cells_counts_none(make_analysis_config):
    labels = np.zeros((4, 4), dtype=np.int32)

    result = _run(labels, make_analysis_config())

    assert result["scan_cell_count"] == 0
