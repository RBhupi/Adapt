"""Test RadarCellSegmenter error handling and edge cases."""

import numpy as np
import pytest
import xarray as xr

from adapt.modules.detection.module import RadarCellSegmenter

pytestmark = pytest.mark.unit


def test_missing_reflectivity_var(detection_module_config):
    """Segmenter fails gracefully when reflectivity variable missing."""
    ds = xr.Dataset(
        {"wrong_var": (("y", "x"), np.ones((3, 3)))}
    )

    seg = RadarCellSegmenter(detection_module_config)
    with pytest.raises(KeyError):
        seg.segment(ds)


def test_non_2d_data_fails(detection_module_config):
    """Segmenter rejects 3D data (must be 2D slice)."""
    ds = xr.Dataset(
        {"reflectivity": (("z", "y", "x"), np.ones((2, 3, 3)))}
    )

    seg = RadarCellSegmenter(detection_module_config)
    with pytest.raises(Exception):  # noqa: B017 — ValueError or similar from segmenter
        seg.segment(ds)

