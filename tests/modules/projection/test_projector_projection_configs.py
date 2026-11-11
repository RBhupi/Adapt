import pytest

from adapt.modules.projection.module import RadarCellProjector

pytestmark = pytest.mark.unit


def test_projection_frames_follow_the_configured_steps(
    simple_labeled_ds_pair, make_projection_config
):
    """The projector writes one registration frame plus one frame per configured step,
    up to the schema's maximum of 10 (a larger value is refused by the configuration)."""
    from adapt.configuration.schemas.user import UserProjectorConfig

    config = make_projection_config(projector=UserProjectorConfig(max_projection_steps=10))
    proj = RadarCellProjector(config)

    out = proj.project(simple_labeled_ds_pair)

    assert out["cell_projections"].shape[0] == 11  # 1 + 10


def test_custom_flow_params_do_not_crash(simple_labeled_ds_pair, make_projection_config):
    """Custom flow parameters are accepted without crashing."""
    from adapt.configuration.schemas.user import UserProjectorConfig

    config = make_projection_config(
        projector=UserProjectorConfig(
            flow_params={
                "pyr_scale": 0.3,
                "levels": 2,
                "winsize": 5,
                "iterations": 2,
                "poly_n": 3,
                "poly_sigma": 1.1,
                "flags": 0,
            }
        )
    )
    proj = RadarCellProjector(config)

    out = proj.project(simple_labeled_ds_pair)

    assert "cell_projections" in out
