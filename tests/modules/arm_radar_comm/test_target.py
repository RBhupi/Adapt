# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The selected cell's projected footprints and vertical extent, from the scan's own data."""

from datetime import UTC, datetime

import pytest

from adapt.modules.arm_radar_comm.config import AltitudeBand
from adapt.modules.arm_radar_comm.extent import VerticalBounds
from adapt.modules.arm_radar_comm.target import SelectionRef, TargetUnavailable, target_geometry
from tests.helpers.radar_store import RUN, SCAN_TIMES, UID_A, UID_B, analysis_ds, volume_rows

pytestmark = pytest.mark.unit

_BAND = AltitudeBand(altitude_min_m=500.0, altitude_max_m=12000.0)


def _geometry(scan=1, cell_uid=UID_A, *, stats=True, band=None, absent_frame=None):
    ref = SelectionRef(RUN, f"scan-{scan}", SCAN_TIMES[scan], cell_uid, "0123456789abcdef")
    return target_geometry(
        analysis_ds(scan, absent_frame), ref, volume_rows(scan) if stats else None, band
    )


def _pairs(footprint):
    return sorted(
        (round(float(lat), 6), round(float(lon), 6))
        for lat, lon in zip(footprint.lats, footprint.lons, strict=True)
    )


def test_one_footprint_per_projection_minute_after_the_scan():
    assert [fp.time for fp in _geometry().footprints] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in range(6, 21)
    ]


def test_footprint_is_the_cells_projected_pixels_in_that_minute():
    geometry = _geometry()

    # uid-a covers rows 4-5 and, k minutes after 12:05, columns 2+k..4+k.
    assert _pairs(geometry.footprints[0]) == [
        (40.04, -87.97),
        (40.04, -87.96),
        (40.04, -87.95),
        (40.05, -87.97),
        (40.05, -87.96),
        (40.05, -87.95),
    ]
    assert _pairs(geometry.footprints[14]) == [
        (40.04, -87.83),
        (40.04, -87.82),
        (40.04, -87.81),
        (40.05, -87.83),
        (40.05, -87.82),
        (40.05, -87.81),
    ]


def test_pixel_radius_is_half_the_pixel_diagonal():
    # 600 m x 800 m pixels: diagonal 1000 m, half of it 0.5 km.
    assert _geometry().pixel_radius_km == pytest.approx(0.5)


def test_vertical_comes_from_the_cells_volume_stats_lifted_to_amsl():
    # Scan 1: uid-a base 1600 m / top 9600 m above the 369 m grid origin.
    assert _geometry().vertical == VerticalBounds(
        bottom_m=1969.0, top_m=9969.0, source="cell_volume_stats"
    )


def test_a_configured_band_is_the_vertical_extent():
    expected = VerticalBounds(bottom_m=500.0, top_m=12000.0, source="config")

    assert _geometry(stats=False, band=_BAND).vertical == expected
    assert _geometry(stats=True, band=_BAND).vertical == expected  # the band is what was asked


def test_no_volume_stats_and_no_band_is_unavailable():
    with pytest.raises(TargetUnavailable, match="vertical_band"):
        _geometry(stats=False)


def test_cell_without_a_finite_base_and_top_is_unavailable():
    with pytest.raises(TargetUnavailable, match="cell_volume_stats"):
        _geometry(cell_uid=UID_B)


def test_cell_uid_not_in_the_scan_is_unavailable():
    with pytest.raises(TargetUnavailable, match="uid-zz"):
        _geometry(cell_uid="uid-zz")


def test_scan_without_projection_frames_is_unavailable():
    # The first scan of a run has no previous scan to project from.
    with pytest.raises(TargetUnavailable, match="projection_minutes"):
        _geometry(scan=0)


def test_minute_where_the_cell_is_not_projected_has_an_empty_footprint():
    geometry = _geometry(absent_frame=4)

    assert geometry.footprints[4].time == datetime(2026, 6, 1, 12, 10, tzinfo=UTC)
    assert geometry.footprints[4].lats.size == 0
    assert geometry.footprints[4].lons.size == 0
    assert geometry.footprints[5].lats.size == 6
