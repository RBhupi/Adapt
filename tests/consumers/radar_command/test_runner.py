# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command end to end: a target selection file in, a published command out."""

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from adapt.api import StoreClient
from adapt.consumers.radar_command import (
    RadarCommandConfig,
    RadarCommandJob,
    process_selection,
    run,
)
from adapt.consumers.radar_command.config import ApiConfig, RadarConfig
from adapt.consumers.radar_command.ledger import Event, read_records
from adapt.consumers.target_selection.selection import (
    SelectionReason,
    TargetSelection,
)
from adapt.consumers.target_selection.writer import write_current_selection
from tests.consumers.radar_command.synthetic_store import (
    COLLECTION,
    RUN,
    SCAN_TIMES,
    UID_A,
    add_run,
    add_volume_stats,
    build_store,
)
from tests.consumers.target_selection.factories import make_scan_decision

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("rc_runner"), volume_stats=True)


@pytest.fixture
def client(built):
    with StoreClient(built.root) as c:
        yield c


@pytest.fixture
def cfg():
    return RadarCommandConfig.model_validate(
        {
            "radar": {"scan_pattern": "ppi_sector", "user_id": "operator"},
            "extent": {"horizontal_buffer_km": 1.0},
        }
    )


@pytest.fixture
def out(tmp_path):
    """The output directory the launcher gives radar command."""
    return tmp_path / "out"


def _target(scan, cell_uid):
    """The engine's decision for ``cell_uid`` at scan index ``scan`` (None: no cell)."""
    if cell_uid is None:
        return None
    return TargetSelection(
        cell_uid=cell_uid,
        reason=SelectionReason.NEW_TARGET,
        score=80.0,
        selection_time=SCAN_TIMES[scan],
        trajectory=(),
        observation_window=(SCAN_TIMES[scan], SCAN_TIMES[scan]),
    )


def _select(tmp_path, built, scan=1, cell_uid=UID_A):
    """Write the selection exactly as the target selection engine does."""
    path = tmp_path / "current_selection.json"
    write_current_selection(
        path,
        make_scan_decision(
            COLLECTION, RUN, built.scan_ids[scan], SCAN_TIMES[scan], _target(scan, cell_uid)
        ),
    )
    return path


def test_selection_publishes_one_box_named_by_its_plan(tmp_path, built, client, cfg, out):
    outcome = process_selection(client, cfg, _select(tmp_path, built), out)

    assert outcome.event is Event.PUBLISHED
    assert outcome.n_entries == 12  # whole minutes 12:06 .. 12:17 after the 12:05:37 scan
    command = json.loads((out / "current_targets.json").read_text(encoding="utf-8"))
    assert (command["scan_pattern"], command["user_id"], command["target_id"]) == (
        "ppi_sector",
        "operator",
        outcome.plan_id,
    )
    # The box covers where uid-a is at 12:06: rows 4-5 (40.04, 40.05 N).
    half = command["lat_extent"] / 2 / 111_194.93
    assert command["lat"] - half < 40.04 and command["lat"] + half > 40.05


def test_the_plan_is_archived_in_the_history_directory(tmp_path, built, client, cfg, out):
    outcome = process_selection(client, cfg, _select(tmp_path, built), out)

    assert (out / "history" / f"{outcome.plan_id}.json").read_bytes() == (
        out / "current_targets.json"
    ).read_bytes()


class _Accepted:
    status = 200

    def read(self):
        return b"accepted"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def api_cfg(cfg):
    api = ApiConfig(
        base_url="https://radar.example.test", api_key_env="ADAPT_TEST_KEY", timeout_seconds=5
    )
    return cfg.model_copy(update={"api": api})


@pytest.fixture
def posted(monkeypatch):
    bodies = []

    def urlopen(request, timeout):
        bodies.append(request.data.decode("utf-8"))
        return _Accepted()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return bodies


def test_with_an_api_the_published_command_is_sent_and_logged(
    tmp_path, built, client, api_cfg, out, posted
):
    outcome = process_selection(client, api_cfg, _select(tmp_path, built), out, SecretStr("k"))

    assert outcome.event is Event.SENT
    assert outcome.detail == "HTTP 200: accepted"
    assert posted == [(out / "current_targets.json").read_text(encoding="utf-8")]


def test_a_duplicate_selection_is_not_sent_again(tmp_path, built, client, api_cfg, out, posted):
    path = _select(tmp_path, built)
    process_selection(client, api_cfg, path, out, SecretStr("k"))
    process_selection(client, api_cfg, path, out, SecretStr("k"))

    assert len(posted) == 1


def test_a_refused_command_is_logged_as_send_failed_without_the_key(
    tmp_path, built, client, api_cfg, out, monkeypatch
):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    outcome = process_selection(
        client, api_cfg, _select(tmp_path, built), out, SecretStr("k-secret")
    )

    assert outcome.event is Event.SEND_FAILED
    assert "HTTP 403: denied" in outcome.detail
    ledger = (out / "history" / "activity.jsonl").read_text(encoding="utf-8")
    assert "k-secret" not in ledger


def test_run_with_an_api_but_no_key_fails_before_reading_any_selection(
    tmp_path, built, client, api_cfg, out, monkeypatch
):
    monkeypatch.delenv("ADAPT_TEST_KEY", raising=False)
    job = RadarCommandJob(_select(tmp_path, built), out, poll_seconds=1.0, stop=threading.Event())

    with pytest.raises(ValueError, match="ADAPT_TEST_KEY"):
        run(client, api_cfg, job)
    assert not (out / "history" / "activity.jsonl").exists()


def _events(out):
    return [r.event for r in read_records(out / "history" / "activity.jsonl")]


def test_published_selection_is_logged_received_planned_published(
    tmp_path, built, client, cfg, out
):
    process_selection(client, cfg, _select(tmp_path, built), out)

    assert _events(out) == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_every_ledger_record_names_the_rules_that_chose_the_cell(tmp_path, built, client, cfg, out):
    process_selection(client, cfg, _select(tmp_path, built), out)

    records = read_records(out / "history" / "activity.jsonl")
    assert {r.selection_config_hash for r in records} == {"0123456789abcdef"}


def _current(out):
    return (out / "current_targets.json").read_bytes()


def test_same_selection_again_is_a_duplicate_and_leaves_the_command(
    tmp_path, built, client, cfg, out
):
    path = _select(tmp_path, built)
    process_selection(client, cfg, path, out)
    published = _current(out)

    assert process_selection(client, cfg, path, out).event is Event.DUPLICATE
    assert _current(out) == published


def test_older_selection_after_a_newer_one_is_out_of_order(tmp_path, built, client, cfg, out):
    process_selection(client, cfg, _select(tmp_path, built, scan=2), out)
    published = _current(out)

    outcome = process_selection(client, cfg, _select(tmp_path, built, scan=1), out)

    assert outcome.event is Event.OUT_OF_ORDER
    assert _current(out) == published


def test_no_selected_cell_logs_no_target_and_leaves_the_command(tmp_path, built, client, cfg, out):
    process_selection(client, cfg, _select(tmp_path, built, scan=1), out)
    published = _current(out)

    outcome = process_selection(client, cfg, _select(tmp_path, built, scan=2, cell_uid=None), out)

    assert outcome.event is Event.NO_TARGET
    assert _events(out)[-2:] == [Event.SELECTION_RECEIVED, Event.NO_TARGET]
    assert _current(out) == published


def test_unlocatable_cell_logs_target_unavailable_and_keeps_the_command(
    tmp_path, built, client, cfg, out
):
    process_selection(client, cfg, _select(tmp_path, built, scan=1), out)
    published = _current(out)

    outcome = process_selection(
        client, cfg, _select(tmp_path, built, scan=2, cell_uid="uid-zz"), out
    )

    assert outcome.event is Event.TARGET_UNAVAILABLE
    assert _events(out)[-1] is Event.TARGET_UNAVAILABLE
    assert _current(out) == published


def test_command_failing_validation_is_logged_and_not_published(tmp_path, built, client, cfg, out):
    # Pydantic forbids an empty command; model_construct bypasses it to reach the
    # last check before anything is written for the radar.
    bad = cfg.model_copy(
        update={"radar": RadarConfig.model_construct(scan_pattern="", user_id="u")}
    )

    outcome = process_selection(client, bad, _select(tmp_path, built), out)

    assert outcome.event is Event.VALIDATION_FAILED
    assert _events(out)[-1] is Event.VALIDATION_FAILED
    assert not (out / "current_targets.json").exists()


class _Clock(threading.Event):
    """The follow loop's stop event, and its only clock: each wait between polls
    advances the world one step; once the steps run out the launcher stops it."""

    def __init__(self, *steps):
        super().__init__()
        self._steps = iter(steps)
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        step = next(self._steps, None)
        if step is None:
            self.set()
        else:
            step()
        return self.is_set()


def _follow(path, out, clock):
    return RadarCommandJob(path, out, poll_seconds=2.5, stop=clock)


def test_follow_processes_each_new_selection_once(tmp_path, built, client, cfg, out):
    # The selection appears, stays the same, changes, then the launcher stops.
    clock = _Clock(
        lambda: _select(tmp_path, built, scan=1),
        lambda: None,
        lambda: _select(tmp_path, built, scan=2),
    )

    run(client, cfg, _follow(tmp_path / "current_selection.json", out, clock))

    assert _events(out) == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]
    assert set(clock.waits) == {2.5}


def test_follow_sends_the_final_selection_written_while_it_was_busy(
    tmp_path, built, client, api_cfg, out, monkeypatch
):
    # While the scan-1 command is being sent, target selection writes its last
    # decision and the launcher stops radar command: that one is still sent.
    monkeypatch.setenv("ADAPT_TEST_KEY", "k")
    clock = _Clock()
    sent = []

    def urlopen(request, timeout):
        if not sent:
            _select(tmp_path, built, scan=2)
            clock.set()
        sent.append(json.loads(request.data)["target_id"])
        return _Accepted()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    run(client, api_cfg, _follow(_select(tmp_path, built, scan=1), out, clock))

    records = read_records(out / "history" / "activity.jsonl")
    assert [r.scan_id for r in records if r.event is Event.SENT] == list(built.scan_ids[1:])
    assert len(set(sent)) == 2


def test_follow_stopped_before_any_selection_writes_nothing(tmp_path, client, cfg, out):
    clock = _Clock()

    run(client, cfg, _follow(tmp_path / "current_selection.json", out, clock))

    assert not out.exists()


def test_follow_retries_a_target_whose_volume_stats_arrive_late(tmp_path, cfg, out):
    # run-2 wrote cell_volume_stats for scan 0; the selection on scan 1 arrives
    # while phase 3 is still computing scan 1's row, which lands before the next poll.
    built = build_store(tmp_path / "built", volume_stats=True)
    add_run(built, "run-2", volume_scans=(0,))
    write_current_selection(
        tmp_path / "current_selection.json",
        make_scan_decision(
            COLLECTION, "run-2", built.scan_ids[1], SCAN_TIMES[1], _target(1, UID_A)
        ),
    )
    clock = _Clock(lambda: add_volume_stats(built, "run-2", 1))

    with StoreClient(built.root) as client:
        run(client, cfg, _follow(tmp_path / "current_selection.json", out, clock))

    assert _events(out) == [
        Event.SELECTION_RECEIVED,
        Event.TARGET_UNAVAILABLE,
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_follow_processes_a_selection_replaced_mid_poll_once(
    tmp_path, built, client, cfg, out, monkeypatch
):
    # Target selection (another thread) replaces the file right after the poll
    # has read it: the newer selection is what gets processed, and only once.
    path = _select(tmp_path, built, scan=1)
    read_text = Path.read_text
    replaced = []

    def read_then_target_selection_writes(self, *args, **kwargs):
        text = read_text(self, *args, **kwargs)
        if self == path and not replaced:
            replaced.append(True)
            _select(tmp_path, built, scan=2)
        return text

    monkeypatch.setattr(Path, "read_text", read_then_target_selection_writes)

    run(client, cfg, _follow(path, out, _Clock(lambda: None)))

    records = read_records(out / "history" / "activity.jsonl")
    assert [(r.event, r.scan_id) for r in records] == [
        (Event.SELECTION_RECEIVED, built.scan_ids[2]),
        (Event.PLAN_GENERATED, built.scan_ids[2]),
        (Event.PUBLISHED, built.scan_ids[2]),
    ]
