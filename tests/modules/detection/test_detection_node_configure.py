# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""DetectModule.configure: the config detection runs with, from its options and the run."""

from dataclasses import replace

import pytest

from adapt.modules.detection.config import DetectionConfig, DetectionOptions
from adapt.modules.detection.node import DetectModule
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_the_modules_options_model_is_detection_options():
    assert DetectModule.options_class is DetectionOptions


def test_configure_returns_a_detection_config():
    assert isinstance(DetectModule.configure(DetectionOptions(), SHARED), DetectionConfig)


def test_configure_keeps_every_option():
    options = DetectionOptions(h_maxima=3.0, closing_radius=0, max_cellsize_gridpoint=400)

    config = DetectModule.configure(options, SHARED)

    assert {name: getattr(config, name) for name in DetectionOptions.model_fields} == {
        name: getattr(options, name) for name in DetectionOptions.model_fields
    }


def test_configure_adds_the_parameters_of_the_selected_method_only():
    options = DetectionOptions(
        method="steiner_conv_strat",
        steiner_conv_strat_params={"intense": 45.0},
        threshold_params={"threshold": 20.0},
    )

    config = DetectModule.configure(options, SHARED)

    assert config.method_params == {
        "intense": 45.0,
        "peak_relation": "default",
        "area_relation": "medium",
        "bkg_rad": 11000.0,
        "use_intense": True,
    }


def test_configure_takes_the_field_and_the_level_from_the_runs_shared_values():
    shared = replace(SHARED, tracking_field="velocity", z_level=3000.0)

    config = DetectModule.configure(DetectionOptions(), shared)

    assert config.reflectivity_var == "velocity"
    assert config.z_level == 3000.0
