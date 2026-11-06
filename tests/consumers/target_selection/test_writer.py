# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Selection outputs — the append-only JSONL log and the current-selection file."""

import json
import os
from datetime import UTC, datetime

import pytest

from adapt.consumers.target_selection.selection import (
    ScanSelection,
    SelectionReason,
    TargetSelection,
)
from adapt.consumers.target_selection.snapshot import TrajectoryPoint
from adapt.consumers.target_selection.writer import append_selection, write_current_selection

pytestmark = pytest.mark.unit


def _selection(reason=SelectionReason.SWITCH):
    t = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)
    return TargetSelection(
        cell_uid="uid_beta",
        reason=reason,
        score=72.0,
        selection_time=t,
        trajectory=(TrajectoryPoint(lat=35.1, lon=-97.0, lead_seconds=7200.0),),
        observation_window=(t, datetime(2024, 6, 1, 14, 30, tzinfo=UTC)),
    )


def test_appends_jsonl_naming_the_run_and_scan(tmp_path):
    path = tmp_path / "selections.jsonl"
    append_selection(path, _scan_selection(_selection()))
    append_selection(path, _scan_selection(_selection(reason=SelectionReason.CONTINUATION)))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": "5fc3da774452a7d5",
        "cell_uid": "uid_beta",
        "reason": "SWITCH",
        "score": 72.0,
        "selection_time": "2024-06-01T14:00:00Z",
        "trajectory": [{"lat": 35.1, "lon": -97.0, "lead_seconds": 7200.0}],
        "observation_window": ["2024-06-01T14:00:00Z", "2024-06-01T14:30:00Z"],
        "predicted_hulls": None,
    }


def test_lines_are_separated_by_a_bare_newline(tmp_path):
    """The selection log is a data product — its bytes must not vary by platform.

    Text mode on Windows translates "\\n" to "\\r\\n", so the same run would emit a
    different file there.
    """
    path = tmp_path / "selections.jsonl"
    append_selection(path, _scan_selection(_selection()))
    append_selection(path, _scan_selection(_selection()))
    assert b"\r\n" not in path.read_bytes()


def test_logging_a_scan_without_a_selection_raises(tmp_path):
    with pytest.raises(ValueError, match="5fc3da774452a7d5"):
        append_selection(tmp_path / "selections.jsonl", _scan_selection(None))


def test_missing_parent_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_selection(
            tmp_path / "no_such_dir" / "selections.jsonl", _scan_selection(_selection())
        )


def _scan_selection(selection):
    return ScanSelection(
        collection="KTST",
        run_id="run-1",
        scan_id="5fc3da774452a7d5",
        scan_time=datetime(2024, 6, 1, 14, 0, 7, tzinfo=UTC),
        selection=selection,
    )


def test_current_selection_names_scan_and_target(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _scan_selection(_selection()))
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert obj == {
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": "5fc3da774452a7d5",
        "scan_time": "2024-06-01T14:00:07Z",
        "cell_uid": "uid_beta",
        "reason": "SWITCH",
        "score": 72.0,
    }
    assert list(obj) == [
        "collection",
        "run_id",
        "scan_id",
        "scan_time",
        "cell_uid",
        "reason",
        "score",
    ]


def test_current_selection_without_target_has_null_target_fields(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _scan_selection(None))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": "5fc3da774452a7d5",
        "scan_time": "2024-06-01T14:00:07Z",
        "cell_uid": None,
        "reason": None,
        "score": None,
    }


def test_current_selection_overwrite_replaces_previous_content(tmp_path):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _scan_selection(_selection()))
    write_current_selection(path, _scan_selection(None))
    assert json.loads(path.read_text(encoding="utf-8"))["cell_uid"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current_selection.json"]


def test_failed_overwrite_keeps_previous_selection(tmp_path, monkeypatch):
    path = tmp_path / "current_selection.json"
    write_current_selection(path, _scan_selection(_selection()))

    def _fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _fail)
    with pytest.raises(OSError, match="disk full"):
        write_current_selection(path, _scan_selection(None))
    assert json.loads(path.read_text(encoding="utf-8"))["cell_uid"] == "uid_beta"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current_selection.json"]
