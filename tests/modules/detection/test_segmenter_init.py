"""Test RadarCellSegmenter initialization with Pydantic configs."""

import pytest

from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig
from adapt.modules.detection.module import RadarCellSegmenter

pytestmark = pytest.mark.unit


def test_default_config(detection_module_config):
    """Segmenter uses expert defaults when no user overrides provided."""
    seg = RadarCellSegmenter(detection_module_config)
    assert seg.method == "threshold"
    assert seg.threshold == 30.0
    assert seg.filter_by_size is True


def test_custom_config(make_detection_config):
    """Segmenter respects user config overrides."""
    config = make_detection_config(
        threshold=45,
        min_cellsize_gridpoint=10,
        # Note: filter_by_size not exposed in UserConfig yet, uses default
    )

    seg = RadarCellSegmenter(config)
    assert seg.threshold == 45.0
    assert seg.min_gridpoints == 10


def test_unknown_method_raises():
    """Invalid segmentation method fails at config validation time."""
    # Pydantic validation happens at model creation, not at runtime
    # This test verifies the old behavior is no longer needed
    # Invalid methods are caught by Literal["threshold"] in ParamConfig

    with pytest.raises(Exception):  # noqa: B017 — ValidationError from Pydantic
        # Try to create config with invalid method
        param = ParamConfig()
        user = UserConfig(segmentation_method="watershed")  # Invalid
        resolve_config(param, user, None)
