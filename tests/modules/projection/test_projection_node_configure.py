# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""ProjectionModule.configure: the config projection runs with, from its options and the run."""

from dataclasses import replace

import pytest

from adapt.modules.projection.config import ProjectionConfig, ProjectionOptions
from adapt.modules.projection.node import ProjectionModule
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_the_modules_options_model_is_projection_options():
    assert ProjectionModule.options_class is ProjectionOptions


def test_configure_returns_a_projection_config():
    assert isinstance(ProjectionModule.configure(ProjectionOptions(), SHARED), ProjectionConfig)


def test_configure_keeps_every_option():
    options = ProjectionOptions(
        max_projection_steps=7, nan_fill_value=-5.5, flow_params={"winsize": 21, "poly_n": 5}
    )

    config = ProjectionModule.configure(options, SHARED)

    assert {name: getattr(config, name) for name in ProjectionOptions.model_fields} == {
        name: getattr(options, name) for name in ProjectionOptions.model_fields
    }


def test_configure_takes_the_field_and_the_scan_gap_from_the_runs_shared_values():
    shared = replace(SHARED, tracking_field="velocity", max_scan_gap_minutes=7.5)

    config = ProjectionModule.configure(ProjectionOptions(), shared)

    assert config.reflectivity_var == "velocity"
    assert config.max_time_interval_minutes == 7.5
