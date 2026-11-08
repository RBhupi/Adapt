# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Append-only JSONL audit ledger — round-trip, order, and fail-loud reads."""

import json
from datetime import UTC, datetime

import pytest

from adapt.consumers.radar_command.ledger import Event, LedgerRecord, append, read_records

pytestmark = pytest.mark.unit


def _record(event=Event.SELECTION_RECEIVED):
    return LedgerRecord(
        event=event,
        recorded_at=datetime(2024, 6, 1, 14, 3, 7, 123456, tzinfo=UTC),
        run_id="run_a",
        scan_id="0123456789abcdef",
        source_scan_time=datetime(2024, 6, 1, 14, 0, 42, tzinfo=UTC),
        cell_uid="uid_beta",
        config_hash="fedcba9876543210",
        selection_config_hash="0123456789abcdef",
    )


_FULL = LedgerRecord(
    event=Event.PUBLISHED,
    recorded_at=datetime(2024, 6, 1, 14, 3, 7, 123456, tzinfo=UTC),
    run_id="run_a",
    scan_id="0123456789abcdef",
    source_scan_time=datetime(2024, 6, 1, 14, 0, 42, tzinfo=UTC),
    cell_uid="uid_beta",
    config_hash="fedcba9876543210",
    selection_config_hash="0123456789abcdef",
    plan_id="aaaabbbbccccdddd",
    n_entries=12,
    truncated=False,
    vertical_source="cell_volume_stats",
    detail="published 12 entries",
)
_NO_TARGET = LedgerRecord(
    event=Event.NO_TARGET,
    recorded_at=datetime(2024, 6, 1, 14, 3, 7, tzinfo=UTC),
    run_id="run_a",
    scan_id="0123456789abcdef",
    source_scan_time=datetime(2024, 6, 1, 14, 0, 42, tzinfo=UTC),
    cell_uid=None,
    config_hash="fedcba9876543210",
    selection_config_hash="0123456789abcdef",
)


@pytest.mark.parametrize("record", [_FULL, _NO_TARGET], ids=["all-fields-set", "optionals-none"])
def test_appended_record_reads_back_identical(tmp_path, record):
    path = tmp_path / "ledger.jsonl"
    append(path, record)
    assert read_records(path) == [record]


def test_records_read_back_in_append_order(tmp_path):
    path = tmp_path / "ledger.jsonl"
    for event in (Event.SELECTION_RECEIVED, Event.PLAN_GENERATED, Event.PUBLISHED):
        append(path, _record(event))
    assert [r.event for r in read_records(path)] == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_missing_ledger_reads_as_no_records(tmp_path):
    assert read_records(tmp_path / "ledger.jsonl") == []


_LINE_WITHOUT_DETAIL = {
    "event": "NO_TARGET",
    "recorded_at": "2024-06-01T14:03:07+00:00",
    "run_id": "run_a",
    "scan_id": "0123456789abcdef",
    "source_scan_time": "2024-06-01T14:00:42Z",
    "cell_uid": None,
    "config_hash": "fedcba9876543210",
    "selection_config_hash": "0123456789abcdef",
    "plan_id": None,
    "n_entries": None,
    "truncated": None,
    "vertical_source": None,
}


@pytest.mark.parametrize(
    "bad_line",
    [
        "not json",
        json.dumps(_LINE_WITHOUT_DETAIL),
        json.dumps(_LINE_WITHOUT_DETAIL | {"detail": None, "event": "EXPLODED"}),
    ],
    ids=["not-json", "missing-key", "unknown-event"],
)
def test_malformed_line_raises_naming_its_line_number(tmp_path, bad_line):
    path = tmp_path / "ledger.jsonl"
    append(path, _record())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(bad_line + "\n")
    with pytest.raises(ValueError, match="line 2"):
        read_records(path)


def test_each_append_adds_exactly_one_line(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append(path, _record())
    assert path.read_bytes().count(b"\n") == 1
    append(path, _record(Event.NO_TARGET))
    assert path.read_bytes().count(b"\n") == 2
