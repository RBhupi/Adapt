# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Canonical column/variable naming — the single authoritative convention."""

import numpy as np
import pytest
import xarray as xr

from adapt.contracts import (
    CELL_LABELS_VAR,
    TIME_COORD,
    X_COORD,
    Y_COORD,
    Z_COORD,
    check_grid_ds_2d,
    stat_column,
)

pytestmark = pytest.mark.unit


def test_stat_column_mints_the_radar_prefixed_convention():
    assert stat_column("reflectivity", "max") == "radar_reflectivity_max"
    assert stat_column("reflectivity", "mean") == "radar_reflectivity_mean"


def test_stat_column_is_field_generic():
    # The radar_ prefix is a cell-stat namespace, not a physics claim —
    # a pressure-tracked run mints radar_pressure_* columns.
    assert stat_column("pressure", "min") == "radar_pressure_min"


def test_cell_labels_constant():
    assert CELL_LABELS_VAR == "cell_labels"


def test_grid_coordinate_names_are_fixed():
    # Stored NetCDF grids carry these names; changing one orphans every store.
    assert (X_COORD, Y_COORD, Z_COORD, TIME_COORD) == ("x", "y", "z", "time")


def test_a_grid_on_the_canonical_coordinates_satisfies_the_grid_contract():
    grid = xr.Dataset(
        {"reflectivity": ((Y_COORD, X_COORD), np.zeros((2, 2)))},
        coords={Y_COORD: [0.0, 1.0], X_COORD: [0.0, 1.0]},
    )

    check_grid_ds_2d(grid)
