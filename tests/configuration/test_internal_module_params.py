# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for InternalConfig.module_params — the per-module override channel.

Defaults to an empty dict. Enables extension modules to receive config without
another InternalConfig change.
"""

import pytest

pytestmark = pytest.mark.unit


class TestInternalConfigModuleParams:
    def test_module_params_defaults_to_empty_dict(self, internal_config):
        """A resolved InternalConfig has an empty module_params by default."""
        assert internal_config.module_params == {}

    def test_module_params_accepts_per_module_overrides(self, param_config, temp_dir):
        """module_params can hold a nested dict keyed by module name."""
        from adapt.configuration.schemas.resolve import resolve_config
        from adapt.configuration.schemas.user import UserConfig

        user = UserConfig(base_dir=str(temp_dir))
        cfg = resolve_config(param_config, user)
        # model_copy with a module_params payload validates the field type
        updated = cfg.model_copy(
            update={"module_params": {"analysis_3d": {"dbz_levels": [30, 40]}}}
        )
        assert updated.module_params["analysis_3d"]["dbz_levels"] == [30, 40]
