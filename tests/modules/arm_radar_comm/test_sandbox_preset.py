# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The packaged sandbox preset: the example ``adapt config --params arm_radar_comm`` prints."""

import re
from importlib.resources import files

import pytest
import yaml

from adapt.modules.arm_radar_comm.config import RadarCommandConfig

pytestmark = pytest.mark.unit

PRESET = files("adapt.modules.arm_radar_comm") / "presets" / "sandbox.yaml"


def test_sandbox_preset_is_a_valid_dry_run_section():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["arm_radar_comm"]
    assert RadarCommandConfig.model_validate(section).api is None


def test_sandbox_preset_asks_for_the_confirmed_scan_pattern():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["arm_radar_comm"]
    assert section["radar"]["scan_pattern"] == "rhi3_ppi7"


def _uncommented(pattern: str, null_line: str) -> str:
    """The preset with the block it shows in comments in place of its ``null`` line."""
    indent = null_line[: len(null_line) - len(null_line.lstrip())]
    text = re.sub(rf"(?m)^{re.escape(null_line)}.*\n", "", PRESET.read_text("utf-8"))
    return re.sub(pattern, indent + r"\1", text)


def test_the_api_part_the_preset_shows_is_valid_once_uncommented():
    shown = _uncommented(r"(?m)^  # (api:|  \w+:)", "  api: null")

    api = RadarCommandConfig.model_validate(yaml.safe_load(shown)["arm_radar_comm"]).api

    assert (api.base_url_env, api.api_key_env, api.ca_file_env) == (
        "ARM_RADAR_URL",
        "BNF_RADAR_API_KEY",
        "ARM_API_CERT",
    )


def test_the_vertical_band_the_preset_shows_is_valid_once_uncommented():
    shown = _uncommented(r"(?m)^    # (vertical_band:|  \w+:)", "    vertical_band: null")

    extent = RadarCommandConfig.model_validate(yaml.safe_load(shown)["arm_radar_comm"]).extent

    assert (extent.vertical_band.altitude_min_m, extent.vertical_band.altitude_max_m) == (
        500.0,
        15000.0,
    )


def test_the_preset_writes_every_option_none_is_left_to_a_default():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["arm_radar_comm"]

    assert list(section) == ["planning", "radar", "extent", "api"]
    assert section["radar"] == {"scan_pattern": "rhi3_ppi7", "user_id_env": "ARM_ADAPT_USER"}
    assert section["extent"]["vertical_band"] is None and section["api"] is None
