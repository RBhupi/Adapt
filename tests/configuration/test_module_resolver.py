# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for resolve_module_configs — the registry-driven config engine.

Asks each named module for its config (``configure`` of its validated options)
and returns a dict keyed by "<name>_config".
"""

import pytest

pytestmark = pytest.mark.unit

from adapt.execution.pipeline_builder import _ensure_modules_registered  # noqa: E402

_CORE = ["ingest", "detection", "projection", "analysis", "tracking"]


@pytest.fixture(autouse=True)
def _register_core_modules():
    """Ensure the five core modules are registered before each test."""
    _ensure_modules_registered()


class TestResolveModuleConfigs:
    def test_returns_all_core_module_config_keys(self, internal_config):
        from adapt.configuration.schemas.module_resolver import resolve_module_configs

        configs = resolve_module_configs(internal_config, _CORE)
        for key in (
            "ingest_config",
            "detection_config",
            "projection_config",
            "analysis_config",
            "tracking_config",
        ):
            assert key in configs

    def test_detection_is_configured_from_the_runs_module_params(self, make_config):
        from adapt.configuration.schemas.module_resolver import resolve_module_configs

        options = {"method": "steiner_conv_strat", "steiner_conv_strat_params": {"intense": 45.0}}
        configs = resolve_module_configs(make_config(params={"detection": options}), _CORE)

        detection = configs["detection_config"]
        assert detection.method == "steiner_conv_strat"
        assert detection.method_params == {
            "intense": 45.0,
            "peak_relation": "default",
            "area_relation": "medium",
            "bkg_rad": 11000.0,
            "use_intense": True,
        }

    def test_analysis_config_does_not_depend_on_projections_options(self, make_config):
        """Analysis takes the number of projection steps from the data it is given."""
        from adapt.configuration.schemas.module_resolver import resolve_module_configs

        default = resolve_module_configs(make_config(), _CORE)
        other = resolve_module_configs(
            make_config(params={"projection": {"max_projection_steps": 7}}), _CORE
        )

        assert other["projection_config"].max_projection_steps == 7
        assert other["analysis_config"] == default["analysis_config"]

    def test_module_returning_none_contributes_no_key(self, internal_config):
        """A module without options has no config: it adds no config key."""
        from adapt.configuration.schemas.module_resolver import resolve_module_configs
        from adapt.execution.module_registry import registry
        from adapt.modules.base import BaseModule

        class _NoConfig(BaseModule):
            name = "no_config_probe"

            def run(self, context):
                return {}

        registry.register(_NoConfig)
        try:
            configs = resolve_module_configs(internal_config, [*_CORE, "no_config_probe"])
            assert "no_config_probe_config" not in configs
        finally:
            registry.unregister("no_config_probe")

    def test_only_the_named_modules_are_configured(self, internal_config):
        """A registered module that does not run is not configured, so its
        requirements (here: always refusing) cannot fail a run without it."""
        from adapt.configuration.schemas.module_resolver import resolve_module_configs
        from adapt.execution.module_registry import registry
        from adapt.modules.base import BaseModule

        class _Unconfigurable(BaseModule):
            name = "unconfigurable_probe"

            @classmethod
            def configure(cls, options, shared):
                raise ValueError("unconfigurable_probe needs params it was not given")

            def run(self, context):
                return {}

        registry.register(_Unconfigurable)
        try:
            configs = resolve_module_configs(internal_config, ["detection"])
        finally:
            registry.unregister("unconfigurable_probe")
        assert set(configs) == {"detection_config"}
