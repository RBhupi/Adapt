# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Command publication — atomic current file, history snapshots, ordering guards."""

import os
import threading
from datetime import UTC, datetime

import pytest

from adapt.consumers.radar_command.ledger import Event, LedgerRecord, read_records
from adapt.consumers.radar_command.lock import exclusive_lock
from adapt.consumers.radar_command.publish import OutputPaths, publish

pytestmark = pytest.mark.unit


def _paths(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    return OutputPaths(
        current=tmp_path / "current_targets.json",
        history_dir=history,
        ledger=tmp_path / "ledger.jsonl",
        lock=tmp_path / "publish.lock",
    )


def _record(plan_id, scan_time):
    return LedgerRecord(
        event=Event.PLAN_GENERATED,
        recorded_at=datetime(2024, 6, 1, 15, 0, tzinfo=UTC),
        run_id="run_a",
        scan_id=f"scan_for_{plan_id}",
        source_scan_time=scan_time,
        cell_uid="uid_beta",
        config_hash="fedcba9876543210",
        selection_config_hash="0123456789abcdef",
        plan_id=plan_id,
        n_entries=12,
        truncated=False,
        vertical_source="cell_volume_stats",
    )


_T0 = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)
_PLAN_A = '{\n  "Command": "a"\n}\n'
_T1 = datetime(2024, 6, 1, 14, 10, tzinfo=UTC)
_PLAN_B = '{\n  "Command": "b"\n}\n'


def test_first_publish_writes_current_history_and_ledger(tmp_path):
    paths = _paths(tmp_path)

    outcome = publish(_PLAN_A, _record("plan_a", _T0), paths)

    assert outcome.event is Event.PUBLISHED
    assert paths.current.read_bytes() == b'{\n  "Command": "a"\n}\n'
    assert (tmp_path / "history" / "plan_a.json").read_bytes() == b'{\n  "Command": "a"\n}\n'
    assert read_records(paths.ledger)[-1].event is Event.PUBLISHED


def test_newer_plan_replaces_current_and_keeps_both_snapshots(tmp_path):
    paths = _paths(tmp_path)
    publish(_PLAN_A, _record("plan_a", _T0), paths)

    outcome = publish(_PLAN_B, _record("plan_b", _T1), paths)

    assert outcome.event is Event.PUBLISHED
    assert paths.current.read_bytes() == b'{\n  "Command": "b"\n}\n'
    assert (tmp_path / "history" / "plan_a.json").read_bytes() == b'{\n  "Command": "a"\n}\n'
    assert (tmp_path / "history" / "plan_b.json").read_bytes() == b'{\n  "Command": "b"\n}\n'


def test_republishing_the_same_plan_is_a_duplicate(tmp_path):
    paths = _paths(tmp_path)
    publish(_PLAN_A, _record("plan_a", _T0), paths)

    outcome = publish('{\n  "Command": "a2"\n}\n', _record("plan_a", _T0), paths)

    assert outcome.event is Event.DUPLICATE
    assert paths.current.read_bytes() == b'{\n  "Command": "a"\n}\n'
    assert read_records(paths.ledger)[-1].event is Event.DUPLICATE


def test_older_scan_after_a_newer_publish_is_out_of_order(tmp_path):
    paths = _paths(tmp_path)
    publish(_PLAN_B, _record("plan_b", _T1), paths)

    outcome = publish(_PLAN_A, _record("plan_a", _T0), paths)

    assert outcome.event is Event.OUT_OF_ORDER
    assert paths.current.read_bytes() == b'{\n  "Command": "b"\n}\n'
    last = read_records(paths.ledger)[-1]
    assert last.event is Event.OUT_OF_ORDER
    assert "2024-06-01T14:10:00Z" in last.detail


def test_second_plan_for_an_already_published_scan_time_is_out_of_order(tmp_path):
    paths = _paths(tmp_path)
    publish(_PLAN_A, _record("plan_a", _T0), paths)

    outcome = publish(_PLAN_B, _record("plan_b", _T0), paths)

    assert outcome.event is Event.OUT_OF_ORDER
    assert paths.current.read_bytes() == b'{\n  "Command": "a"\n}\n'


def _disk_full(src, dst):
    raise OSError(28, "No space left on device")


def test_failed_write_is_logged_and_leaves_current_untouched(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    publish(_PLAN_A, _record("plan_a", _T0), paths)
    monkeypatch.setattr(os, "replace", _disk_full)

    with pytest.raises(OSError, match="No space left on device"):
        publish(_PLAN_B, _record("plan_b", _T1), paths)

    assert paths.current.read_bytes() == b'{\n  "Command": "a"\n}\n'
    assert list(tmp_path.rglob("*.tmp")) == []
    last = read_records(paths.ledger)[-1]
    assert last.event is Event.PUBLISH_FAILED
    assert last.detail.startswith("OSError")


def test_a_failed_plan_can_be_published_on_retry(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    publish(_PLAN_A, _record("plan_a", _T0), paths)
    with monkeypatch.context() as m:
        m.setattr(os, "replace", _disk_full)
        with pytest.raises(OSError):
            publish(_PLAN_B, _record("plan_b", _T1), paths)

    outcome = publish(_PLAN_B, _record("plan_b", _T1), paths)

    assert outcome.event is Event.PUBLISHED
    assert paths.current.read_bytes() == b'{\n  "Command": "b"\n}\n'


def test_publish_waits_while_another_publisher_holds_the_output_lock(tmp_path):
    # Two radar-command processes on one output directory: the guard (duplicate,
    # out-of-order) and the write must not interleave with another publish.
    paths = _paths(tmp_path)
    published = threading.Event()

    def other_process():
        publish(_PLAN_A, _record("plan_a", _T0), paths)
        published.set()

    publisher = threading.Thread(target=other_process)
    with exclusive_lock(paths.lock):
        publisher.start()
        assert not published.wait(0.3)
        assert not paths.current.exists()
    assert published.wait(5.0)
    publisher.join()
    assert paths.current.read_bytes() == b'{\n  "Command": "a"\n}\n'
