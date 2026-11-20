# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Geographic extent of a target footprint — hand-computed boxes."""

import math

import numpy as np
import pytest

from adapt.modules.arm_radar_comm.extent import VerticalBounds, geographic_extent

pytestmark = pytest.mark.unit

_VERTICAL = VerticalBounds(bottom_m=1500.0, top_m=11500.0, source="config")
_KM_PER_DEG_LAT = 111.19492664455873  # 6371 * pi / 180


def test_near_target_box_with_zero_margin_spans_the_points():
    lats = np.array([35.0, 35.5, 35.25])
    lons = np.array([-97.5, -97.0, -97.25])

    extent = geographic_extent(lats, lons, 0.0, _VERTICAL)

    assert extent.lat_center_deg == pytest.approx(35.25)
    assert extent.lon_center_deg == pytest.approx(-97.25)
    assert extent.lat_width_deg == pytest.approx(0.5)
    assert extent.lon_width_deg == pytest.approx(0.5)


def test_far_target_box_with_zero_margin_spans_the_points():
    lats = np.array([-12.0, -10.5])
    lons = np.array([131.0, 133.0])

    extent = geographic_extent(lats, lons, 0.0, _VERTICAL)

    assert extent.lat_center_deg == pytest.approx(-11.25)
    assert extent.lon_center_deg == pytest.approx(132.0)
    assert extent.lat_width_deg == pytest.approx(1.5)
    assert extent.lon_width_deg == pytest.approx(2.0)


def test_one_degree_margin_widens_box_by_two_degrees_lat_and_secant_lon():
    lats = np.array([35.0, 35.5])
    lons = np.array([-97.5, -97.0])

    extent = geographic_extent(lats, lons, _KM_PER_DEG_LAT, _VERTICAL)

    # dlat = 1.0 deg; phi_edge = max(|35.0 - 1.0|, |35.5 + 1.0|) = 36.5 deg,
    # so each side grows by 1/cos(36.5 deg) ~= 1.24400 deg of longitude.
    assert extent.lat_center_deg == pytest.approx(35.25)
    assert extent.lon_center_deg == pytest.approx(-97.25)
    assert extent.lat_width_deg == pytest.approx(0.5 + 2.0)
    assert extent.lon_width_deg == pytest.approx(0.5 + 2.0 / math.cos(math.radians(36.5)))
    assert extent.lon_width_deg == pytest.approx(2.98800, abs=1e-5)


def _edges(extent):
    """(south, north, west, east) edges of a box that does not cross 180 deg."""
    return (
        extent.lat_center_deg - extent.lat_width_deg / 2,
        extent.lat_center_deg + extent.lat_width_deg / 2,
        extent.lon_center_deg - extent.lon_width_deg / 2,
        extent.lon_center_deg + extent.lon_width_deg / 2,
    )


def test_buffered_box_strictly_contains_unbuffered_box():
    lats = np.array([36.6, 36.9, 36.7])
    lons = np.array([-97.6, -97.3, -97.4])

    south0, north0, west0, east0 = _edges(geographic_extent(lats, lons, 0.0, _VERTICAL))
    south, north, west, east = _edges(geographic_extent(lats, lons, 5.0, _VERTICAL))

    assert south < south0
    assert north > north0
    assert west < west0
    assert east > east0


def test_every_input_point_lies_inside_the_box():
    lats = np.array([[36.61, 36.63, 36.95], [36.70, 36.58, 36.88]])
    lons = np.array([[-97.62, -97.31, -97.45], [-97.50, -97.29, -97.66]])

    south, north, west, east = _edges(geographic_extent(lats, lons, 2.0, _VERTICAL))

    assert np.all((lats > south) & (lats < north))
    assert np.all((lons > west) & (lons < east))


def test_box_across_antimeridian_is_narrow_and_centre_normalised():
    lats = np.array([-17.0, -16.0])
    lons = np.array([179.5, -179.5])

    extent = geographic_extent(lats, lons, 0.0, _VERTICAL)

    assert extent.lon_center_deg == -180.0
    assert extent.lon_width_deg == pytest.approx(1.0)


def test_box_straddling_prime_meridian_is_centred_on_zero():
    lats = np.array([51.4, 51.6])
    lons = np.array([0.25, -0.25])

    extent = geographic_extent(lats, lons, 0.0, _VERTICAL)

    assert extent.lon_center_deg == pytest.approx(0.0)
    assert extent.lon_width_deg == pytest.approx(0.5)


def test_altitudes_are_copied_from_vertical_bounds():
    vertical = VerticalBounds(bottom_m=2314.0, top_m=13314.0, source="cell_volume_stats")

    extent = geographic_extent(np.array([36.6]), np.array([-97.5]), 3.0, vertical)

    assert extent.alt_bottom_m == 2314.0
    assert extent.alt_top_m == 13314.0


def test_empty_footprint_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        geographic_extent(np.array([]), np.array([]), 0.0, _VERTICAL)


def test_lat_lon_shape_mismatch_is_rejected():
    with pytest.raises(ValueError, match="shape"):
        geographic_extent(np.array([36.6, 36.7]), np.array([-97.5]), 0.0, _VERTICAL)


@pytest.mark.parametrize(
    ("lats", "lons"),
    [([36.6, np.nan], [-97.5, -97.4]), ([36.6, 36.7], [-97.5, np.inf])],
    ids=["nan-lat", "inf-lon"],
)
def test_non_finite_coordinate_is_rejected(lats, lons):
    with pytest.raises(ValueError, match="finite"):
        geographic_extent(np.array(lats), np.array(lons), 0.0, _VERTICAL)


@pytest.mark.parametrize("margin_km", [-0.5, np.inf, np.nan], ids=["negative", "inf", "nan"])
def test_negative_or_non_finite_margin_is_rejected(margin_km):
    with pytest.raises(ValueError, match="margin_km"):
        geographic_extent(np.array([36.6]), np.array([-97.5]), margin_km, _VERTICAL)


@pytest.mark.parametrize(
    ("bottom_m", "top_m"),
    [(5000.0, 5000.0), (6000.0, 5000.0), (1500.0, np.nan)],
    ids=["equal", "inverted", "nan-top"],
)
def test_vertical_top_not_above_finite_bottom_is_rejected(bottom_m, top_m):
    vertical = VerticalBounds(bottom_m=bottom_m, top_m=top_m, source="config")

    with pytest.raises(ValueError, match="top_m"):
        geographic_extent(np.array([36.6]), np.array([-97.5]), 0.0, vertical)


@pytest.mark.parametrize("lat", [89.0, 89.5, -89.5], ids=["edge-at-90N", "past-90N", "past-90S"])
def test_box_reaching_a_pole_is_rejected(lat):
    # A one-degree margin puts the poleward edge at |lat| + 1 >= 90 deg.
    with pytest.raises(ValueError, match="pole"):
        geographic_extent(np.array([lat]), np.array([10.0]), _KM_PER_DEG_LAT, _VERTICAL)


def test_box_wrapping_all_longitudes_is_rejected():
    # 8 deg margin at 80N: phi_edge = 88 deg, each side grows 8 / cos(88 deg) ~= 229 deg.
    with pytest.raises(ValueError, match="360"):
        geographic_extent(np.array([80.0]), np.array([10.0]), 8 * _KM_PER_DEG_LAT, _VERTICAL)
