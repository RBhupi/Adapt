# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command YAML config — the radar_command section of the shared file."""

import re

import pytest
from pydantic import ValidationError

from adapt.consumers.radar_command.config import config_hash, load_config

pytestmark = pytest.mark.unit

TSE_SECTION = """\
target_selection:
  snapshot:
    growth_window_scans: 4
"""

RADAR_BLOCK = """\
  radar:
    command: track
    pattern: sector_rhi
    user: synthetic_user
"""

VALID_YAML = (
    TSE_SECTION
    + "radar_command:\n"
    + RADAR_BLOCK
    + """\
  extent:
    horizontal_buffer_km: 2.0
    vertical_band:
      altitude_min_m: 500.0
      altitude_max_m: 15000.0
  output:
    directory: /data/radar_command
"""
)


def _write(tmp_path, text):
    path = tmp_path / "consumers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _with_planning(horizon_minutes, interval_seconds):
    planning = (
        "radar_command:\n"
        "  planning:\n"
        f"    horizon_minutes: {horizon_minutes}\n"
        f"    interval_seconds: {interval_seconds}\n"
    )
    return VALID_YAML.replace("radar_command:\n", planning)


def test_valid_file_loads_with_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.planning.horizon_minutes == 12
    assert cfg.planning.interval_seconds == 60
    assert cfg.radar.pattern == "sector_rhi"
    assert cfg.extent.vertical_band.altitude_max_m == 15000.0
    assert cfg.output.directory == "/data/radar_command"
    assert cfg.output.current_file == "current_targets.json"
    assert cfg.output.history_directory == "history"


def test_config_is_frozen(tmp_path):
    cfg = load_config(_write(tmp_path, VALID_YAML))
    with pytest.raises(ValidationError, match="frozen"):
        cfg.planning.horizon_minutes = 6  # type: ignore[misc]


def test_missing_radar_command_section_raises(tmp_path):
    with pytest.raises(ValueError, match="'radar_command'"):
        load_config(_write(tmp_path, TSE_SECTION))


# --- planning ---------------------------------------------------------------


@pytest.mark.parametrize("horizon_minutes", [10, 15])
def test_non_default_horizon_accepted(tmp_path, horizon_minutes):
    cfg = load_config(_write(tmp_path, _with_planning(horizon_minutes, 60)))
    assert cfg.planning.horizon_minutes == horizon_minutes


def test_zero_horizon_raises(tmp_path):
    with pytest.raises(ValidationError, match="horizon_minutes"):
        load_config(_write(tmp_path, _with_planning(0, 60)))


def test_horizon_beyond_one_hour_raises(tmp_path):
    with pytest.raises(ValidationError, match="horizon_minutes"):
        load_config(_write(tmp_path, _with_planning(61, 60)))


def test_zero_interval_raises(tmp_path):
    with pytest.raises(ValidationError, match="interval_seconds"):
        load_config(_write(tmp_path, _with_planning(12, 0)))


def test_interval_not_whole_minutes_raises(tmp_path):
    # 90 s divides the 720 s horizon but falls between projection minutes.
    with pytest.raises(ValidationError, match="interval_seconds"):
        load_config(_write(tmp_path, _with_planning(12, 90)))


def test_interval_not_dividing_horizon_raises(tmp_path):
    # 420 s is 7 whole minutes, but 720 s / 420 s leaves 300 s over.
    with pytest.raises(ValidationError, match="interval_seconds"):
        load_config(_write(tmp_path, _with_planning(12, 420)))


# --- finite numbers ---------------------------------------------------------


def test_nan_value_raises(tmp_path):
    text = VALID_YAML.replace("altitude_min_m: 500.0", "altitude_min_m: .nan")
    with pytest.raises(ValidationError, match="altitude_min_m"):
        load_config(_write(tmp_path, text))


@pytest.mark.parametrize("line", ["horizontal_buffer_km: 2.0", "altitude_max_m: 15000.0"])
def test_infinite_value_raises(tmp_path, line):
    key = line.split(":")[0]
    text = VALID_YAML.replace(line, f"{key}: .inf")
    with pytest.raises(ValidationError, match=key):
        load_config(_write(tmp_path, text))


# --- extent -----------------------------------------------------------------


def test_missing_horizontal_buffer_raises(tmp_path):
    text = VALID_YAML.replace("    horizontal_buffer_km: 2.0\n", "")
    with pytest.raises(ValidationError, match="horizontal_buffer_km"):
        load_config(_write(tmp_path, text))


def test_negative_horizontal_buffer_raises(tmp_path):
    text = VALID_YAML.replace("horizontal_buffer_km: 2.0", "horizontal_buffer_km: -0.5")
    with pytest.raises(ValidationError, match="horizontal_buffer_km"):
        load_config(_write(tmp_path, text))


def test_vertical_band_is_optional(tmp_path):
    band = "    vertical_band:\n      altitude_min_m: 500.0\n      altitude_max_m: 15000.0\n"
    cfg = load_config(_write(tmp_path, VALID_YAML.replace(band, "")))
    assert cfg.extent.vertical_band is None


@pytest.mark.parametrize("altitude_min_m", ["15000.0", "16000.0"])
def test_empty_vertical_band_raises(tmp_path, altitude_min_m):
    text = VALID_YAML.replace("altitude_min_m: 500.0", f"altitude_min_m: {altitude_min_m}")
    with pytest.raises(ValidationError, match="vertical_band"):
        load_config(_write(tmp_path, text))


# --- output -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_file", "sub/current_targets.json"),
        ("current_file", "sub\\current_targets.json"),
        ("current_file", ""),
        ("history_directory", ".."),
        ("history_directory", "."),
    ],
)
def test_output_name_not_plain_raises(tmp_path, field, value):
    text = VALID_YAML.replace(
        "    directory: /data/radar_command\n",
        f"    directory: /data/radar_command\n    {field}: '{value}'\n",
    )
    with pytest.raises(ValidationError, match=field):
        load_config(_write(tmp_path, text))


# --- whole document ---------------------------------------------------------


@pytest.mark.parametrize(
    ("loc", "line"),
    [
        ("radar.command", "command: track"),
        ("radar.pattern", "pattern: sector_rhi"),
        ("radar.user", "user: synthetic_user"),
        ("output.directory", "directory: /data/radar_command"),
    ],
)
def test_empty_string_raises(tmp_path, loc, line):
    key = line.split(":")[0]
    text = VALID_YAML.replace(line, f"{key}: ''")
    with pytest.raises(ValidationError, match=re.escape(loc)):
        load_config(_write(tmp_path, text))


@pytest.mark.parametrize(
    ("anchor", "unknown"),
    [
        ("radar_command:\n", "  retries: 3\n"),
        ("  radar:\n", "    priority: 1\n"),
        ("    vertical_band:\n", "      units: m\n"),
        ("  output:\n", "    format: json\n"),
    ],
)
def test_unknown_key_anywhere_raises(tmp_path, anchor, unknown):
    text = VALID_YAML.replace(anchor, anchor + unknown)
    with pytest.raises(ValidationError, match=unknown.split(":")[0].strip()):
        load_config(_write(tmp_path, text))


# --- config_hash ------------------------------------------------------------

RADAR_BLOCK_REORDERED = """\
  radar:
    user: synthetic_user
    pattern: sector_rhi
    command: track
"""


def _hash_of(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return config_hash(load_config(path))


def test_config_hash_equal_for_identical_configs(tmp_path):
    # Explicit defaults, reordered keys, int-for-float and a different sibling
    # section leave the effective radar_command config unchanged.
    equivalent = (
        _with_planning(12, 60)
        .replace(RADAR_BLOCK, RADAR_BLOCK_REORDERED)
        .replace("altitude_min_m: 500.0", "altitude_min_m: 500")
        .replace("growth_window_scans: 4", "growth_window_scans: 9")
    )
    assert _hash_of(tmp_path, "a.yaml", VALID_YAML) == _hash_of(tmp_path, "b.yaml", equivalent)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("radar_command:\n", "radar_command:\n  planning:\n    horizon_minutes: 15\n"),
        ("pattern: sector_rhi", "pattern: sector_ppi"),
        ("altitude_min_m: 500.0", "altitude_min_m: 501.0"),
        ("horizontal_buffer_km: 2.0", "horizontal_buffer_km: 0.0"),
        ("altitude_max_m: 15000.0", "altitude_max_m: 14000.0"),
        ("/data/radar_command\n", "/data/radar_command\n    history_directory: plans\n"),
    ],
)
def test_config_hash_differs_when_a_value_changes(tmp_path, old, new):
    changed = VALID_YAML.replace(old, new)
    assert _hash_of(tmp_path, "a.yaml", VALID_YAML) != _hash_of(tmp_path, "b.yaml", changed)


def test_config_hash_is_16_hex_chars(tmp_path):
    assert re.fullmatch(r"[0-9a-f]{16}", _hash_of(tmp_path, "a.yaml", VALID_YAML))
