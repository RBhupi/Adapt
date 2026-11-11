# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The packaged sandbox preset: the example ``adapt config --show radar_command`` prints."""

from importlib.resources import files

import pytest
import yaml

from adapt.modules.radar_command.config import RadarCommandConfig

pytestmark = pytest.mark.unit

PRESET = files("adapt.modules.radar_command") / "presets" / "sandbox.yaml"


def test_sandbox_preset_is_a_valid_dry_run_section():
    section = yaml.safe_load(PRESET.read_text("utf-8"))["radar_command"]
    assert RadarCommandConfig.model_validate(section).api is None
