# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Unit tests for _timeseries pure helper functions."""

import pandas as pd
import pytest

pytestmark = pytest.mark.unit

pytest.importorskip("matplotlib", reason="matplotlib not installed")


@pytest.fixture()
def fig_and_axes():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6, 8))
    FigureCanvasAgg(fig)  # attach a non-interactive renderer
    axes = fig.subplots(3, 1)
    yield fig, axes


def test_style_ts_ax_enables_grid(fig_and_axes):
    """style_ts_ax must turn on the axis grid."""
    from adapt.consumers.live._timeseries import style_ts_ax

    _, axes = fig_and_axes
    ax = axes[0]
    ax.set_axisbelow(False)  # ensure grid is off before call
    style_ts_ax(ax, "km²", "Area")
    assert ax.yaxis.get_gridlines(), "grid lines should be visible after style_ts_ax"


def test_style_ts_ax_sets_title(fig_and_axes):
    """style_ts_ax must set the axis title."""
    from adapt.consumers.live._timeseries import style_ts_ax

    _, axes = fig_and_axes
    ax = axes[0]
    style_ts_ax(ax, "km²", "My Title")
    assert ax.get_title() == "My Title"


def test_clear_time_series_removes_lines(fig_and_axes):
    """clear_time_series must clear all lines from every axis."""
    from adapt.consumers.live._timeseries import clear_time_series

    _, axes = fig_and_axes
    axes[0].plot([1, 2], [3, 4])
    axes[1].plot([1, 2], [5, 6])
    clear_time_series(tuple(axes))
    for ax in axes:
        assert len(ax.lines) == 0, (
            f"axis should have no lines after clear_time_series, got {list(ax.lines)}"
        )


def test_update_track_legend_adds_legend_to_figure(fig_and_axes):
    """update_track_legend must add a figure-level legend when cells are selected."""
    from adapt.consumers.live._timeseries import update_track_legend

    fig, _ = fig_and_axes
    selected_cells = {"abcd1234": 0}
    color_slots = ["#e41a1c", "#377eb8"]
    update_track_legend(fig, selected_cells, color_slots)
    assert fig.legends, "figure should have at least one legend after update_track_legend"


def test_update_track_legend_clears_legend_when_no_cells(fig_and_axes):
    """update_track_legend must remove existing legends when selected_cells is empty."""
    import matplotlib.patches as mpatches

    from adapt.consumers.live._timeseries import update_track_legend

    fig, _ = fig_and_axes
    # Add a legend manually first
    fig.legend(handles=[mpatches.Patch(label="test")], loc="upper right")
    assert fig.legends  # sanity check

    update_track_legend(fig, {}, ["#e41a1c"])
    assert not fig.legends, "all legends should be removed when selected_cells is empty"


def test_style_ts_ax_horizontal_grid_only(fig_and_axes):
    """style_ts_ax must show horizontal (y) grid lines only — no vertical (x) lines."""
    from adapt.consumers.live._timeseries import style_ts_ax

    fig, axes = fig_and_axes
    ax = axes[0]
    style_ts_ax(ax, "km²", "Area")
    fig.canvas.draw()

    x_grid_visible = any(ln.get_visible() for ln in ax.get_xgridlines())
    y_grid_visible = any(ln.get_visible() for ln in ax.get_ygridlines())
    assert not x_grid_visible, "vertical (x) grid lines must be off"
    assert y_grid_visible, "horizontal (y) grid lines must be on"


def test_draw_scan_marker_adds_vline_to_all_axes(fig_and_axes):
    """draw_scan_marker must add a vertical line to every axis."""
    import pandas as pd

    from adapt.consumers.live._timeseries import draw_scan_marker

    fig, axes = fig_and_axes
    cur_t = pd.Timestamp("2026-06-04 14:00:00", tz="UTC")
    # Give each axis some data so axes have a valid date range
    for ax in axes:
        ax.plot([cur_t], [1.0])
    draw_scan_marker(tuple(axes), cur_t)
    fig.canvas.draw()

    for ax in axes:
        vlines = [
            ln
            for ln in ax.get_lines()
            if ln.get_linewidth() == 1.0 and ln.get_linestyle() != "None"
        ]
        assert vlines, f"expected a scan-time vertical line on axis, got lines: {ax.get_lines()}"


def test_draw_scan_marker_noop_when_cur_t_is_none(fig_and_axes):
    """draw_scan_marker must be a no-op and not raise when cur_t is None."""
    from adapt.consumers.live._timeseries import draw_scan_marker

    _, axes = fig_and_axes
    before = [len(ax.get_lines()) for ax in axes]
    draw_scan_marker(tuple(axes), None)
    after = [len(ax.get_lines()) for ax in axes]
    assert before == after, "no lines should be added when cur_t is None"


def test_build_ts_title_encodes_group_name_and_styles():
    """build_ts_title must include the group name and each style+label pair."""
    from adapt.consumers.live._timeseries import build_ts_title

    group = {
        "styles": ["solid", "dashed"],
        "labels": ["Cell area (km²)", "Core area (km²)"],
    }
    title = build_ts_title("Area", group)
    assert "Area" in title
    assert "Cell area" in title
    assert "Core area" in title


def test_build_ts_title_empty_group_returns_group_name():
    """build_ts_title with no variables must return just the group name."""
    from adapt.consumers.live._timeseries import build_ts_title

    title = build_ts_title("ZDR", {})
    assert title == "ZDR"


class TestHasValues:
    """A variable is drawn for a track only when the track has a value of it."""

    @pytest.fixture
    def has_values(self):
        from adapt.consumers.live._timeseries import has_values

        return has_values

    def test_a_variable_with_a_value_is_drawn(self, has_values):
        track = pd.DataFrame({"cell_top_m": [float("nan"), 9600.0]})

        assert has_values(track, "cell_top_m") is True

    def test_a_variable_without_any_value_is_not_drawn(self, has_values):
        # The column is there (the view carries it) but no scan of the track has it.
        track = pd.DataFrame({"cell_top_m": [float("nan"), None]})

        assert has_values(track, "cell_top_m") is False

    def test_a_variable_the_track_does_not_carry_is_not_drawn(self, has_values):
        track = pd.DataFrame({"cell_area_sqkm": [1.0]})

        assert has_values(track, "cell_top_m") is False


class TestNoDataHint:
    """What an empty plot says: which module to list, when its columns are not there."""

    @pytest.fixture
    def hint(self):
        from adapt.consumers.live._timeseries import no_data_hint

        return no_data_hint

    def test_volume_columns_no_module_wrote_name_the_module(self, hint):
        assert hint(["cell_top_m"], {"cell_area_sqkm"}) == "no data — enable cell_volume_stats"

    def test_volume_columns_without_a_value_are_plain_no_data(self, hint):
        # The module ran (the cells carry the column); this track has no value of it.
        assert hint(["cell_top_m"], {"cell_area_sqkm", "cell_top_m"}) == "no data"

    def test_lightning_columns_that_are_not_there_name_the_module(self, hint):
        assert hint(["flash_count"], {"cell_area_sqkm"}) == (
            "no data — list xlma_stat under modules:"
        )

    def test_any_other_variable_is_plain_no_data(self, hint):
        assert hint(["cell_area_sqkm"], set()) == "no data"
