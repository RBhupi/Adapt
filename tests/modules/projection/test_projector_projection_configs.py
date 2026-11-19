import numpy as np
import pytest

from adapt.modules.projection import module as projection_module
from adapt.modules.projection.module import RadarCellProjector

pytestmark = pytest.mark.unit


def test_projection_frames_follow_the_configured_steps(
    simple_labeled_ds_pair, make_projection_config
):
    """The projector writes one registration frame plus one frame per configured step,
    up to the schema's maximum of 10 (a larger value is refused by the configuration)."""
    config = make_projection_config(max_projection_steps=10)
    proj = RadarCellProjector(config)

    out = proj.project(simple_labeled_ds_pair)

    assert out["cell_projections"].shape[0] == 11  # 1 + 10


def test_custom_flow_params_do_not_crash(simple_labeled_ds_pair, make_projection_config):
    """Custom flow parameters are accepted without crashing."""
    config = make_projection_config(
        flow_params={
            "pyr_scale": 0.3,
            "levels": 2,
            "winsize": 5,
            "iterations": 2,
            "poly_n": 5,
            "poly_sigma": 1.1,
            "flags": 0,
        }
    )
    proj = RadarCellProjector(config)

    out = proj.project(simple_labeled_ds_pair)

    assert "cell_projections" in out


def test_optical_flow_is_computed_with_the_configured_flow_parameters(
    monkeypatch, simple_labeled_ds_pair, make_projection_config
):
    """OpenCV's Farneback call gets the seven flow parameters of the options, by name."""
    handed = {}

    def fake_flow(prev, curr, _none, **flow_params):
        handed.update(flow_params)
        flow = np.zeros((*prev.shape, 2), dtype=np.float32)
        flow[:, :, 0] = 6.0
        return flow

    monkeypatch.setattr(projection_module.cv2, "calcOpticalFlowFarneback", fake_flow)
    config = make_projection_config(flow_params={"winsize": 21, "poly_n": 5})

    RadarCellProjector(config).project(simple_labeled_ds_pair)

    assert handed == {
        "pyr_scale": 0.5,
        "levels": 3,
        "winsize": 21,
        "iterations": 3,
        "poly_n": 5,
        "poly_sigma": 1.5,
        "flags": 0,
    }
