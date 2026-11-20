# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``method_line``: what each module says of itself in the run header.

A module with a method names it and its key choices in one short line, read off
its own built config; a module without one has no line.
"""

import pytest

from adapt.modules.analysis.node import AnalysisModule
from adapt.modules.detection.node import DetectModule
from adapt.modules.ingest.node import LoadModule
from adapt.modules.projection.node import ProjectionModule
from adapt.modules.tracking.node import TrackingModule

pytestmark = pytest.mark.unit


def _with(config, **values):
    """``config`` with ``values`` changed, validated by its own model."""
    return type(config).model_validate({**config.model_dump(), **values})


def test_ingest_names_the_regridding_weights_and_radius_of_influence(ingest_module_config):
    assert LoadModule.method_line(ingest_module_config) == "regrid cressman · roi dist_beam"


def test_ingest_line_follows_its_config(ingest_module_config):
    config = _with(ingest_module_config, weighting_function="barnes", roi_func="dist")

    assert LoadModule.method_line(config) == "regrid barnes · roi dist"


def test_detection_names_its_method_and_smallest_cell(detection_module_config):
    assert DetectModule.method_line(detection_module_config) == "threshold · min_cellsize 5"


def test_detection_line_follows_its_config(detection_module_config):
    config = _with(detection_module_config, min_cellsize_gridpoint=12)

    assert DetectModule.method_line(config) == "threshold · min_cellsize 12"


def test_projection_names_its_method_horizon_and_steps(projection_module_config):
    assert (
        ProjectionModule.method_line(projection_module_config)
        == "adapt_default · horizon 15min · steps 3"
    )


def test_projection_line_follows_its_config(projection_module_config):
    config = _with(projection_module_config, projection_horizon_minutes=20, max_projection_steps=4)

    assert ProjectionModule.method_line(config) == "adapt_default · horizon 20min · steps 4"


def test_tracking_names_its_gap_and_link_limits(tracking_module_config):
    assert (
        TrackingModule.method_line(tracking_module_config)
        == "gap 10min · u≤0.6 · c≤1.8 · vmax 40m/s"
    )


def test_tracking_line_shows_the_gap_tracking_itself_uses(tracking_module_config):
    config = _with(tracking_module_config, max_tracking_gap_minutes=7.5)

    assert TrackingModule.method_line(config).startswith("gap 7.5min · ")


def test_a_module_without_a_method_has_no_line(analysis_module_config):
    assert AnalysisModule.method_line(analysis_module_config) is None
