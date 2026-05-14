import pytest

from adapt.modules.projection.module import RadarCellProjector

pytestmark = pytest.mark.unit


def test_init_stores_method(make_projection_config):
    """Projector stores method from config."""
    config = make_projection_config()
    proj = RadarCellProjector(config)

    assert proj.method == config.method


def test_init_stores_projection_steps(make_projection_config):
    """Projector stores max_proj_steps from config."""
    config = make_projection_config()
    proj = RadarCellProjector(config)

    assert proj.max_proj_steps == config.max_projection_steps
