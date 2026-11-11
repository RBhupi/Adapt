# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Every scan yields a Decision: what was chosen, what ended, and the ranked field.

Scores under the factory config (weights 1.0 / 0.05 / 2.0):
cell_a: refl 50, area 200, growth 1.0 -> 62.0
cell_b: refl 40, area 100, growth 0.0 -> 45.0
"""

from datetime import UTC, datetime

import pytest

from adapt.modules.target_selection.engine import TargetSelectionEngine
from adapt.modules.target_selection.selection import RankedCandidate, StopReason
from adapt.modules.target_selection.snapshot import Snapshot
from tests.modules.target_selection.factories import make_cell, make_config

pytestmark = pytest.mark.unit

T0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
T5 = datetime(2024, 6, 1, 12, 5, tzinfo=UTC)


def _a(**kw):
    return make_cell("cell_a", refl=50.0, area=200.0, growth=1.0, **kw)


def _b(**kw):
    return make_cell("cell_b", refl=40.0, area=100.0, growth=0.0, **kw)


def test_a_scan_without_candidates_is_a_decision_with_no_target():
    decision = TargetSelectionEngine(make_config()).decide(Snapshot(T0, (_a(age=100.0),)))
    assert decision.scan_time == T0
    assert decision.selection is None
    assert decision.stop_reason is None
    assert decision.previous_cell_uid is None
    assert decision.candidates == ()


def test_candidates_are_ranked_best_first_with_their_scores():
    decision = TargetSelectionEngine(make_config()).decide(Snapshot(T0, (_b(), _a())))
    assert decision.candidates == (
        RankedCandidate("cell_a", 62.0),
        RankedCandidate("cell_b", 45.0),
    )


def test_losing_the_target_is_recorded_with_its_reason_and_cell():
    engine = TargetSelectionEngine(make_config())
    engine.decide(Snapshot(T0, (_a(),)))
    decision = engine.decide(Snapshot(T5, ()))
    assert decision.selection is None
    assert decision.stop_reason is StopReason.TARGET_LOST
    assert decision.previous_cell_uid == "cell_a"


def test_a_continued_target_names_itself_as_previous_and_has_no_stop():
    engine = TargetSelectionEngine(make_config())
    engine.decide(Snapshot(T0, (_a(),)))
    decision = engine.decide(Snapshot(T5, (_a(),)))
    assert decision.selection.cell_uid == "cell_a"
    assert decision.previous_cell_uid == "cell_a"
    assert decision.stop_reason is None
