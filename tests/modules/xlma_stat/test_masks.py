# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""scan_minute_masks: the minute-resolution cell masks one scan owns.

A scan owns the minutes its registration frames cover (previous-scan labels,
registration LUT); where its own time falls on the minute grid, its real
segmentation (fraction 0, its own LUT) replaces the advected frame.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from adapt.modules.xlma_stat.masks import scan_minute_masks
from tests.helpers.analysis_nc import cell_block, make_analysis_ds

pytestmark = pytest.mark.unit

T_0303 = datetime(2024, 5, 18, 19, 3, tzinfo=UTC)
T_0306 = datetime(2024, 5, 18, 19, 6, tzinfo=UTC)


def _first_pair():
    """Scan 19:03 after 19:00: the previous scan was never tracked (no registration LUT)."""
    return make_analysis_ds(
        "2024-05-18T19:03:00",
        "2024-05-18T19:00:00",
        cell_labels=cell_block(col=6),
        cell_uids=["uid-A"],
        minute_labels={
            "2024-05-18T19:01:00": cell_block(col=4),
            "2024-05-18T19:02:00": cell_block(col=5),
            "2024-05-18T19:03:00": cell_block(col=6),
        },
        registration_uids=None,
    )


def _second_pair():
    """Scan 19:06 after 19:03: the previous scan was tracked."""
    return make_analysis_ds(
        "2024-05-18T19:06:00",
        "2024-05-18T19:03:00",
        cell_labels=cell_block(col=9),
        cell_uids=["uid-A"],
        minute_labels={
            "2024-05-18T19:04:00": cell_block(col=7),
            "2024-05-18T19:05:00": cell_block(col=8),
            "2024-05-18T19:06:00": cell_block(col=9),
        },
        registration_uids=["uid-A"],
    )


def _by_minute(masks):
    return {pd.Timestamp(m.minute_time): m for m in masks}


def test_advected_minutes_carry_the_registration_lut():
    mask = _by_minute(scan_minute_masks(_second_pair(), T_0306))[pd.Timestamp("2024-05-18T19:04")]

    assert list(mask.cell_uid_lut.astype(str)) == ["NONE", "uid-A"]
    assert mask.interpolation_fraction == pytest.approx(1 / 3)
    assert pd.Timestamp(mask.source_scan_time) == pd.Timestamp("2024-05-18T19:03:00")
    assert pd.Timestamp(mask.target_scan_time) == pd.Timestamp("2024-05-18T19:06:00")
    np.testing.assert_array_equal(mask.cell_labels, cell_block(col=7))


def test_the_scans_own_minute_uses_its_real_segmentation():
    """At 19:06 the real mask (fraction 0, the scan's own LUT) wins over fraction 1.0."""
    mask = _by_minute(scan_minute_masks(_second_pair(), T_0306))[pd.Timestamp("2024-05-18T19:06")]

    assert mask.interpolation_fraction == 0.0
    np.testing.assert_array_equal(mask.cell_labels, cell_block(col=9))
    assert pd.Timestamp(mask.source_scan_time) == pd.Timestamp("2024-05-18T19:06:00")


def test_minutes_without_a_registration_lut_are_left_out():
    """The previous scan was never tracked: only the scan's own minute remains."""
    minutes = [pd.Timestamp(m.minute_time) for m in scan_minute_masks(_first_pair(), T_0303)]

    assert minutes == [pd.Timestamp("2024-05-18T19:03:00")]


def test_a_scan_owns_its_minutes_in_ascending_order():
    minutes = [pd.Timestamp(m.minute_time) for m in scan_minute_masks(_second_pair(), T_0306)]

    assert minutes == list(pd.date_range("2024-05-18T19:04:00", periods=3, freq="1min"))


def test_a_scan_time_off_the_minute_grid_adds_no_real_mask():
    off_grid = datetime(2024, 5, 18, 19, 6, 37, tzinfo=UTC)

    fractions = [m.interpolation_fraction for m in scan_minute_masks(_second_pair(), off_grid)]

    assert 0.0 not in fractions


def test_a_scan_without_cells_has_no_real_mask():
    ds = _second_pair().drop_vars("cell_uid")

    minutes = [pd.Timestamp(m.minute_time) for m in scan_minute_masks(ds, T_0306)]

    assert pd.Timestamp("2024-05-18T19:06:00") in minutes  # the advected frame stays
    assert all(m.interpolation_fraction > 0 for m in scan_minute_masks(ds, T_0306))
