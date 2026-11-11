"""Test RadarCellSegmenter morphological operations."""

import pytest

from adapt.modules.detection.module import RadarCellSegmenter

pytestmark = pytest.mark.unit


def test_close_cells_without_closing(close_cells_ds, make_detection_config):
    """Without morphological closing, nearby cells remain separate."""
    from adapt.configuration.schemas.user import UserSegmenterConfig

    config = make_detection_config(
        threshold=30, segmenter=UserSegmenterConfig(filter_by_size=False)
    )
    seg = RadarCellSegmenter(config)

    labels = seg.segment(close_cells_ds)["cell_labels"].values

    # Two cells separated by gap should remain separate
    assert labels.max() == 2


def test_close_cells_with_closing(close_cells_ds, make_detection_config):
    """Closing fills the gap but maxtree still resolves two distinct intensity peaks."""
    from adapt.configuration.schemas.user import UserSegmenterConfig

    config = make_detection_config(
        threshold=30,
        segmenter=UserSegmenterConfig(filter_by_size=False, closing_radius=1),
    )
    seg = RadarCellSegmenter(config)

    labels = seg.segment(close_cells_ds)["cell_labels"].values

    # Closing merges the binary mask, but the two reflectivity peaks remain
    # distinct (separated by a 0-value gap in the original field), so maxtree
    # seeds two watershed regions and correctly labels them as two cells.
    assert labels.max() == 2


def _block_with_hole(hole_value: float):
    """One 40 dBZ block with a 48 dBZ core and a one-pixel hole that closing fills."""
    import numpy as np
    import xarray as xr

    field = np.full((20, 20), 10.0, dtype=np.float32)
    field[5:15, 5:15] = 40.0
    field[7, 7] = 48.0
    field[10, 10] = hole_value
    return xr.Dataset(
        {"reflectivity": (("y", "x"), field)},
        coords={"y": np.arange(20), "x": np.arange(20)},
    )


def test_a_no_echo_pixel_closed_into_the_mask_does_not_disturb_the_cells(make_detection_config):
    """Closing can pull a NaN (no-echo) pixel into the mask; it must count as the
    lowest value there, never reach the peak finder as NaN."""
    import numpy as np

    from adapt.configuration.schemas.user import UserSegmenterConfig

    config = make_detection_config(
        threshold=35,
        segmenter=UserSegmenterConfig(filter_by_size=False, closing_radius=2, h_maxima=5.0),
    )

    with_nan = RadarCellSegmenter(config)
    labels_nan = with_nan.segment(_block_with_hole(np.nan))["cell_labels"].values
    labels_zero = RadarCellSegmenter(config).segment(_block_with_hole(0.0))["cell_labels"].values

    assert labels_nan.max() == 1  # one core, one cell
    assert labels_nan[10, 10] == 1  # the filled hole belongs to it
    np.testing.assert_array_equal(labels_nan, labels_zero)
    assert [seed.peak_value for seed in with_nan.decisions().seeds] == [48.0]
