# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Plans: one target entry per planned whole minute, truncated where the projection ends."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from adapt.consumers.radar_command.config import (
    ExtentConfig,
    OutputConfig,
    PlanningConfig,
    RadarCommandConfig,
    RadarConfig,
)
from adapt.consumers.radar_command.extent import VerticalBounds
from adapt.consumers.radar_command.plan import build_plan
from adapt.consumers.radar_command.selection import SelectionRef
from adapt.consumers.radar_command.source import Footprint, TargetGeometry, TargetUnavailable

pytestmark = pytest.mark.unit

_GRID_ORIGIN = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _config(planning=None, buffer_km=0.0):
    return RadarCommandConfig(
        planning=planning or PlanningConfig(),
        radar=RadarConfig(scan_pattern="ppi_sector", user_id="operator"),
        extent=ExtentConfig(horizontal_buffer_km=buffer_km),
        output=OutputConfig(directory="out"),
    )


def _geometry(frames=15, absent=None, step_minutes=1):
    """Two-pixel footprint at 40.00/40.02 N, 88.00/87.98 W, one frame every
    ``step_minutes`` from 12:00 + ``step_minutes`` (the projection's grid for a
    scan between 12:00 and the first frame)."""
    empty = np.array([])
    footprints = tuple(
        Footprint(
            time=_GRID_ORIGIN + timedelta(minutes=step_minutes * (k + 1)),
            lats=empty if k == absent else np.array([40.0, 40.02]),
            lons=empty if k == absent else np.array([-88.0, -87.98]),
        )
        for k in range(frames)
    )
    return TargetGeometry(
        footprints=footprints,
        pixel_radius_km=0.5,
        vertical=VerticalBounds(bottom_m=1969.0, top_m=9969.0, source="cell_volume_stats"),
    )


def _ref(scan_time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC), cell_uid="uid-a"):
    return SelectionRef(
        "KTST", "run-1", "0123456789abcdef", scan_time, cell_uid, "0123456789abcdef"
    )


@pytest.mark.parametrize("second", [0, 37])
def test_default_plan_covers_the_twelve_whole_minutes_after_the_scan(second):
    scan_time = datetime(2026, 6, 1, 12, 0, second, tzinfo=UTC)

    plan = build_plan(_geometry(), _ref(scan_time), _config())

    assert [entry.time for entry in plan.command.entries] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in range(1, 13)
    ]


@pytest.mark.parametrize(
    ("planning", "minutes"),
    [
        (PlanningConfig(horizon_minutes=10), list(range(1, 11))),
        (PlanningConfig(horizon_minutes=15), list(range(1, 16))),
        (PlanningConfig(interval_seconds=120), [1, 3, 5, 7, 9, 11]),
    ],
)
def test_horizon_and_interval_set_the_planned_minutes(planning, minutes):
    plan = build_plan(_geometry(), _ref(), _config(planning))

    assert [entry.time for entry in plan.command.entries] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in minutes
    ]
    assert plan.truncated is False


def test_plan_follows_a_two_minute_projection_grid():
    # registration_step_minutes: 2 puts the frames of a 12:00:37 scan at 12:02, 12:04, ...
    plan = build_plan(
        _geometry(step_minutes=2),
        _ref(datetime(2026, 6, 1, 12, 0, 37, tzinfo=UTC)),
        _config(PlanningConfig(interval_seconds=120)),
    )

    assert [entry.time for entry in plan.command.entries] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in (2, 4, 6, 8, 10, 12)
    ]
    assert plan.truncated is False


def test_interval_finer_than_the_projection_grid_raises_naming_both():
    # A 60 s interval on 2-minute frames would plan 12:03, which no frame covers.
    with pytest.raises(ValueError, match=r"interval_seconds=60.*2026-06-01T12:03:00Z"):
        build_plan(_geometry(step_minutes=2), _ref(), _config(PlanningConfig()))


def test_horizon_beyond_the_projection_truncates_at_the_last_frame():
    plan = build_plan(_geometry(frames=15), _ref(), _config(PlanningConfig(horizon_minutes=20)))

    assert len(plan.command.entries) == 15
    assert plan.command.entries[-1].time == datetime(2026, 6, 1, 12, 15, tzinfo=UTC)
    assert plan.truncated is True


def test_target_missing_from_the_fifth_frame_stops_the_plan_after_four_entries():
    plan = build_plan(_geometry(absent=4), _ref(), _config())

    assert [entry.time for entry in plan.command.entries] == [
        datetime(2026, 6, 1, 12, minute, tzinfo=UTC) for minute in range(1, 5)
    ]
    assert plan.truncated is True


def test_target_missing_from_the_first_frame_is_unavailable():
    with pytest.raises(TargetUnavailable, match="uid-a"):
        build_plan(_geometry(absent=0), _ref(), _config())


def test_buffer_widens_the_box_beyond_the_pixel_radius():
    # Footprint spans 0.02 deg of latitude; one degree is 6371 km * pi / 180 = 111.195 km.
    unbuffered = build_plan(_geometry(), _ref(), _config(buffer_km=0.0)).command.entries[0]
    buffered = build_plan(_geometry(), _ref(), _config(buffer_km=2.0)).command.entries[0]

    # 0.5 km pixel radius on each side: 0.02 + 1 / 111.195 deg.
    assert unbuffered.extent.lat_width_deg == pytest.approx(0.0289932161)
    # 2 km more on each side: 4 / 111.195 deg wider.
    assert buffered.extent.lat_width_deg - unbuffered.extent.lat_width_deg == pytest.approx(
        0.0359728642
    )
    assert buffered.extent.lon_width_deg > unbuffered.extent.lon_width_deg


def test_plan_id_is_a_stable_16_hex_identity_of_scan_cell_and_config():
    first = build_plan(_geometry(), _ref(), _config()).plan_id
    again = build_plan(_geometry(), _ref(), _config()).plan_id
    other_cell = build_plan(_geometry(), _ref(cell_uid="uid-b"), _config()).plan_id
    other_config = build_plan(_geometry(), _ref(), _config(buffer_km=2.0)).plan_id

    assert len(first) == 16
    assert set(first) <= set("0123456789abcdef")
    assert again == first
    assert other_cell != first
    assert other_config != first


def test_entry_times_are_utc():
    plan = build_plan(_geometry(), _ref(datetime(2026, 6, 1, 12, 0, 37, tzinfo=UTC)), _config())

    assert {entry.time.utcoffset() for entry in plan.command.entries} == {timedelta(0)}
