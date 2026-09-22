# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The split and merge tests normalise by different areas.

Before these were separated, both divided the intersection by the *projected
hull* area. For a merge that is the dissipating cell's own hull, so the fraction
can reach 1.0. For a split the hull belongs to the continuing parent while the
tested mask is a born fragment, so the fraction is bounded above by
child area / parent hull area — a split could not pass a high threshold no
matter how cleanly it split. These tests pin both denominators.
"""

import numpy as np
import pytest

from adapt.modules.tracking.module import CellTracker

pytestmark = pytest.mark.unit


@pytest.fixture
def parent_and_child():
    """A 40 px parent hull and a 10 px born fragment fully inside it."""
    proj = np.zeros((10, 10), dtype=np.int32)
    proj[0:4, 0:10] = 1  # parent hull, 40 px
    child = np.zeros((10, 10), dtype=bool)
    child[0:1, 0:10] = True  # 10 px, entirely within the hull
    return proj, child


def test_hull_and_cell_fractions_use_different_denominators(parent_and_child):
    proj, child = parent_and_child
    inter, hull_px, tested_px = CellTracker._overlap_areas(proj, 1, child)
    assert (inter, hull_px, tested_px) == (10, 40, 10)
    # merge normalisation: how much of the hull was covered
    assert CellTracker._hull_overlap_fraction(proj, 1, child) == pytest.approx(0.25)
    # split normalisation: how much of the born cell the parent explains
    assert CellTracker._cell_overlap_fraction(proj, 1, child) == pytest.approx(1.0)


def test_a_perfectly_explained_child_fails_the_hull_normalisation(parent_and_child):
    """The exact failure that made splits undetectable: a child wholly inside
    its parent's hull still scores only 0.25 when normalised by the hull."""
    proj, child = parent_and_child
    assert CellTracker._hull_overlap_fraction(proj, 1, child) < 0.7
    assert CellTracker._cell_overlap_fraction(proj, 1, child) >= 0.7


def test_a_wholly_absorbed_cell_reaches_one_on_the_merge_normalisation():
    """The merge case is unaffected: a small cell absorbed by a big one
    covers all of its own hull."""
    proj = np.zeros((10, 10), dtype=np.int32)
    proj[0:2, 0:2] = 1  # dissipating cell's hull, 4 px
    survivor = np.zeros((10, 10), dtype=bool)
    survivor[0:6, 0:6] = True  # the large continuing cell
    assert CellTracker._hull_overlap_fraction(proj, 1, survivor) == pytest.approx(1.0)


def test_empty_hull_and_empty_mask_are_zero_not_an_error():
    empty = np.zeros((4, 4), dtype=np.int32)
    mask = np.ones((4, 4), dtype=bool)
    assert CellTracker._hull_overlap_fraction(empty, 1, mask) == 0.0
    assert CellTracker._cell_overlap_fraction(empty, 1, np.zeros((4, 4), dtype=bool)) == 0.0
