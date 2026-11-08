# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command end to end: a target selection file in, a published command out."""

import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from adapt.api import StoreClient
from adapt.consumers.radar_command import RadarCommandJob, load_config, process_selection, run
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
def cfg(tmp_path):
    path, out = tmp_path / "consumers.yaml", tmp_path / "out"
    path.write_text(
        f"""\
target_selection: {{}}
radar_command:
  radar: {{scan_pattern: ppi_sector, user_id: operator}}
  extent: {{horizontal_buffer_km: 1.0}}
  output: {{directory: '{out}'}}
""",
        encoding="utf-8",
    )
    return load_config(path)


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


def test_selection_publishes_one_box_named_by_its_plan(tmp_path, built, client, cfg):
    outcome = process_selection(client, cfg, _select(tmp_path, built))

    assert outcome.event is Event.PUBLISHED
    assert outcome.n_entries == 12  # whole minutes 12:06 .. 12:17 after the 12:05:37 scan
    command = json.loads((tmp_path / "out" / "current_targets.json").read_text(encoding="utf-8"))
    assert (command["scan_pattern"], command["user_id"], command["target_id"]) == (
        "ppi_sector",
        "operator",
        outcome.plan_id,
    )
    # The box covers where uid-a is at 12:06: rows 4-5 (40.04, 40.05 N).
    half = command["lat_extent"] / 2 / 111_194.93
    assert command["lat"] - half < 40.04 and command["lat"] + half > 40.05


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
    tmp_path, built, client, api_cfg, posted
):
    outcome = process_selection(client, api_cfg, _select(tmp_path, built), SecretStr("k"))

    assert outcome.event is Event.SENT
    assert outcome.detail == "HTTP 200: accepted"
    assert posted == [(tmp_path / "out" / "current_targets.json").read_text(encoding="utf-8")]


def test_a_duplicate_selection_is_not_sent_again(tmp_path, built, client, api_cfg, posted):
    path = _select(tmp_path, built)
    process_selection(client, api_cfg, path, SecretStr("k"))
    process_selection(client, api_cfg, path, SecretStr("k"))

    assert len(posted) == 1


def test_a_refused_command_is_logged_as_send_failed_without_the_key(
    tmp_path, built, client, api_cfg, monkeypatch
):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    outcome = process_selection(client, api_cfg, _select(tmp_path, built), SecretStr("k-secret"))

    assert outcome.event is Event.SEND_FAILED
    assert "HTTP 403: denied" in outcome.detail
    ledger = (tmp_path / "out" / "history" / "activity.jsonl").read_text(encoding="utf-8")
    assert "k-secret" not in ledger


def test_run_with_an_api_but_no_key_fails_before_reading_any_selection(
    tmp_path, built, client, api_cfg, monkeypatch
):
    monkeypatch.delenv("ADAPT_TEST_KEY", raising=False)
    job = RadarCommandJob(_select(tmp_path, built), follow=False, poll_seconds=1.0)

    with pytest.raises(ValueError, match="ADAPT_TEST_KEY"):
        run(client, api_cfg, job)
    assert not (tmp_path / "out" / "history" / "activity.jsonl").exists()


def _events(tmp_path):
    return [r.event for r in read_records(tmp_path / "out" / "history" / "activity.jsonl")]


def test_published_selection_is_logged_received_planned_published(tmp_path, built, client, cfg):
    process_selection(client, cfg, _select(tmp_path, built))

    assert _events(tmp_path) == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_every_ledger_record_names_the_rules_that_chose_the_cell(tmp_path, built, client, cfg):
    process_selection(client, cfg, _select(tmp_path, built))

    records = read_records(tmp_path / "out" / "history" / "activity.jsonl")
    assert {r.selection_config_hash for r in records} == {"0123456789abcdef"}


def _current(tmp_path):
    return (tmp_path / "out" / "current_targets.json").read_bytes()


def test_same_selection_again_is_a_duplicate_and_leaves_the_command(tmp_path, built, client, cfg):
    path = _select(tmp_path, built)
    process_selection(client, cfg, path)
    published = _current(tmp_path)

    assert process_selection(client, cfg, path).event is Event.DUPLICATE
    assert _current(tmp_path) == published


def test_older_selection_after_a_newer_one_is_out_of_order(tmp_path, built, client, cfg):
    process_selection(client, cfg, _select(tmp_path, built, scan=2))
    published = _current(tmp_path)

    assert (
        process_selection(client, cfg, _select(tmp_path, built, scan=1)).event is Event.OUT_OF_ORDER
    )
    assert _current(tmp_path) == published


def test_no_selected_cell_logs_no_target_and_leaves_the_command(tmp_path, built, client, cfg):
    process_selection(client, cfg, _select(tmp_path, built, scan=1))
    published = _current(tmp_path)

    outcome = process_selection(client, cfg, _select(tmp_path, built, scan=2, cell_uid=None))

    assert outcome.event is Event.NO_TARGET
    assert _events(tmp_path)[-2:] == [Event.SELECTION_RECEIVED, Event.NO_TARGET]
    assert _current(tmp_path) == published


def test_unlocatable_cell_logs_target_unavailable_and_keeps_the_command(
    tmp_path, built, client, cfg
):
    process_selection(client, cfg, _select(tmp_path, built, scan=1))
    published = _current(tmp_path)

    outcome = process_selection(client, cfg, _select(tmp_path, built, scan=2, cell_uid="uid-zz"))

    assert outcome.event is Event.TARGET_UNAVAILABLE
    assert _events(tmp_path)[-1] is Event.TARGET_UNAVAILABLE
    assert _current(tmp_path) == published


def test_command_failing_validation_is_logged_and_not_published(tmp_path, built, client, cfg):
    # Pydantic forbids an empty command; model_construct bypasses it to reach the
    # last check before anything is written for the radar.
    bad = cfg.model_copy(
        update={"radar": RadarConfig.model_construct(scan_pattern="", user_id="u")}
    )

    outcome = process_selection(client, bad, _select(tmp_path, built))

    assert outcome.event is Event.VALIDATION_FAILED
    assert _events(tmp_path)[-1] is Event.VALIDATION_FAILED
    assert not (tmp_path / "out" / "current_targets.json").exists()


def test_run_without_follow_processes_the_selection_once(tmp_path, built, client, cfg):
    job = RadarCommandJob(selection_path=_select(tmp_path, built), follow=False, poll_seconds=1.0)

    run(client, cfg, job)

    assert _events(tmp_path) == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


class _Stop(Exception):
    """Ends the otherwise endless follow loop."""


def test_follow_processes_each_new_selection_once(tmp_path, built, client, cfg, monkeypatch):
    # The wall clock is the loop's only external boundary: each "sleep" advances the
    # world one step — the selection appears, stays the same, changes, then we stop.
    steps = iter(
        [
            lambda: _select(tmp_path, built, scan=1),
            lambda: None,
            lambda: _select(tmp_path, built, scan=2),
        ]
    )

    def fake_sleep(seconds):
        step = next(steps, None)
        if step is None:
            raise _Stop
        step()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    job = RadarCommandJob(tmp_path / "current_selection.json", follow=True, poll_seconds=2.5)

    with pytest.raises(_Stop):
        run(client, cfg, job)

    assert _events(tmp_path) == [
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_follow_retries_a_target_whose_volume_stats_arrive_late(tmp_path, cfg, monkeypatch):
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
    steps = iter([lambda: add_volume_stats(built, "run-2", 1)])

    def fake_sleep(seconds):
        step = next(steps, None)
        if step is None:
            raise _Stop
        step()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    job = RadarCommandJob(tmp_path / "current_selection.json", follow=True, poll_seconds=2.5)

    with StoreClient(built.root) as client, pytest.raises(_Stop):
        run(client, cfg, job)

    assert _events(tmp_path) == [
        Event.SELECTION_RECEIVED,
        Event.TARGET_UNAVAILABLE,
        Event.SELECTION_RECEIVED,
        Event.PLAN_GENERATED,
        Event.PUBLISHED,
    ]


def test_follow_processes_a_selection_replaced_mid_poll_once(
    tmp_path, built, client, cfg, monkeypatch
):
    # Target selection (another process) replaces the file right after the poll
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
    polls = iter([None])

    def fake_sleep(seconds):
        if next(polls, _Stop) is _Stop:
            raise _Stop

    monkeypatch.setattr(time, "sleep", fake_sleep)
    job = RadarCommandJob(path, follow=True, poll_seconds=2.5)

    with pytest.raises(_Stop):
        run(client, cfg, job)

    records = read_records(tmp_path / "out" / "history" / "activity.jsonl")
    assert [(r.event, r.scan_id) for r in records] == [
        (Event.SELECTION_RECEIVED, built.scan_ids[2]),
        (Event.PLAN_GENERATED, built.scan_ids[2]),
        (Event.PUBLISHED, built.scan_ids[2]),
    ]
