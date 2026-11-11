# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Scoring target-selection rules in hindsight: ideal targets, fates, the oracle, variants.

Synthetic scan outcomes with known answers; the engine itself is tested elsewhere.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from adapt.consumers.target_selection.study import (
    IdealConfig,
    ScanOutcome,
    StudyConfig,
    Variant,
    fates,
    ideal_targets,
    oracle,
    summarize,
    variant_rule,
)
from tests.helpers.pipeline_files import TARGET_SELECTION

pytestmark = pytest.mark.unit

T = [f"2025-06-17T18:{m:02d}:00Z" for m in range(0, 60, 5)]  # 12 scan times, 5 min apart


def _cells(**series):
    """cells_by_scan rows: ``uid=[zmax at each observation]``, observations at T[0], T[1], ..."""
    rows = [
        {"scan_time": T[k], "cell_uid": uid, "radar_reflectivity_max": z}
        for uid, zs in series.items()
        for k, z in enumerate(zs)
    ]
    return pd.DataFrame(rows)


def _tracks(**origin):
    return pd.DataFrame([{"cell_uid": uid, "origin_type": o} for uid, o in origin.items()])


# ── ideal targets ──────────────────────────────────────────────────────────


def test_an_ideal_target_lives_long_gets_strong_and_peaks_late():
    cells = _cells(
        good=[30, 35, 40, 45, 50, 40],  # 6 scans, reaches 45, peak at observation 5
        short=[30, 45, 50],  # too short
        weak=[30, 35, 40, 42, 44, 40],  # never 45 dBZ
        early=[50, 45, 40, 35, 30, 30],  # peak at the first observation
    )
    tracks = _tracks(good="INITIATION", short="INITIATION", weak="INITIATION", early="INITIATION")

    ideal = ideal_targets(cells, tracks, "radar_reflectivity_max", IdealConfig())

    assert list(ideal["cell_uid"]) == ["good"]
    assert ideal.iloc[0]["peak_scan_time"] == T[4]


def test_a_split_child_is_not_an_ideal_target_unless_allowed():
    cells = _cells(child=[30, 35, 40, 45, 50, 40])
    tracks = _tracks(child="SPLIT")

    assert ideal_targets(cells, tracks, "radar_reflectivity_max", IdealConfig()).empty
    allowed = IdealConfig(exclude_split=False)
    assert list(ideal_targets(cells, tracks, "radar_reflectivity_max", allowed)["cell_uid"]) == [
        "child"
    ]


# ── fates ──────────────────────────────────────────────────────────────────


def _outcome(k, cell=None, reason=None, candidates=()):
    return ScanOutcome(T[k], cell, reason, tuple(candidates))


def _life(**uids):
    return {uid: [T[k] for k in ks] for uid, ks in uids.items()}


def _ideal(**peaks):
    return pd.DataFrame([{"cell_uid": uid, "peak_scan_time": T[k]} for uid, k in peaks.items()])


def test_each_ideal_target_gets_one_fate():
    # a: held through its peak. b: held, but released before its peak.
    # c: eligible only while a was held (radar busy). d: eligible when b was chosen over it.
    # e: never eligible.
    outcomes = [
        _outcome(0, "a", "NEW_TARGET", ["a"]),
        _outcome(1, "a", "CONTINUATION", ["a", "c"]),
        _outcome(2, None, None, []),
        _outcome(3, "b", "NEW_TARGET", ["b", "d"]),
        _outcome(4, None, None, []),
    ]
    life = _life(a=[0, 1, 2], b=[3, 4, 5], c=[0, 1, 2], d=[3, 4], e=[0, 1, 2, 3])
    ideal = _ideal(a=1, b=5, c=1, d=4, e=2)

    result = fates(outcomes, ideal, life).set_index("cell_uid")

    assert result.loc["a", "fate"] == "captured_peak"
    assert result.loc["a", "coverage"] == pytest.approx(2 / 3)
    assert result.loc["b", "fate"] == "captured_nopeak"
    assert result.loc["c", "fate"] == "lost_busy"
    assert result.loc["d", "fate"] == "lost_outranked"
    assert result.loc["e", "fate"] == "lost_never"
    assert result.loc["e", "coverage"] == 0.0


def test_summary_counts_fates_episodes_and_false_alarms():
    outcomes = [
        _outcome(0, "a", "NEW_TARGET", ["a"]),
        _outcome(1, "x", "SWITCH", ["a", "x"]),  # x is not an ideal target: a false alarm
        _outcome(2, "x", "CONTINUATION", ["x"]),
    ]
    life = _life(a=[0, 1, 2, 3], x=[1, 2])
    ideal = _ideal(a=0)

    summary = summarize(fates(outcomes, ideal, life), outcomes, set(ideal["cell_uid"]))

    assert summary["captured_peak"] == 1
    assert summary["n_ideal"] == 1
    assert summary["episodes"] == 2
    assert summary["false_alarms"] == 1
    assert summary["coverage_median"] == pytest.approx(0.25)


# ── oracle ─────────────────────────────────────────────────────────────────


def test_the_oracle_takes_the_longest_lived_target_at_its_second_observation():
    # p and q both reach their 2nd observation at T[1]; q lives longer, so the oracle
    # takes q and holds it to its end; p is lost to the busy radar. r starts after.
    life = _life(p=[0, 1, 2, 3], q=[0, 1, 2, 3, 4, 5], r=[6, 7, 8, 9])
    ideal = _ideal(p=2, q=3, r=8)

    summary = oracle(ideal, life)

    assert summary["captured_peak"] == 2
    assert summary["lost_busy"] == 1
    assert summary["false_alarms"] == 0
    assert summary["coverage_median"] == pytest.approx((5 / 6 + 3 / 4) / 2)


# ── variants ───────────────────────────────────────────────────────────────


def test_a_variant_changes_only_the_parts_it_names():
    variant = Variant(name="switch at 20", switch={"allow": True, "margin": 20.0})

    rule = variant_rule(TARGET_SELECTION, variant)

    assert rule.switch.margin == 20.0 and rule.switch.allow is True
    assert rule.duration.max_minutes == TARGET_SELECTION["duration"]["max_minutes"]
    assert [g.field for g in rule.entry.gates] == ["radar_reflectivity_max"]


def test_a_variant_may_replace_a_parts_option_and_keep_the_others():
    variant = Variant(name="no cap", duration={"max_minutes": None})

    assert variant_rule(TARGET_SELECTION, variant).duration.max_minutes is None


def test_a_variant_names_only_the_rules_five_parts_and_snapshot():
    with pytest.raises(ValidationError, match="candidate"):
        Variant(name="old layout", candidate={"gates": []})


def test_the_study_config_defaults_to_the_papers_ideal_target():
    cfg = StudyConfig()

    assert (cfg.ideal.min_scans, cfg.ideal.min_field_max, cfg.ideal.min_peak_observation) == (
        6,
        45.0,
        3,
    )
    assert cfg.ideal.exclude_split is True
    assert cfg.variants == ()
