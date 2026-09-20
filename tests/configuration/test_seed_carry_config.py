# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Seed-carry configuration: off by default, bounded, and needs projection."""

import pytest
from pydantic import ValidationError

from adapt.configuration.schemas.param import SegmenterConfig
from adapt.configuration.schemas.user import UserSegmenterConfig
from adapt.execution.nodes.detection import DetectModule

pytestmark = pytest.mark.unit


def test_seed_carry_is_off_by_default():
    seg = SegmenterConfig()
    assert seg.seed_carry is False
    assert seg.seed_carry_max_frames == 2
    assert seg.seed_carry_min_separation == 3


@pytest.mark.parametrize("field", ["seed_carry_max_frames", "seed_carry_min_separation"])
def test_seed_carry_bounds_below_one_are_rejected(field):
    with pytest.raises(ValidationError):
        SegmenterConfig(**{field: 0})


def test_build_config_passes_seed_carry_fields(make_config):
    cfg = make_config(
        segmenter=UserSegmenterConfig(
            seed_carry=True, seed_carry_max_frames=4, seed_carry_min_separation=5
        )
    )
    det = DetectModule.build_config(cfg)
    assert det.seed_carry is True
    assert det.seed_carry_max_frames == 4
    assert det.seed_carry_min_separation == 5


@pytest.mark.parametrize(
    "selection",
    [
        {"exclude_modules": ["projection"]},
        {"only_modules": ["ingest", "detection"]},
        {"modules": ["ingest", "detection", "analysis"]},
    ],
)
def test_build_config_raises_when_seed_carry_is_on_without_projection(make_config, selection):
    cfg = make_config(segmenter=UserSegmenterConfig(seed_carry=True)).model_copy(update=selection)
    with pytest.raises(ValueError, match="projection"):
        DetectModule.build_config(cfg)


def test_build_config_accepts_seed_carry_when_projection_is_enabled(make_config):
    cfg = make_config(segmenter=UserSegmenterConfig(seed_carry=True))
    assert DetectModule.build_config(cfg).seed_carry is True
