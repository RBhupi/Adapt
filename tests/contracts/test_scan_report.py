# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Contracts of what a module reports about a scan: the radar's location and the cell count."""

import math

import pytest

from adapt.contracts import (
    ContractViolation,
    RadarLocation,
    check_radar_location,
    check_scan_cell_count,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(34.93, -86.08), (90.0, -180.0), (-90.0, 360.0), (0.0, 0.0)],
)
def test_a_radar_location_on_the_globe_is_accepted(latitude, longitude):
    check_radar_location(RadarLocation(latitude=latitude, longitude=longitude))


def test_anything_but_a_radar_location_is_refused():
    with pytest.raises(ContractViolation):
        check_radar_location((34.93, -86.08))


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(90.5, 0.0), (math.nan, 0.0), (0.0, -181.0), (0.0, 361.0), (0.0, math.inf)],
)
def test_a_radar_location_off_the_globe_or_not_finite_is_refused(latitude, longitude):
    with pytest.raises(ContractViolation):
        check_radar_location(RadarLocation(latitude=latitude, longitude=longitude))


@pytest.mark.parametrize("count", [0, 36])
def test_a_cell_count_of_zero_or_more_is_accepted(count):
    check_scan_cell_count(count)


@pytest.mark.parametrize("count", [-1, 3.0, True, None])
def test_a_cell_count_that_is_not_a_whole_number_of_cells_is_refused(count):
    with pytest.raises(ContractViolation):
        check_scan_cell_count(count)
