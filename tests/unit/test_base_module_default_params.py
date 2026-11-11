# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for BaseModule.default_params.

A module's owned, user-tunable defaults are sourced from its config_class field
defaults, excluding any field listed in injected_global_fields (those are filled
from the shared global section by build_config). These feed `adapt config --show`,
so a new module's params appear automatically.
"""

import pytest

pytestmark = pytest.mark.unit

from pydantic import BaseModel, Field  # noqa: E402

from adapt.modules.base import BaseModule  # noqa: E402


class _ParamCfg(BaseModel):
    threshold: float = Field(30.0, description="dBZ threshold")
    gap_tolerance_m: float = Field(500.0, description="vertical gap bridged (m)")
    reflectivity_var: str = "reflectivity"  # injected from global


class _OwnedModule(BaseModule):
    name = "owned"
    config_class = _ParamCfg
    injected_global_fields = frozenset({"reflectivity_var"})

    def run(self, context: dict) -> dict:
        return {}


class _NoConfigModule(BaseModule):
    name = "no_config"

    def run(self, context: dict) -> dict:
        return {}


class TestDefaultParams:
    def test_returns_owned_defaults(self):
        assert _OwnedModule.default_params() == {
            "threshold": 30.0,
            "gap_tolerance_m": 500.0,
        }

    def test_excludes_injected_global_fields(self):
        assert "reflectivity_var" not in _OwnedModule.default_params()

    def test_empty_without_config_class(self):
        assert _NoConfigModule.default_params() == {}

    def test_default_injected_set_is_empty(self):
        assert BaseModule.injected_global_fields == frozenset()
