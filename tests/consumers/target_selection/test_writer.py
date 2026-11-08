# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Decision outputs — the per-scan JSONL log and the current-selection file."""

import json
import os
from datetime import UTC, datetime

import pytest

from adapt.consumers.target_selection.selection import (
    Decision,
    RankedCandidate,
    ScanDecision,
    SelectionReason,
    StopReason,
    TargetSelection,
)
from adapt.consumers.target_selection.snapshot import TrajectoryPoint
from adapt.consumers.target_selection.writer import append_decision, write_current_selection

pytestmark = pytest.mark.unit

_T = datetime(2024, 6, 1, 14, 0, 7, tzinfo=UTC)


def _selection(reason=SelectionReason.SWITCH):
    return TargetSelection(
        cell_uid="uid_beta",
        reason=reason,
        score=72.0,
        selection_time=_T,
        trajectory=(TrajectoryPoint(lat=35.1, lon=-97.0, lead_seconds=7200.0),),
        observation_window=(_T, datetime(2024, 6, 1, 14, 30, 7, tzinfo=UTC)),
    )


def _scan_decision(selection, *, stop=None, previous=None, candidates=()):
    return ScanDecision(
        collection="KTST",
        run_id="run-1",
        scan_id="5fc3da774452a7d5",
        config_hash="0123456789abcdef",
        decision=Decision(_T, selection, stop, previous, candidates),
    )


_SWITCHED = _scan_decision(
    _selection(),
    previous="uid_alpha",
    candidates=(RankedCandidate("uid_beta", 72.0), RankedCandidate("uid_alpha", 50.0)),
)
_LOST = _scan_decision(None, stop=StopReason.TARGET_LOST, previous="uid_beta")


def _summary(**target):
    return {
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": "5fc3da774452a7d5",
        "scan_time": "2024-06-01T14:00:07Z",
        "config_hash": "0123456789abcdef",
        **target,
    }


def test_log_line_records_the_target_the_change_and_the_ranked_field(tmp_path):
    path = tmp_path / "decisions.jsonl"
    append_decision(path, _SWITCHED)
    assert json.loads(path.read_text(encoding="utf-8")) == _summary(
        cell_uid="uid_beta",
        reason="SWITCH",
        score=72.0,
        stop_reason=None,
        previous_cell_uid="uid_alpha",
        candidates=[
            {"cell_uid": "uid_beta", "score": 72.0},
            {"cell_uid": "uid_alpha", "score": 50.0},
        ],
        trajectory=[{"lat": 35.1, "lon": -97.0, "lead_seconds": 7200.0}],
        observation_window=["2024-06-01T14:00:07Z", "2024-06-01T14:30:07Z"],
    )


def test_a_scan_without_a_target_is_logged_with_why_the_last_one_ended(tmp_path):
    path = tmp_path / "decisions.jsonl"
    append_decision(path, _LOST)
    assert json.loads(path.read_text(encoding="utf-8")) == _summary(
        cell_uid=None,
        reason=None,
        score=None,
        stop_reason="TARGET_LOST",
        previous_cell_uid="uid_beta",
        candidates=[],
        trajectory=[],
        observation_window=None,
    )


def test_log_lines_are_appended_one_per_scan(tmp_path):
    path = tmp_path / "decisions.jsonl"
    append_decision(path, _SWITCHED)
    append_decision(path, _LOST)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_lines_are_separated_by_a_bare_newline(tmp_path):
    """The decision log is a data product — its bytes must not vary by platform.

    Text mode on Windows translates "\\n" to "\\r\\n", so the same run would emit a
    different file there.
    """
    path = tmp_path / "decisions.jsonl"
    append_decision(path, _SWITCHED)
    append_decision(path, _LOST)
    assert b"\r\n" not in path.read_bytes()


def test_missing_parent_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_decision(tmp_path / "no_such_dir" / "decisions.jsonl", _SWITCHED)


def test_current_selection_carries_schema_rules_target_and_change(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _SWITCHED)
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert obj == {
        "schema": 1,
        **_summary(
            cell_uid="uid_beta",
            reason="SWITCH",
            score=72.0,
            stop_reason=None,
            previous_cell_uid="uid_alpha",
        ),
    }


def test_current_selection_without_target_has_null_target_fields(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _LOST)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": 1,
        **_summary(
            cell_uid=None,
            reason=None,
            score=None,
            stop_reason="TARGET_LOST",
            previous_cell_uid="uid_beta",
        ),
    }


def test_current_selection_overwrite_replaces_previous_content(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _SWITCHED)
    write_current_selection(path, _LOST)
    assert json.loads(path.read_text(encoding="utf-8"))["cell_uid"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current_selection.json"]


def test_failed_overwrite_keeps_previous_selection(tmp_path, monkeypatch):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _SWITCHED)

    def _fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _fail)
    with pytest.raises(OSError, match="disk full"):
        write_current_selection(path, _LOST)
    assert json.loads(path.read_text(encoding="utf-8"))["cell_uid"] == "uid_beta"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current_selection.json"]
