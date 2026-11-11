# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests that user config module_params reaches InternalConfig.module_params.

This is the channel by which extension modules receive their per-module config:
a user writes `module_params: {analysis_3d: {...}}` and build_config reads it.
"""

import pytest

pytestmark = pytest.mark.unit

from adapt.configuration.schemas.param import ParamConfig  # noqa: E402
from adapt.configuration.schemas.resolve import resolve_config  # noqa: E402
from adapt.configuration.schemas.user import UserConfig  # noqa: E402


class TestModuleParamsRouting:
    def test_module_params_reaches_internal_config(self, tmp_path):
        user = UserConfig(
            base_dir=str(tmp_path),
            radar="KLOT",
            module_params={"analysis_3d": {"dbz_levels": [0, 10, 20, 30, 40, 50]}},
        )
        cfg = resolve_config(ParamConfig(), user)
        assert cfg.module_params["analysis_3d"]["dbz_levels"] == [0, 10, 20, 30, 40, 50]

    def test_absent_module_params_defaults_empty(self, tmp_path):
        user = UserConfig(base_dir=str(tmp_path), radar="KLOT")
        cfg = resolve_config(ParamConfig(), user)
        assert cfg.module_params == {}

    def test_to_internal_overrides_includes_module_params(self):
        user = UserConfig(module_params={"hail": {"min_vil": 3.0}})
        overrides = user.to_internal_overrides()
        assert overrides["module_params"] == {"hail": {"min_vil": 3.0}}
