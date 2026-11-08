# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command wire format and validation — worked examples."""

import json
import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from adapt.consumers.radar_command.command import (
    CommandValidationError,
    RadarCommand,
    TargetEntry,
    to_json,
    validate,
)
from adapt.consumers.radar_command.extent import GeoExtent

pytestmark = pytest.mark.unit

_T1 = datetime(2026, 6, 1, 12, 1, tzinfo=UTC)
_T2 = datetime(2026, 6, 1, 12, 2, tzinfo=UTC)


def _extent(**overrides):
    fields = {
        "lat_center_deg": 36.6,
        "lon_center_deg": -97.5,
        "lat_width_deg": 0.4,
        "lon_width_deg": 0.5,
        "alt_bottom_m": 1000.0,
        "alt_top_m": 9000.0,
    }
    return GeoExtent(**(fields | overrides))


def _command(entries=None, **overrides):
    if entries is None:
        entries = (TargetEntry(time=_T1, extent=_extent()),)
    fields = {"scan_pattern": "3rhi_7ppi", "user_id": "operator", "target_id": "plan-1"}
    return RadarCommand(**(fields | overrides), entries=tuple(entries))


_M_PER_DEG = 6371000.0 * math.pi / 180


def test_one_entry_command_serialises_to_the_flat_sandbox_payload():
    payload = json.loads(to_json(_command()))

    assert payload == {
        "lat": pytest.approx(36.6),
        "lon": pytest.approx(-97.5),
        "alt": 5000.0,
        "lat_extent": pytest.approx(0.4 * _M_PER_DEG),
        "lon_extent": pytest.approx(0.5 * _M_PER_DEG * math.cos(math.radians(36.6))),
        "alt_extent": 8000.0,
        "user_id": "operator",
        "target_id": "plan-1",
        "scan_pattern": "3rhi_7ppi",
    }
    assert list(payload) == [
        "lat",
        "lon",
        "alt",
        "lat_extent",
        "lon_extent",
        "alt_extent",
        "user_id",
        "target_id",
        "scan_pattern",
    ]


def test_the_single_box_covers_every_planned_minute():
    # 12:01 box spans lat 36.4..36.8, lon -97.75..-97.25, 1-9 km;
    # 12:02 box spans lat 36.6..37.0, lon -97.5..-97.0, 2-11 km.
    later = _extent(
        lat_center_deg=36.8, lon_center_deg=-97.25, alt_bottom_m=2000.0, alt_top_m=11000.0
    )
    cmd = _command(entries=(TargetEntry(_T1, _extent()), TargetEntry(_T2, later)))

    payload = json.loads(to_json(cmd))

    assert payload["lat"] == pytest.approx(36.7)
    assert payload["lon"] == pytest.approx(-97.375)
    assert payload["lat_extent"] == pytest.approx(0.6 * _M_PER_DEG)
    assert payload["lon_extent"] == pytest.approx(0.75 * _M_PER_DEG * math.cos(math.radians(36.7)))
    assert (payload["alt"], payload["alt_extent"]) == (6000.0, 10000.0)


def test_command_file_carries_no_api_key():
    text = to_json(_command()).lower()
    assert "key" not in text and "token" not in text


def test_validate_rejects_command_without_entries():
    with pytest.raises(CommandValidationError, match="entry"):
        validate(_command(entries=()))


@pytest.mark.parametrize("field", ["scan_pattern", "user_id", "target_id"])
def test_validate_rejects_empty_identity_string(field):
    with pytest.raises(CommandValidationError, match=field):
        validate(_command(**{field: ""}))


def _at(time):
    return _command(entries=(TargetEntry(time=time, extent=_extent()),))


def test_validate_rejects_naive_time():
    with pytest.raises(CommandValidationError, match="timezone"):
        validate(_at(datetime(2026, 6, 1, 12, 1)))


def test_validate_rejects_non_utc_offset():
    cdt = timezone(timedelta(hours=-5))

    with pytest.raises(CommandValidationError, match="UTC"):
        validate(_at(datetime(2026, 6, 1, 7, 1, tzinfo=cdt)))


@pytest.mark.parametrize(
    "time",
    [datetime(2026, 6, 1, 12, 1, 30, tzinfo=UTC), datetime(2026, 6, 1, 12, 1, 0, 5, tzinfo=UTC)],
    ids=["seconds", "microseconds"],
)
def test_validate_rejects_time_off_the_whole_minute(time):
    with pytest.raises(CommandValidationError, match="whole minute"):
        validate(_at(time))


@pytest.mark.parametrize("second_time", [_T1, _T1 - timedelta(minutes=1)], ids=["equal", "earlier"])
def test_validate_rejects_times_not_strictly_increasing(second_time):
    entries = (
        TargetEntry(time=_T1, extent=_extent()),
        TargetEntry(time=second_time, extent=_extent()),
    )

    with pytest.raises(CommandValidationError, match="increasing"):
        validate(_command(entries=entries))


def _with(**extent_overrides):
    return _command(entries=(TargetEntry(time=_T1, extent=_extent(**extent_overrides)),))


@pytest.mark.parametrize(
    "field",
    [
        "lat_center_deg",
        "lon_center_deg",
        "lat_width_deg",
        "lon_width_deg",
        "alt_bottom_m",
        "alt_top_m",
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf], ids=["nan", "inf"])
def test_validate_rejects_non_finite_number(field, value):
    with pytest.raises(CommandValidationError, match=f"{field}.*finite"):
        validate(_with(**{field: value}))


def test_validate_rejects_zero_lat_width():
    with pytest.raises(CommandValidationError, match="lat_width_deg"):
        validate(_with(lat_width_deg=0.0))


def test_validate_rejects_zero_lon_width():
    with pytest.raises(CommandValidationError, match="lon_width_deg"):
        validate(_with(lon_width_deg=0.0))


@pytest.mark.parametrize("lat", [90.5, -90.5], ids=["north", "south"])
def test_validate_rejects_lat_center_outside_plus_minus_90(lat):
    with pytest.raises(CommandValidationError, match="lat_center_deg"):
        validate(_with(lat_center_deg=lat))


@pytest.mark.parametrize("lat", [89.9, -89.9], ids=["north", "south"])
def test_validate_rejects_box_crossing_a_pole(lat):
    # 89.9 +- 0.4 / 2 reaches 90.1: the box spills over the pole.
    with pytest.raises(CommandValidationError, match="pole"):
        validate(_with(lat_center_deg=lat, lat_width_deg=0.4))


@pytest.mark.parametrize("lon", [180.0, -180.5], ids=["plus-180", "below-minus-180"])
def test_validate_rejects_lon_center_outside_half_open_range(lon):
    with pytest.raises(CommandValidationError, match="lon_center_deg"):
        validate(_with(lon_center_deg=lon))


@pytest.mark.parametrize("top_m", [3000.0, 2000.0], ids=["equal", "below"])
def test_validate_rejects_alt_top_not_above_bottom(top_m):
    with pytest.raises(CommandValidationError, match="alt_top_m"):
        validate(_with(alt_bottom_m=3000.0, alt_top_m=top_m))


@pytest.mark.parametrize(
    "cmd",
    [_command(entries=()), _with(lat_width_deg=math.nan)],
    ids=["no-entries", "nan-width"],
)
def test_to_json_refuses_invalid_command(cmd):
    with pytest.raises(CommandValidationError):
        to_json(cmd)
