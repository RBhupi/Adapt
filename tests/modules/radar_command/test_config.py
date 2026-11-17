# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The radar_command section of a pipeline file's ``consumers:``."""

import re

import pytest
import yaml
from pydantic import ValidationError

from adapt.modules.radar_command.config import RadarCommandConfig, config_hash

pytestmark = pytest.mark.unit

RADAR_BLOCK = """\
radar:
  scan_pattern: sector_rhi
  user_id: synthetic_user
"""

VALID_YAML = (
    RADAR_BLOCK
    + """\
extent:
  horizontal_buffer_km: 2.0
  vertical_band:
    altitude_min_m: 500.0
    altitude_max_m: 15000.0
"""
)


def _load(text):
    return RadarCommandConfig.model_validate(yaml.safe_load(text))


def _with_planning(horizon_minutes, interval_seconds):
    planning = (
        f"planning:\n  horizon_minutes: {horizon_minutes}\n  interval_seconds: {interval_seconds}\n"
    )
    return planning + VALID_YAML


def test_valid_section_loads_with_defaults():
    cfg = _load(VALID_YAML)
    assert cfg.planning.horizon_minutes == 12
    assert cfg.planning.interval_seconds == 60
    assert cfg.radar.scan_pattern == "sector_rhi"
    assert cfg.extent.vertical_band.altitude_max_m == 15000.0
    assert cfg.api is None


def test_config_is_frozen():
    cfg = _load(VALID_YAML)
    with pytest.raises(ValidationError, match="frozen"):
        cfg.planning.horizon_minutes = 6  # type: ignore[misc]


def test_output_paths_are_not_configurable():
    # The launcher picks the output directory; the names inside it are fixed.
    with pytest.raises(ValidationError, match="output"):
        _load(VALID_YAML + "output:\n  directory: /data/radar_command\n")


# --- planning ---------------------------------------------------------------


@pytest.mark.parametrize("horizon_minutes", [10, 15])
def test_non_default_horizon_accepted(horizon_minutes):
    cfg = _load(_with_planning(horizon_minutes, 60))
    assert cfg.planning.horizon_minutes == horizon_minutes


def test_zero_horizon_raises():
    with pytest.raises(ValidationError, match="horizon_minutes"):
        _load(_with_planning(0, 60))


def test_horizon_beyond_one_hour_raises():
    with pytest.raises(ValidationError, match="horizon_minutes"):
        _load(_with_planning(61, 60))


def test_zero_interval_raises():
    with pytest.raises(ValidationError, match="interval_seconds"):
        _load(_with_planning(12, 0))


def test_interval_not_whole_minutes_raises():
    # 90 s divides the 720 s horizon but falls between projection minutes.
    with pytest.raises(ValidationError, match="interval_seconds"):
        _load(_with_planning(12, 90))


def test_interval_not_dividing_horizon_raises():
    # 420 s is 7 whole minutes, but 720 s / 420 s leaves 300 s over.
    with pytest.raises(ValidationError, match="interval_seconds"):
        _load(_with_planning(12, 420))


# --- finite numbers ---------------------------------------------------------


def test_nan_value_raises():
    text = VALID_YAML.replace("altitude_min_m: 500.0", "altitude_min_m: .nan")
    with pytest.raises(ValidationError, match="altitude_min_m"):
        _load(text)


@pytest.mark.parametrize("line", ["horizontal_buffer_km: 2.0", "altitude_max_m: 15000.0"])
def test_infinite_value_raises(line):
    key = line.split(":")[0]
    text = VALID_YAML.replace(line, f"{key}: .inf")
    with pytest.raises(ValidationError, match=key):
        _load(text)


# --- extent -----------------------------------------------------------------


def test_missing_horizontal_buffer_raises():
    text = VALID_YAML.replace("  horizontal_buffer_km: 2.0\n", "")
    with pytest.raises(ValidationError, match="horizontal_buffer_km"):
        _load(text)


def test_negative_horizontal_buffer_raises():
    text = VALID_YAML.replace("horizontal_buffer_km: 2.0", "horizontal_buffer_km: -0.5")
    with pytest.raises(ValidationError, match="horizontal_buffer_km"):
        _load(text)


def test_vertical_band_is_optional():
    band = "  vertical_band:\n    altitude_min_m: 500.0\n    altitude_max_m: 15000.0\n"
    cfg = _load(VALID_YAML.replace(band, ""))
    assert cfg.extent.vertical_band is None


@pytest.mark.parametrize("altitude_min_m", ["15000.0", "16000.0"])
def test_empty_vertical_band_raises(altitude_min_m):
    text = VALID_YAML.replace("altitude_min_m: 500.0", f"altitude_min_m: {altitude_min_m}")
    with pytest.raises(ValidationError, match="vertical_band"):
        _load(text)


# --- whole section ----------------------------------------------------------


@pytest.mark.parametrize(
    ("loc", "line"),
    [
        ("radar.scan_pattern", "scan_pattern: sector_rhi"),
        ("radar.user_id", "user_id: synthetic_user"),
    ],
)
def test_empty_string_raises(loc, line):
    key = line.split(":")[0]
    text = VALID_YAML.replace(line, f"{key}: ''")
    with pytest.raises(ValidationError, match=re.escape(loc)):
        _load(text)


@pytest.mark.parametrize(
    ("anchor", "unknown"),
    [
        ("radar:\n", "retries: 3\n"),
        ("radar:\n", "  priority: 1\n"),
        ("  vertical_band:\n", "    units: m\n"),
    ],
)
def test_unknown_key_anywhere_raises(anchor, unknown):
    # A top-level unknown goes before the anchor, a nested one after it.
    nested = unknown.startswith(" ")
    text = VALID_YAML.replace(anchor, anchor + unknown if nested else unknown + anchor)
    with pytest.raises(ValidationError, match=unknown.split(":")[0].strip()):
        _load(text)


# --- config_hash ------------------------------------------------------------

RADAR_BLOCK_REORDERED = """\
radar:
  user_id: synthetic_user
  scan_pattern: sector_rhi
"""


def test_config_hash_equal_for_identical_configs():
    # Explicit defaults, reordered keys and int-for-float leave the effective
    # radar_command config unchanged.
    equivalent = (
        _with_planning(12, 60)
        .replace(RADAR_BLOCK, RADAR_BLOCK_REORDERED)
        .replace("altitude_min_m: 500.0", "altitude_min_m: 500")
    )
    assert config_hash(_load(VALID_YAML)) == config_hash(_load(equivalent))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("radar:\n", "planning:\n  horizon_minutes: 15\nradar:\n"),
        ("scan_pattern: sector_rhi", "scan_pattern: sector_ppi"),
        ("altitude_min_m: 500.0", "altitude_min_m: 501.0"),
        ("horizontal_buffer_km: 2.0", "horizontal_buffer_km: 0.0"),
        ("altitude_max_m: 15000.0", "altitude_max_m: 14000.0"),
    ],
)
def test_config_hash_differs_when_a_value_changes(old, new):
    changed = VALID_YAML.replace(old, new)
    assert config_hash(_load(VALID_YAML)) != config_hash(_load(changed))


def test_config_hash_is_16_hex_chars():
    assert re.fullmatch(r"[0-9a-f]{16}", config_hash(_load(VALID_YAML)))


# --- api ----------------------------------------------------------------------

API_BLOCK = """\
api:
  base_url_env: ADAPT_TEST_RADAR_URL
  api_key_env: ADAPT_TEST_RADAR_KEY
  ca_file_env: ADAPT_TEST_RADAR_CA
  timeout_seconds: 30
  max_scan_age_seconds: 480
"""


def test_without_an_api_section_commands_are_only_written_to_files():
    assert _load(VALID_YAML).api is None


def test_api_section_names_the_variables_that_hold_its_settings():
    cfg = _load(VALID_YAML + API_BLOCK)
    assert cfg.api.base_url_env == "ADAPT_TEST_RADAR_URL"
    assert cfg.api.api_key_env == "ADAPT_TEST_RADAR_KEY"
    assert cfg.api.ca_file_env == "ADAPT_TEST_RADAR_CA"
    assert cfg.api.timeout_seconds == 30
    assert cfg.api.max_scan_age_seconds == 480


@pytest.mark.parametrize(
    "option",
    ["base_url_env", "api_key_env", "ca_file_env", "timeout_seconds", "max_scan_age_seconds"],
)
def test_every_api_option_is_required(option):
    lines = [line for line in API_BLOCK.splitlines() if not line.strip().startswith(option)]
    with pytest.raises(ValidationError, match=option):
        _load(VALID_YAML + "\n".join(lines) + "\n")


@pytest.mark.parametrize(
    "line",
    ["base_url: https://radar.example.test", "ca_file: /etc/ssl/ca.cer"],
    ids=["address", "ca-path"],
)
def test_the_address_and_the_ca_path_are_refused_in_the_config(line):
    # One source each: the environment variable the section names.
    with pytest.raises(ValidationError, match=line.split(":")[0]):
        _load(VALID_YAML + API_BLOCK + f"  {line}\n")


def test_api_key_itself_is_refused_in_the_config():
    text = VALID_YAML + API_BLOCK + "  api_key: s3cret\n"
    with pytest.raises(ValidationError, match="api_key"):
        _load(text)


@pytest.mark.parametrize("option", ["base_url_env", "api_key_env", "ca_file_env"])
def test_a_variable_name_must_not_be_empty(option):
    text = re.sub(rf"{option}: \w+", f"{option}: ''", VALID_YAML + API_BLOCK)
    with pytest.raises(ValidationError, match=option):
        _load(text)


@pytest.mark.parametrize("seconds", [0, -1, ".inf"])
def test_the_scan_age_limit_must_be_a_positive_number(seconds):
    text = VALID_YAML + API_BLOCK.replace(
        "max_scan_age_seconds: 480", f"max_scan_age_seconds: {seconds}"
    )
    with pytest.raises(ValidationError, match="max_scan_age_seconds"):
        _load(text)


@pytest.mark.parametrize("seconds", [0, -1])
def test_api_timeout_must_be_positive(seconds):
    text = VALID_YAML + API_BLOCK.replace("timeout_seconds: 30", f"timeout_seconds: {seconds}")
    with pytest.raises(ValidationError, match="timeout_seconds"):
        _load(text)
