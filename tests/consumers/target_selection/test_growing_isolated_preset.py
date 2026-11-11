# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Trend fields, the origin flag, extra priority weights, and the growing-isolated preset."""

from importlib.resources import files

import pytest
import yaml

from adapt.api.store_client import StoreClient
from adapt.consumers.target_selection.config import TSEConfig
from adapt.consumers.target_selection.repository_source import build_snapshot
from adapt.consumers.target_selection.rules import is_candidate, priority_score
from tests.consumers.target_selection.factories import make_cell, make_config
from tests.consumers.target_selection.test_repository_source import (
    COLLECTION,
    RUN_ID,
    _build_store,
    _cell,
)

pytestmark = pytest.mark.unit

PRESET = files("adapt.consumers.target_selection") / "presets" / "growing_isolated.yaml"


def _preset() -> TSEConfig:
    return TSEConfig.model_validate(yaml.safe_load(PRESET.read_text("utf-8"))["target_selection"])


@pytest.fixture
def client(tmp_path):
    c = StoreClient(_build_store(tmp_path))
    yield c
    c.close()


def test_preset_loads_and_is_frozen():
    cfg = _preset()
    assert cfg.snapshot.trend_fields == ("area_40dbz_km2", "radar_reflectivity_max")
    assert cfg.score.weights["n_adjacent_cells"] < 0  # isolation is a preference, not a gate
    assert {g.field for g in cfg.entry.gates} >= {
        "origin_is_split",
        "trend_area_40dbz_km2_per_min",
    }


def test_trend_fields_added_to_values(client):
    snap = build_snapshot(
        client,
        RUN_ID,
        COLLECTION,
        growth_window_scans=4,
        trend_fields=("radar_reflectivity_max", "cell_area_sqkm"),
    )
    beta = _cell(snap, "uid_beta")
    # (55 - 48.1) dBZ over 120 min; area trend equals the legacy growth rate
    assert beta.values["trend_radar_reflectivity_max_per_min"] == pytest.approx(6.9 / 120)
    assert beta.values["trend_cell_area_sqkm_per_min"] == pytest.approx(
        beta.growth_rate_sqkm_per_min
    )
    # a cell seen once has no measurable trend, and it is 0.0, not NaN
    assert _cell(snap, "uid_gamma").values["trend_radar_reflectivity_max_per_min"] == 0.0


def test_unknown_trend_field_raises(client):
    with pytest.raises(ValueError, match="trend_fields"):
        build_snapshot(
            client, RUN_ID, COLLECTION, growth_window_scans=4, trend_fields=("no_such_column",)
        )


def test_origin_flag_present_and_numeric(client):
    snap = build_snapshot(client, RUN_ID, COLLECTION, growth_window_scans=4)
    for cell in snap.cells:
        assert cell.values["origin_is_split"] in (0.0, 1.0)


def test_extra_weights_enter_the_score():
    cell = make_cell(
        refl=50.0,
        area=200.0,
        growth=1.0,
        values={"n_scans": 2.0, "trend_area_40dbz_km2_per_min": 0.5, "n_adjacent_cells": 2.0},
    )
    weights = {"trend_area_40dbz_km2_per_min": 10.0, "n_adjacent_cells": -10.0}
    assert priority_score(cell, weights) == pytest.approx(5.0 - 20.0)


def test_weight_on_missing_value_raises():
    cell = make_cell(values={"n_scans": 2.0})
    with pytest.raises(ValueError, match="absent"):
        priority_score(cell, {"absent": 1.0})


def test_preset_gates_select_a_growing_initiated_cell():
    cfg = _preset()
    base = {
        "n_scans": 2.0,
        "origin_is_split": 0.0,
        "trend_area_40dbz_km2_per_min": 0.4,
        "trend_radar_reflectivity_max_per_min": 0.6,
        "radar_reflectivity_max": 44.0,
        "radar_differential_reflectivity_max": 1.8,
        "n_adjacent_cells": 0.0,
    }
    assert is_candidate(make_cell(age=300.0, values=base), cfg.entry)
    assert not is_candidate(
        make_cell(age=300.0, values={**base, "origin_is_split": 1.0}), cfg.entry
    )
    assert not is_candidate(
        make_cell(age=300.0, values={**base, "trend_area_40dbz_km2_per_min": 0.0}), cfg.entry
    )
    assert not is_candidate(make_cell(age=300.0, values={**base, "n_scans": 5.0}), cfg.entry)
    # isolation is a preference: a neighbour lowers the score but keeps the cell eligible
    crowded = make_cell(age=300.0, values={**base, "n_adjacent_cells": 1.0})
    assert is_candidate(crowded, cfg.entry)
    assert priority_score(crowded, cfg.score.weights) < priority_score(
        make_cell(age=300.0, values=base), cfg.score.weights
    )


def test_hold_gates_keep_a_target_that_left_the_entry_set():
    from datetime import UTC, datetime, timedelta

    from adapt.consumers.target_selection.engine import TargetSelectionEngine
    from adapt.consumers.target_selection.snapshot import Snapshot

    cfg = make_config(
        gates=({"field": "growing", "op": "gt", "value": 0.0},),
        min_age_seconds=0.0,
        hold_gates=({"field": "core", "op": "gt", "value": 0.0},),
        max_observation_seconds=3600.0,
    )
    t0 = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
    eng = TargetSelectionEngine(cfg)
    young = make_cell("a", age=300.0, values={"growing": 1.0, "core": 5.0})
    assert eng.decide(Snapshot(t0, (young,))).selection.cell_uid == "a"
    # next scan: no longer growing (fails entry) but still has a core (passes hold) -> continue
    held = make_cell("a", age=600.0, values={"growing": -1.0, "core": 3.0})
    sel = eng.decide(Snapshot(t0 + timedelta(minutes=5), (held,))).selection
    assert sel is not None and sel.cell_uid == "a" and sel.reason.value == "CONTINUATION"
    # core gone -> stop, and with no other candidate, no selection
    dead = make_cell("a", age=900.0, values={"growing": -1.0, "core": 0.0})
    assert eng.decide(Snapshot(t0 + timedelta(minutes=10), (dead,))).selection is None


def test_entry_gates_as_hold_gates_govern_the_stop():
    from datetime import UTC, datetime, timedelta

    from adapt.consumers.target_selection.engine import TargetSelectionEngine
    from adapt.consumers.target_selection.snapshot import Snapshot

    cfg = make_config(gates=({"field": "growing", "op": "gt", "value": 0.0},), min_age_seconds=0.0)
    t0 = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
    eng = TargetSelectionEngine(cfg)
    assert (
        eng.decide(
            Snapshot(t0, (make_cell("a", age=300.0, values={"growing": 1.0}),))
        ).selection.cell_uid
        == "a"
    )
    assert (
        eng.decide(
            Snapshot(
                t0 + timedelta(minutes=5), (make_cell("a", age=600.0, values={"growing": -1.0}),)
            )
        ).selection
        is None
    )


def test_allow_switch_false_keeps_the_target_against_a_better_challenger():
    from datetime import UTC, datetime, timedelta

    from adapt.consumers.target_selection.engine import TargetSelectionEngine
    from adapt.consumers.target_selection.snapshot import Snapshot

    cfg = make_config(gates=(), min_age_seconds=0.0, allow_switch=False, switch_margin=0.0)
    t0 = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
    eng = TargetSelectionEngine(cfg)
    assert eng.decide(Snapshot(t0, (make_cell("a", refl=40.0),))).selection.cell_uid == "a"
    sel = eng.decide(
        Snapshot(t0 + timedelta(minutes=5), (make_cell("a", refl=40.0), make_cell("b", refl=60.0)))
    ).selection
    assert sel.cell_uid == "a" and sel.reason.value == "CONTINUATION"
