from dataclasses import replace

import pytest

from adapt.execution.nodes.projection import ProjectionModule
from adapt.modules.projection.config import ProjectionOptions
from adapt.modules.projection.module import RadarCellProjector
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit


def test_validate_requires_two_datasets(simple_labeled_ds_pair, make_projection_config):
    """Projector requires at least two datasets."""
    config = make_projection_config()
    proj = RadarCellProjector(config)

    with pytest.raises(ValueError):
        proj.project(simple_labeled_ds_pair[:1])


def test_projection_skipped_if_time_gap_too_large(simple_labeled_ds_pair):
    """Projection skipped when time gap exceeds max interval."""
    one_minute = replace(SHARED, max_scan_gap_minutes=1.0)
    config = ProjectionModule.configure(ProjectionOptions(), one_minute)
    proj = RadarCellProjector(config)

    out = proj.project(simple_labeled_ds_pair)

    # No projections added
    assert "cell_projections" not in out
