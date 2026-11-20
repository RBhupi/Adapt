# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""ProjectionModule projects over the prior scans' frames plus the current one.

``prior_scans`` holds previous completed scan contexts only; the current scan's
labels arrive as ``segmented_ds``.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from adapt.contracts import check_prior_scans
from adapt.modules.projection.node import ProjectionModule

pytestmark = pytest.mark.unit


def _make_labeled_ds(t: str) -> xr.Dataset:
    data = np.array([[0, 40, 40, 0], [0, 40, 40, 0], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.float32)
    labels = np.array([[0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.int32)
    ds = xr.Dataset(
        {"reflectivity": (("y", "x"), data), "cell_labels": (("y", "x"), labels)},
        coords={"y": range(4), "x": range(4)},
    )
    return ds.assign_coords(time=np.datetime64(t))


def _make_scan_ctx(t: str) -> dict:
    """Build a prior-scan entry as the processor would produce it."""
    return {
        "segmented_ds": _make_labeled_ds(t),
        "scan_time": datetime.fromisoformat(t).replace(tzinfo=UTC),
        "nexrad_file": f"KLOT_{t.replace(':', '').replace('-', '')}_V06",
    }


def test_projects_from_one_prior_scan_and_the_current_labels(make_projection_config):
    ctx = {
        "prior_scans": [_make_scan_ctx("2024-01-01T00:00:00")],
        "segmented_ds": _make_labeled_ds("2024-01-01T00:05:00"),
        "projection_config": make_projection_config(),
    }

    result = ProjectionModule().run(ctx)

    assert "projected_ds" in result


def test_declares_prior_scans_as_a_contracted_input():
    assert "prior_scans" in ProjectionModule.inputs
    assert ProjectionModule.input_contracts["prior_scans"] is check_prior_scans


def test_needs_one_prior_scan():
    assert ProjectionModule.required_history == 2


def test_raises_without_prior_scans(make_projection_config):
    ctx = {
        "segmented_ds": _make_labeled_ds("2024-01-01T00:05:00"),
        "projection_config": make_projection_config(),
        # prior_scans intentionally omitted
    }
    with pytest.raises(KeyError):
        ProjectionModule().run(ctx)
