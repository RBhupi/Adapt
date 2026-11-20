# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pin: the hash of the packaged radar command options, with and without ``api:``.

The hash is part of the ``radar_commands`` table key ``(run_id, scan_id,
config_hash)`` and of every ``plan_id``, so options that did not change must
keep it: with another hash, the plans of one run stop being recognised as
already published. (Changed on purpose 2026-11-20: the user id is named by
``radar.user_id_env``.)
"""

from importlib.resources import files

import pytest
import yaml

from adapt.modules.arm_radar_comm.config import RadarCommandConfig, config_hash
from tests.helpers.pipeline_files import API

pytestmark = pytest.mark.unit

PRESET = files("adapt.modules.arm_radar_comm") / "presets" / "sandbox.yaml"


def _packaged_options() -> dict:
    return yaml.safe_load(PRESET.read_text("utf-8"))["arm_radar_comm"]


def test_the_packaged_dry_run_options_keep_their_hash():
    options = RadarCommandConfig.model_validate(_packaged_options())

    assert config_hash(options) == "6c30d258df181195"


def test_the_packaged_options_with_an_api_part_keep_their_hash():
    options = RadarCommandConfig.model_validate({**_packaged_options(), "api": API})

    assert config_hash(options) == "941a5cae4986b525"
