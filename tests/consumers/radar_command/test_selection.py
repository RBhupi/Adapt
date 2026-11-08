# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Reading the target selection engine's current-selection file."""

import json
from datetime import UTC, datetime

import pytest

from adapt.consumers.radar_command.selection import SelectionRef, read_selection
from adapt.consumers.target_selection.selection import (
    SelectionReason,
    TargetSelection,
)
from adapt.consumers.target_selection.writer import write_current_selection
from tests.consumers.target_selection.factories import make_scan_decision

pytestmark = pytest.mark.unit

_SCAN_TIME = datetime(2024, 6, 1, 14, 0, 37, tzinfo=UTC)


def _write(path, cell_uid="uid_beta"):
    selection = None
    if cell_uid is not None:
        selection = TargetSelection(
            cell_uid=cell_uid,
            reason=SelectionReason.SWITCH,
            score=72.0,
            selection_time=_SCAN_TIME,
            trajectory=(),
            observation_window=(_SCAN_TIME, _SCAN_TIME),
        )
    write_current_selection(
        path, make_scan_decision("KTST", "run_a", "0123456789abcdef", _SCAN_TIME, selection)
    )


def test_reads_the_selection_written_by_target_selection(tmp_path):
    path = tmp_path / "current_selection.json"
    _write(path)

    assert read_selection(path) == SelectionRef(
        collection="KTST",
        run_id="run_a",
        scan_id="0123456789abcdef",
        scan_time=datetime(2024, 6, 1, 14, 0, 37, tzinfo=UTC),
        cell_uid="uid_beta",
        config_hash="0123456789abcdef",
    )


def test_a_selection_file_of_another_schema_raises_naming_both(tmp_path):
    path = tmp_path / "current_selection.json"
    _write(path)
    _edit(path, lambda obj: obj.update(schema=99))

    with pytest.raises(ValueError, match=r"schema 99.*expected 1"):
        read_selection(path)


def test_no_selected_cell_reads_as_a_null_cell_uid(tmp_path):
    path = tmp_path / "current_selection.json"
    _write(path, cell_uid=None)

    assert read_selection(path).cell_uid is None


def _edit(path, change):
    obj = json.loads(path.read_text(encoding="utf-8"))
    change(obj)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_missing_key_raises_naming_the_key(tmp_path):
    path = tmp_path / "current_selection.json"
    _write(path)
    _edit(path, lambda obj: obj.pop("scan_id"))

    with pytest.raises(ValueError, match="scan_id"):
        read_selection(path)


def test_unexpected_key_raises_naming_the_key(tmp_path):
    path = tmp_path / "current_selection.json"
    _write(path)
    _edit(path, lambda obj: obj.update(api_token="x"))

    with pytest.raises(ValueError, match="api_token"):
        read_selection(path)


@pytest.mark.parametrize("scan_time", ["2024-06-01T14:00:37", "2024-06-01 14:00", "yesterday"])
def test_scan_time_not_in_the_utc_scan_format_raises_naming_the_key(tmp_path, scan_time):
    path = tmp_path / "current_selection.json"
    _write(path)
    _edit(path, lambda obj: obj.update(scan_time=scan_time))

    with pytest.raises(ValueError, match="scan_time"):
        read_selection(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("collection", None),
        ("run_id", 7),
        ("scan_id", ["0123456789abcdef"]),
        ("scan_time", 1717250437),
        ("cell_uid", 3),
        ("reason", 1),
        ("score", "72.0"),
        ("config_hash", None),
        ("stop_reason", 1),
        ("previous_cell_uid", 2),
    ],
)
def test_value_of_the_wrong_type_raises_naming_the_key(tmp_path, key, value):
    path = tmp_path / "current_selection.json"
    _write(path)
    _edit(path, lambda obj: obj.update({key: value}))

    with pytest.raises(ValueError, match=f"'{key}'"):
        read_selection(path)
