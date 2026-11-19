# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""LoadModule.configure: the config ingest runs with, from its options and the run."""

from dataclasses import replace

import pytest

from adapt.execution.nodes.ingest import LoadModule
from adapt.modules.ingest.config import IngestConfig, IngestOptions
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_the_modules_options_model_is_ingest_options():
    assert LoadModule.options_class is IngestOptions


def test_configure_returns_an_ingest_config():
    assert isinstance(LoadModule.configure(IngestOptions(), SHARED), IngestConfig)


def test_configure_keeps_every_field_under_its_own_name_by_default():
    config = LoadModule.configure(IngestOptions(), SHARED)

    assert config.field_map == {}
    assert config.fields == ()


def test_configure_keeps_the_renames_and_the_selection():
    options = IngestOptions(
        field_map={"corrected_reflectivity": "reflectivity"}, fields=["reflectivity", "velocity"]
    )

    config = LoadModule.configure(options, SHARED)

    assert config.field_map == {"corrected_reflectivity": "reflectivity"}
    assert config.fields == ("reflectivity", "velocity")


def test_configure_keeps_the_grid_and_how_it_is_filled():
    options = IngestOptions(
        grid_shape=(10, 50, 60),
        grid_limits=((0, 10000), (-50000, 50000), (-60000, 60000)),
        roi_func="dist",
        min_radius=2000,
        weighting_function="nearest",
    )

    config = LoadModule.configure(options, SHARED)

    assert config.grid_shape == (10, 50, 60)
    assert config.grid_limits == ((0.0, 10000.0), (-50000.0, 50000.0), (-60000.0, 60000.0))
    assert (config.roi_func, config.min_radius, config.weighting_function) == (
        "dist",
        2000.0,
        "nearest",
    )


def test_configure_takes_the_level_from_the_runs_shared_values():
    config = LoadModule.configure(IngestOptions(), replace(SHARED, z_level=3500.0))

    assert config.z_level == 3500.0


def test_configure_refuses_a_selection_that_drops_the_tracking_field():
    options = IngestOptions(fields=["velocity"])

    with pytest.raises(ValueError, match=r"excluded by params\.ingest\.fields \['velocity'\]"):
        LoadModule.configure(options, SHARED)


def test_configure_accepts_a_selection_that_keeps_the_tracking_field():
    shared = replace(SHARED, tracking_field="velocity")

    config = LoadModule.configure(IngestOptions(fields=["velocity"]), shared)

    assert config.fields == ("velocity",)
