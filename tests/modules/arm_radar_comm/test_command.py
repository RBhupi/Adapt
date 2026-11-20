# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command wire format and validation — worked examples."""

import json
import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from adapt.modules.arm_radar_comm.command import (
    CommandValidationError,
    RadarCommand,
    TargetEntry,
    from_json,
    to_json,
    validate,
)
from adapt.modules.arm_radar_comm.extent import GeoExtent

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
    fields = {"scan_pattern": "rhi3_ppi7", "user_id": "operator", "target_id": "plan-1"}
    return RadarCommand(**(fields | overrides), entries=tuple(entries))


_M_PER_DEG = 6371000.0 * math.pi / 180


_FIELDS = [
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


def _target(text: str) -> dict:
    """The target of a command, checked to be the one thing its JSON holds."""
    body = json.loads(text)
    assert list(body) == ["AutoTarget"]
    return body["AutoTarget"]


def test_one_entry_command_serialises_to_one_wrapped_auto_target():
    target = _target(to_json(_command()))

    assert target == {
        "lat": pytest.approx(36.6),
        "lon": pytest.approx(-97.5),
        "alt": 5000.0,
        "lat_extent": pytest.approx(0.4 * _M_PER_DEG),
        "lon_extent": pytest.approx(0.5 * _M_PER_DEG * math.cos(math.radians(36.6))),
        "alt_extent": 8000.0,
        "user_id": "operator",
        "target_id": "plan-1",
        "scan_pattern": "rhi3_ppi7",
    }
    assert list(target) == _FIELDS


def test_the_single_box_covers_every_planned_minute():
    # 12:01 box spans lat 36.4..36.8, lon -97.75..-97.25, 1-9 km;
    # 12:02 box spans lat 36.6..37.0, lon -97.5..-97.0, 2-11 km.
    later = _extent(
        lat_center_deg=36.8, lon_center_deg=-97.25, alt_bottom_m=2000.0, alt_top_m=11000.0
    )
    cmd = _command(entries=(TargetEntry(_T1, _extent()), TargetEntry(_T2, later)))

    payload = _target(to_json(cmd))

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


def test_to_json_refuses_a_command_the_wire_cannot_carry():
    # validate() takes any non-empty identity; on the wire it must be a string.
    with pytest.raises(CommandValidationError, match="user_id"):
        to_json(_command(user_id=7))


# --- the wire format, read back -------------------------------------------------

_WIRE = {
    "lat": 34.642,
    "lon": -87.25,
    "alt": 1000.0,
    "lat_extent": 320.0,
    "lon_extent": 1000.0,
    "alt_extent": 250.0,
    "user_id": "user-123",
    "target_id": "target-456",
    "scan_pattern": "rhi3_ppi7",
}


def _text(**changes) -> str:
    return json.dumps({"AutoTarget": _WIRE | changes})


def test_a_generated_command_reads_back_as_what_it_says():
    text = to_json(_command())

    assert from_json(text).auto_target.model_dump() == _target(text)


def test_the_confirmed_example_is_a_valid_command():
    assert from_json(_text()).auto_target.model_dump() == _WIRE


def test_whole_numbers_are_numbers():
    assert from_json(_text(alt=1000, lat_extent=320)).auto_target.alt == 1000.0


@pytest.mark.parametrize("field", _FIELDS)
def test_a_command_missing_a_field_is_refused(field):
    target = {name: value for name, value in _WIRE.items() if name != field}

    with pytest.raises(CommandValidationError, match=field):
        from_json(json.dumps({"AutoTarget": target}))


@pytest.mark.parametrize(
    "body",
    [
        _WIRE,
        {"autotarget": _WIRE},
        {"Target": _WIRE},
        {"AutoTarget": [_WIRE]},
        {"AutoTarget": None},
        [{"AutoTarget": _WIRE}],
    ],
    ids=["no-wrapper", "lower-case", "other-name", "a-list", "null", "list-of-commands"],
)
def test_a_command_not_wrapped_as_one_auto_target_is_refused(body):
    with pytest.raises(CommandValidationError):
        from_json(json.dumps(body))


def test_a_field_the_radar_does_not_take_is_refused():
    with pytest.raises(CommandValidationError, match="api_key"):
        from_json(_text(api_key="s3cret"))


def test_a_second_part_beside_the_target_is_refused():
    with pytest.raises(CommandValidationError, match="priority"):
        from_json(json.dumps({"AutoTarget": _WIRE, "priority": 1}))


@pytest.mark.parametrize("field", _FIELDS[:6])
@pytest.mark.parametrize("value", [True, "12.5", None, [1.0]], ids=["bool", "text", "null", "list"])
def test_a_number_field_takes_only_a_number(field, value):
    with pytest.raises(CommandValidationError, match=field):
        from_json(_text(**{field: value}))


@pytest.mark.parametrize("field", _FIELDS[:6])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_a_number_that_is_not_finite_is_refused(field, literal):
    text = _text(**{field: 12345.5}).replace("12345.5", literal)

    with pytest.raises(CommandValidationError):
        from_json(text)


@pytest.mark.parametrize(
    ("field", "value"),
    [("lat", 90.5), ("lat", -90.5), ("lon", 180.5), ("lon", -180.5)],
)
def test_a_centre_off_the_globe_is_refused(field, value):
    with pytest.raises(CommandValidationError, match=field):
        from_json(_text(**{field: value}))


@pytest.mark.parametrize("field", ["lat_extent", "lon_extent", "alt_extent"])
@pytest.mark.parametrize("value", [0.0, -5.0], ids=["zero", "negative"])
def test_an_extent_must_be_positive(field, value):
    with pytest.raises(CommandValidationError, match=field):
        from_json(_text(**{field: value}))


@pytest.mark.parametrize("field", _FIELDS[6:])
@pytest.mark.parametrize("value", ["", 7, None], ids=["empty", "number", "null"])
def test_an_identity_or_pattern_must_be_a_non_empty_string(field, value):
    with pytest.raises(CommandValidationError, match=field):
        from_json(_text(**{field: value}))


@pytest.mark.parametrize(
    "text",
    ["", "not json", '{"AutoTarget": {"lat": 34.6', "{'AutoTarget': {}}"],
    ids=["empty", "words", "cut-short", "single-quotes"],
)
def test_text_that_is_not_json_is_refused(text):
    with pytest.raises(CommandValidationError):
        from_json(text)


def test_a_refusal_names_the_field_but_not_what_it_held():
    with pytest.raises(CommandValidationError, match="api_key.*user_id|user_id.*api_key") as info:
        from_json(_text(api_key="s3cret-value", user_id=5150))

    assert "s3cret-value" not in str(info.value) and "5150" not in str(info.value)


def test_a_field_given_twice_is_refused():
    text = _text().replace('"lat": 34.642', '"lat": 34.642, "lat": 35.0')

    with pytest.raises(CommandValidationError, match="lat"):
        from_json(text)
