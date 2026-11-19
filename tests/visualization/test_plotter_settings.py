# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The plotter draws with one fixed set of settings; a run's config has no part in it."""

import pytest

from adapt.visualization.plotter import RadarPlotter

pytestmark = pytest.mark.unit


def test_the_plotter_draws_with_its_fixed_settings():
    plotter = RadarPlotter()

    assert (plotter.dpi, plotter.figsize, plotter.output_format) == (200, (20, 10), "png")
    assert plotter.basemap_alpha == 0.6
    assert (plotter.seg_linewidth, plotter.proj_linewidth, plotter.proj_alpha) == (1.0, 0.8, 0.6)
    assert (plotter.flow_scale, plotter.flow_subsample) == (1.0, 10)
    assert (plotter.min_refl, plotter.vmin, plotter.vmax) == (0, 10, 50)


def test_the_plotter_takes_no_run_config(internal_config):
    with pytest.raises(TypeError, match="config"):
        RadarPlotter(config=internal_config)
