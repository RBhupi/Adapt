"""Test RadarCellSegmenter initialization with Pydantic configs."""

import pytest
from pydantic import ValidationError

from adapt.modules.detection.config import DetectionOptions
from adapt.modules.detection.module import RadarCellSegmenter

pytestmark = pytest.mark.unit


def test_threshold_fixture_config(detection_module_config):
    """The threshold test fixture yields a threshold segmenter with expert values."""
    seg = RadarCellSegmenter(detection_module_config)
    assert seg.method == "threshold"
    assert seg.method_params["threshold"] == 35.0
    assert seg.filter_by_size is True


def test_expert_default_method_is_threshold():
    """The unconfigured default segmentation method is threshold.

    Threshold needs only the 2D CAPPI the pipeline always produces, so a plain
    `adapt run nexrad` works with no further setup. The pyart
    convective/stratiform classifiers stay available via `params.detection.method`.
    """
    assert DetectionOptions().method == "threshold"


def test_custom_config(make_detection_config):
    """Segmenter respects user config overrides."""
    config = make_detection_config(threshold=45, min_cellsize_gridpoint=10)

    seg = RadarCellSegmenter(config)
    assert seg.method_params["threshold"] == 45.0
    assert seg.min_gridpoints == 10


def test_unknown_method_raises():
    """Invalid segmentation method fails when the options are validated, not at runtime."""
    with pytest.raises(ValidationError, match="method"):
        DetectionOptions(method="watershed")
