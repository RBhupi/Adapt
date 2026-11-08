# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Loading the selected cell's projected footprints and vertical extent from the store."""

from datetime import UTC, datetime

import pytest

from adapt.api import StoreClient, StoreError
from adapt.consumers.radar_command.config import AltitudeBand
from adapt.consumers.radar_command.extent import VerticalBounds
from adapt.consumers.radar_command.selection import SelectionRef
from adapt.consumers.radar_command.source import TargetUnavailable, load_target
from tests.consumers.radar_command.synthetic_store import (
    COLLECTION,
    RUN,
    SCAN_TIMES,
    UID_A,
    UID_B,
    add_run,
    build_store,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def with_stats(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("rc_source_stats"), volume_stats=True)


@pytest.fixture(scope="module")
def without_stats(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("rc_source_nostats"), volume_stats=False)


def _ref(built, scan=1, cell_uid=UID_A):
    return SelectionRef(
        COLLECTION, RUN, built.scan_ids[scan], SCAN_TIMES[scan], cell_uid, "0123456789abcdef"
    )


def _pairs(footprint):
    return sorted(
        (round(float(lat), 6), round(float(lon), 6))
        for lat, lon in zip(footprint.lats, footprint.lons, strict=True)
    )


def test_one_footprint_per_projection_minute_after_the_scan(with_stats):
    with StoreClient(with_stats.root) as client:
        geometry = load_target(client, _ref(with_stats), vertical_band=None)

    assert [fp.time for fp in geometry.footprints] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in range(6, 21)
    ]


def test_footprint_is_the_cells_projected_pixels_in_that_minute(with_stats):
    with StoreClient(with_stats.root) as client:
        geometry = load_target(client, _ref(with_stats), vertical_band=None)

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


def test_pixel_radius_is_half_the_pixel_diagonal(with_stats):
    with StoreClient(with_stats.root) as client:
        geometry = load_target(client, _ref(with_stats), vertical_band=None)

    # 600 m x 800 m pixels: diagonal 1000 m, half of it 0.5 km.
    assert geometry.pixel_radius_km == pytest.approx(0.5)


def test_vertical_comes_from_cell_volume_stats_lifted_to_amsl(with_stats):
    with StoreClient(with_stats.root) as client:
        geometry = load_target(client, _ref(with_stats), vertical_band=None)

    # Scan 1: uid-a base 1600 m / top 9600 m above the 369 m grid origin.
    assert geometry.vertical == VerticalBounds(
        bottom_m=1969.0, top_m=9969.0, source="cell_volume_stats"
    )


def test_run_without_cell_volume_stats_uses_the_configured_band(without_stats):
    band = AltitudeBand(altitude_min_m=500.0, altitude_max_m=12000.0)
    with StoreClient(without_stats.root) as client:
        geometry = load_target(client, _ref(without_stats), vertical_band=band)

    assert geometry.vertical == VerticalBounds(bottom_m=500.0, top_m=12000.0, source="config")


def test_run_without_volume_stats_uses_the_band_when_another_run_wrote_them(tmp_path):
    # run-1 of KTST wrote cell_volume_stats, so the collection declares the table;
    # run-2 was processed without the module.
    built = build_store(tmp_path, volume_stats=True)
    add_run(built, "run-2", volume_scans=())
    band = AltitudeBand(altitude_min_m=500.0, altitude_max_m=12000.0)
    ref = SelectionRef(
        COLLECTION, "run-2", built.scan_ids[1], SCAN_TIMES[1], UID_A, "0123456789abcdef"
    )
    with StoreClient(built.root) as client:
        geometry = load_target(client, ref, vertical_band=band)

    assert geometry.vertical == VerticalBounds(bottom_m=500.0, top_m=12000.0, source="config")


def test_run_without_cell_volume_stats_and_no_band_is_unavailable(without_stats):
    with (
        StoreClient(without_stats.root) as client,
        pytest.raises(TargetUnavailable, match="vertical_band"),
    ):
        load_target(client, _ref(without_stats), vertical_band=None)


def test_cell_without_a_finite_base_and_top_is_unavailable(with_stats):
    with (
        StoreClient(with_stats.root) as client,
        pytest.raises(TargetUnavailable, match="cell_volume_stats"),
    ):
        load_target(client, _ref(with_stats, cell_uid=UID_B), vertical_band=None)


def test_cell_uid_not_in_the_scan_is_unavailable(with_stats):
    with StoreClient(with_stats.root) as client, pytest.raises(TargetUnavailable, match="uid-zz"):
        load_target(client, _ref(with_stats, cell_uid="uid-zz"), vertical_band=None)


def test_scan_without_projection_frames_is_unavailable(with_stats):
    # The first scan of a run has no previous scan to project from.
    with (
        StoreClient(with_stats.root) as client,
        pytest.raises(TargetUnavailable, match="projection_minutes"),
    ):
        load_target(client, _ref(with_stats, scan=0), vertical_band=None)


def test_unknown_scan_raises_the_store_error(with_stats):
    ref = SelectionRef(
        COLLECTION, RUN, "ffffffffffffffff", SCAN_TIMES[1], UID_A, "0123456789abcdef"
    )
    with (
        StoreClient(with_stats.root) as client,
        pytest.raises(StoreError, match="ffffffffffffffff"),
    ):
        load_target(client, ref, vertical_band=None)


def test_minute_where_the_cell_is_not_projected_has_an_empty_footprint(tmp_path):
    built = build_store(tmp_path, volume_stats=True, absent_frame=4)
    with StoreClient(built.root) as client:
        geometry = load_target(client, _ref(built), vertical_band=None)

    assert geometry.footprints[4].time == datetime(2026, 6, 1, 12, 10, tzinfo=UTC)
    assert geometry.footprints[4].lats.size == 0
    assert geometry.footprints[4].lons.size == 0
    assert geometry.footprints[5].lats.size == 6
