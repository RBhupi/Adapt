# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The packaged sandbox preset: the example ``adapt config --show radar_command`` prints."""

import re
from importlib.resources import files

import pytest
import yaml

from adapt.modules.radar_command.config import RadarCommandConfig

pytestmark = pytest.mark.unit

PRESET = files("adapt.modules.radar_command") / "presets" / "sandbox.yaml"


def test_sandbox_preset_is_a_valid_dry_run_section():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["radar_command"]
    assert RadarCommandConfig.model_validate(section).api is None


def test_sandbox_preset_asks_for_the_confirmed_scan_pattern():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["radar_command"]
    assert section["radar"]["scan_pattern"] == "rhi3_ppi7"


def test_the_api_part_the_preset_shows_is_valid_once_uncommented():
    shown = re.sub(r"(?m)^  # (api:|  \w+:)", r"  \1", PRESET.read_text("utf-8"))

    api = RadarCommandConfig.model_validate(yaml.safe_load(shown)["radar_command"]).api

    assert (api.base_url_env, api.api_key_env, api.ca_file_env) == (
        "BNF_API_URL",
        "BNF_RADAR_API_KEY",
        "ARM_API_CERT",
    )
