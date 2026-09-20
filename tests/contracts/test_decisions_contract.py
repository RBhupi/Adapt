# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Decision-record contracts: bundles are internally consistent and frozen."""

import dataclasses

import pytest

from adapt.contracts import (
    ContractViolation,
    SegmentationDecisions,
    SegmentationFrame,
    TrackingDecisions,
    TrackingFrame,
    check_segmentation_decisions,
    check_tracking_decisions,
)

pytestmark = pytest.mark.unit


def _tracking_frame(**kw) -> TrackingFrame:
    fields = dict.fromkeys(
        (
            "n_prev",
            "n_curr",
            "n_pairs",
            "n_pass_overlap",
            "n_pass_kinematic",
            "n_propagated",
            "n_hungarian",
            "n_split",
            "n_merge",
            "n_initiation",
            "n_termination",
        ),
        0,
    )
    return TrackingFrame(dt_s=None, reset_code="FIRST_SCAN", **{**fields, **kw})


def test_tracking_bundle_with_no_pairs_is_valid():
    check_tracking_decisions(TrackingDecisions(_tracking_frame(), (), (), ()))


def test_tracking_candidate_rows_must_match_generated_pair_count():
    with pytest.raises(ContractViolation):
        check_tracking_decisions(TrackingDecisions(_tracking_frame(n_pairs=1), (), (), ()))


def test_segmentation_component_rows_must_match_component_count():
    frame = SegmentationFrame(0, 0, 1, 0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(ContractViolation):
        check_segmentation_decisions(SegmentationDecisions(frame, (), ()))


def test_records_are_frozen():
    frame = _tracking_frame()
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.n_prev = 3  # type: ignore[misc]
