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
    fields = {"command": "add_targets", "pattern": "ppi_sector", "user": "operator"}
    return RadarCommand(**(fields | overrides), entries=tuple(entries))


def test_two_entry_command_serialises_to_wire_format():
    cmd = _command(
        entries=(
            TargetEntry(time=_T1, extent=_extent()),
            TargetEntry(
                time=_T2,
                extent=_extent(
                    lat_center_deg=36.65,
                    lon_center_deg=-97.45,
                    lat_width_deg=0.5,
                    lon_width_deg=0.6,
                    alt_bottom_m=1500.0,
                    alt_top_m=11500.0,
                ),
            ),
        )
    )

    expected = """{
  "Command": "add_targets",
  "Pattern": "ppi_sector",
  "User": "operator",
  "TargetData": {
    "2026-06-01T12:01:00Z": {
      "lat": 36.6,
      "lon": -97.5,
      "alt": 5000.0,
      "dLat": 0.4,
      "dLon": 0.5,
      "dAlt": 8000.0
    },
    "2026-06-01T12:02:00Z": {
      "lat": 36.65,
      "lon": -97.45,
      "alt": 6500.0,
      "dLat": 0.5,
      "dLon": 0.6,
      "dAlt": 10000.0
    }
  }
}
"""
    assert to_json(cmd) == expected


def test_altitude_is_band_midpoint_and_depth_is_band_thickness():
    extent = _extent(alt_bottom_m=1234.5, alt_top_m=10234.5)
    cmd = _command(entries=(TargetEntry(time=_T1, extent=extent),))

    target = json.loads(to_json(cmd))["TargetData"]["2026-06-01T12:01:00Z"]

    assert target["alt"] == 5734.5
    assert target["dAlt"] == 9000.0


def test_command_file_carries_no_api_token():
    assert "ApiToken" not in to_json(_command())


def test_validate_rejects_command_without_entries():
    with pytest.raises(CommandValidationError, match="entry"):
        validate(_command(entries=()))


@pytest.mark.parametrize("field", ["command", "pattern", "user"])
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
