# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""TargetSelectionModule: the run's cells so far -> one scan's ``selection`` row."""

import json
from dataclasses import replace

import pandas as pd
import pytest
import yaml

from adapt.configuration.schemas.module_resolver import module_options
from adapt.contracts import check_selection
from adapt.execution.nodes.target_selection import TargetSelectionModule, TargetSelectionRun
from adapt.modules.target_selection import TSEConfig, config_hash
from adapt.utils.time import from_scan_iso
from tests.helpers.pipeline_files import TARGET_SELECTION
from tests.helpers.shared_config import SHARED

pytestmark = pytest.mark.unit

_T = ["2026-06-01T12:00:00Z", "2026-06-01T12:05:00Z", "2026-06-01T12:10:00Z"]
_RULES = TSEConfig.model_validate(TARGET_SELECTION)  # any cell with a 45 dBZ core
_CONFIG = TargetSelectionRun(rules=_RULES, tracking_field="reflectivity")


def _cells(*rows) -> pd.DataFrame:
    """cells_by_scan rows: (scan index, uid, max dBZ, area km2)."""
    return pd.DataFrame(
        [
            {
                "scan_time": _T[scan],
                "cell_uid": uid,
                "radar_reflectivity_max": dbz,
                "cell_area_sqkm": area,
                "cell_centroid_mass_lat": 40.0,
                "cell_centroid_mass_lon": -88.0,
                "age_seconds": 300.0 * scan,
            }
            for scan, uid, dbz, area in rows
        ]
    )


class _Scans:
    """Scan after scan through a new module each time, as the runtime does:
    what the module returns for its next scan is handed back to it."""

    def __init__(self) -> None:
        self._state = None

    def decide(self, cells: pd.DataFrame, scan: int) -> pd.Series:
        result = TargetSelectionModule().run(
            {
                "target_selection_config": _CONFIG,
                "cell_identity": pd.DataFrame(),
                "cells_so_far": cells,
                "tracks_so_far": pd.DataFrame({"cell_uid": cells["cell_uid"].unique()}),
                "scan_time": from_scan_iso(_T[scan]),
                "target_selection_state": self._state,
            }
        )
        check_selection(result["selection"])
        self._state = result["target_selection_state"]
        return result["selection"].iloc[0]


def test_the_best_candidate_is_selected_and_then_kept():
    scans = _Scans()
    cells = _cells((0, "weak", 40.0, 10.0), (0, "strong", 50.0, 10.0), (1, "strong", 51.0, 12.0))

    first, second = scans.decide(cells, 0), scans.decide(cells, 1)

    assert (first["cell_uid"], first["reason"]) == ("strong", "NEW_TARGET")
    assert (second["cell_uid"], second["reason"]) == ("strong", "CONTINUATION")
    assert second["previous_cell_uid"] == "strong"
    assert [c["cell_uid"] for c in json.loads(first["candidates_json"])] == ["strong"]
    assert first["config_hash"] == config_hash(_RULES)


def test_no_cell_passing_the_gates_selects_none():
    row = _Scans().decide(_cells((0, "weak", 40.0, 10.0)), 0)

    assert row["cell_uid"] is None and row["reason"] is None
    assert json.loads(row["candidates_json"]) == []


def test_a_scan_without_cells_loses_the_target():
    scans = _Scans()
    cells = _cells((0, "strong", 50.0, 10.0))
    scans.decide(cells, 0)

    row = scans.decide(cells, 1)  # nothing was tracked at the second scan

    assert row["cell_uid"] is None
    assert (row["previous_cell_uid"], row["stop_reason"]) == ("strong", "TARGET_LOST")


def test_only_the_scans_up_to_this_one_count():
    """Growth is fitted on what was known at the scan, not on what came after."""
    cells = _cells((0, "a", 50.0, 10.0), (1, "a", 50.0, 20.0), (2, "a", 50.0, 90.0))

    at_second = _Scans().decide(cells[cells["scan_time"] <= _T[1]], 1)
    with_the_future_stored = _Scans().decide(cells, 1)

    assert at_second["score"] == with_the_future_stored["score"]


def test_the_packaged_rule_is_the_default_and_parts_are_laid_over_it():
    rules = module_options("target_selection", {"duration": {"max_minutes": 15}}, source="a test")

    run = TargetSelectionModule.configure(rules, replace(SHARED, tracking_field="velocity"))

    packaged = yaml.safe_load(TargetSelectionModule.options_preset())["target_selection"]
    assert run.rules.duration.max_minutes == 15
    assert run.rules.switch.allow is packaged["switch"]["allow"]
    assert run.tracking_field == "velocity"
    assert "decided at the 2nd observation" in TargetSelectionModule.options_preset()


def test_decisions_under_different_rules_are_stored_side_by_side():
    (spec,) = TargetSelectionModule.persistence

    assert spec.table == "target_selection"
    assert spec.primary_key == ("run_id", "scan_id", "config_hash")
