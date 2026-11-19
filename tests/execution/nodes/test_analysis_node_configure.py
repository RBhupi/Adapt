# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""AnalysisModule.configure: the config analysis runs with, from its options and the run."""

from dataclasses import replace

import pytest

from adapt.execution.nodes.analysis import AnalysisModule
from adapt.modules.analysis.config import ALWAYS_EXCLUDED, AnalysisConfig, AnalysisOptions
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_the_modules_options_model_is_analysis_options():
    assert AnalysisModule.options_class is AnalysisOptions


def test_configure_returns_an_analysis_config():
    assert isinstance(AnalysisModule.configure(AnalysisOptions(), SHARED), AnalysisConfig)


def test_configure_keeps_the_variables_and_the_adjacency_threshold():
    options = AnalysisOptions(
        radar_variables=["reflectivity", "velocity"], adjacency_min_touching_boundary_pixels=4
    )

    config = AnalysisModule.configure(options, SHARED)

    assert config.radar_variables == ("reflectivity", "velocity")
    assert config.adjacency_min_touching_boundary_pixels == 4


def test_configure_puts_the_options_exclusions_after_the_modules_own():
    options = AnalysisOptions(exclude_fields=["zeta", ALWAYS_EXCLUDED[0], "alpha", "zeta"])

    config = AnalysisModule.configure(options, SHARED)

    assert config.exclude_fields == (*ALWAYS_EXCLUDED, "zeta", "alpha")


def test_configure_takes_the_field_and_the_level_from_the_runs_shared_values():
    shared = replace(SHARED, tracking_field="velocity", z_level=3500.0)

    config = AnalysisModule.configure(AnalysisOptions(), shared)

    assert config.reflectivity_var == "velocity"
    assert config.z_level == 3500.0


def test_configure_refuses_a_tracking_field_that_gets_no_statistics():
    shared = replace(SHARED, tracking_field="pressure")

    with pytest.raises(ValueError, match=r"params\.analysis\.radar_variables"):
        AnalysisModule.configure(AnalysisOptions(), shared)
