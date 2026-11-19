# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pin: the hash of the packaged target selection rule.

The hash is part of the ``target_selection`` table key ``(run_id, scan_id,
config_hash)`` and is recorded on every radar command, so rules that did not
change must keep it: with another hash, the rows of one rule read as two.
"""

from importlib.resources import files

import pytest
import yaml

from adapt.modules.target_selection.config import TSEConfig, config_hash

pytestmark = pytest.mark.unit

PRESET = files("adapt.modules.target_selection") / "presets" / "growing_isolated.yaml"


def test_the_packaged_rule_keeps_its_hash():
    rule = TSEConfig.model_validate(yaml.safe_load(PRESET.read_text("utf-8"))["target_selection"])

    assert config_hash(rule) == "9364593f43813994"
