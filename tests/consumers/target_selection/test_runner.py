# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Headless TSE runner over a real synthetic store: one decision per complete scan."""

import json
import os
import time
from pathlib import Path

import pandas as pd
import pytest

from adapt.api import StoreClient
from adapt.consumers.target_selection.runner import TSEJob, run
from adapt.contracts.persistence import PersistenceMeta
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store
from adapt.persistence.store_registry import StoreRegistry
from adapt.runtime.acquire import StoreAcquirer
from tests.api.synthetic_store import (
    _ANALYSIS_SPEC,
    _MODULES,
    _TRACK_SPEC,
    COLLECTION,
    RUN_1,
    RUN_2,
    _Module,
    _scan_result,
    build_synthetic_store,
)
from tests.consumers.target_selection.factories import make_config

pytestmark = pytest.mark.unit

# uid-1 is 45 dBZ at 12:00 and 46 dBZ at 12:05 (age 0 s, then 300 s).
_REFL_GE_45 = ({"field": "radar_reflectivity_max", "op": "ge", "value": 45.0},)


@pytest.fixture
def built(tmp_path):
    return build_synthetic_store(tmp_path / "built")


@pytest.fixture
def client(built):
    c = StoreClient(built.root)
    yield c
    c.close()


def _job(tmp_path, run_id=RUN_1, follow=False):
    return TSEJob(
        collection=COLLECTION,
        run_id=run_id,
        selection_path=tmp_path / "current_selection.json",
        follow=follow,
        poll_seconds=0.01,
    )


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# The tracking node links segmentation2d (the last product a scan needs to be
# complete) before it commits the scan's tracking rows.
_UP_TO_SEGMENTATION = [*_MODULES[:2], _Module(name="tracking", persistence=(_ANALYSIS_SPEC,))]
_TRACKING_ROWS = [_Module(name="tracking", persistence=(_TRACK_SPEC,))]
_UID = {RUN_1: "uid-1", RUN_2: "uid-r2"}


def _persist(built, index, run_id, modules):
    """Persist ``modules``' outputs for raw scan ``index`` of ``run_id``: one cell,
    tracked as that run's uid."""
    store = Store.open(built.root)
    meta = PersistenceMeta(
        scan_time=built.scan_times[index],
        scan_id=built.scan_ids[index],
        run_id=run_id,
        source_file=f"KTST20260601_12{index * 5:02d}00_V06",
        collection_id=COLLECTION,
    )
    result = _scan_result(index, _UID[run_id], pd.DataFrame())
    StoreOutputRouter(store.collection(COLLECTION)).persist(modules, result, meta)
    store.close()


def test_selection_file_names_last_complete_scan_and_target(tmp_path, built, client):
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    job = _job(tmp_path)
    run(client, config, job)
    # Score at 12:05 = 1.0 * 46 dBZ + 0.05 * 20 km2 + 2.0 * (20 - 10) km2 / 5 min = 51.0
    assert _read(job.selection_path) == {
        "collection": "KTST",
        "run_id": "run-1",
        "scan_id": built.scan_ids[1],
        "scan_time": "2026-06-01T12:05:00Z",
        "cell_uid": "uid-1",
        "reason": "CONTINUATION",
        "score": pytest.approx(51.0),
    }


def test_catch_up_shows_readers_only_the_newest_decision(tmp_path, client, monkeypatch):
    # A radar command consumer polling the file during a replay of run-1 must not
    # see the 12:00 decision: it would plan a target from a scan long past.
    job = _job(tmp_path)
    shown = []
    replace = os.replace

    def replace_and_observe(src, dst):
        replace(src, dst)
        if Path(dst) == job.selection_path:
            shown.append(_read(job.selection_path)["scan_time"])

    monkeypatch.setattr(os, "replace", replace_and_observe)
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    run(client, config, job)

    assert shown == ["2026-06-01T12:05:00Z"]


def test_returns_the_number_of_scans_processed(tmp_path, client):
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    assert run(client, config, _job(tmp_path)) == 2


def test_jsonl_log_gets_one_line_per_selection(tmp_path, client):
    log = tmp_path / "selections.jsonl"
    config = make_config(
        gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2, jsonl_path=str(log)
    )
    run(client, config, _job(tmp_path))
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(rec["cell_uid"], rec["reason"], rec["selection_time"]) for rec in lines] == [
        ("uid-1", "NEW_TARGET", "2026-06-01T12:00:00Z"),
        ("uid-1", "CONTINUATION", "2026-06-01T12:05:00Z"),
    ]


def test_running_again_leaves_one_log_line_per_selecting_scan(tmp_path, built, client):
    # A second invocation (or a restart of a follow) replays the run from its first scan.
    log = tmp_path / "selections.jsonl"
    config = make_config(
        gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2, jsonl_path=str(log)
    )
    run(client, config, _job(tmp_path))
    run(client, config, _job(tmp_path))

    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(rec["run_id"], rec["scan_id"]) for rec in lines] == [
        ("run-1", built.scan_ids[0]),
        ("run-1", built.scan_ids[1]),
    ]


def test_no_candidate_writes_a_null_target(tmp_path, built, client):
    config = make_config(
        gates=({"field": "radar_reflectivity_max", "op": "ge", "value": 50.0},),
        min_age_seconds=0.0,
        growth_window_scans=2,
    )
    job = _job(tmp_path)
    run(client, config, job)
    written = _read(job.selection_path)
    assert (written["scan_id"], written["cell_uid"], written["reason"], written["score"]) == (
        built.scan_ids[1],
        None,
        None,
        None,
    )


def test_complete_scan_without_tracking_rows_in_an_ended_run_raises(tmp_path, built, client):
    # 12:10 is complete (segmentation2d linked) but its tracking rows never came.
    _persist(built, 2, RUN_1, _UP_TO_SEGMENTATION)
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)

    with pytest.raises(RuntimeError, match=built.scan_ids[2]):
        run(client, config, _job(tmp_path))


def test_follow_picks_up_scans_until_the_run_ends(tmp_path, built, client, monkeypatch):
    """run-2 is running with one complete scan (12:00); while the runner waits, the
    12:05 volume completes and the run finishes."""
    finished = []

    def _pipeline_progresses(seconds):
        if finished:
            raise AssertionError("still polling after the run finished")
        store = Store.open(built.root)
        raw = tmp_path / "built" / "raw" / "KTST20260601_120500_V06"
        StoreAcquirer(store.collection(COLLECTION), RUN_2).acquire_existing(
            str(raw), scan_time=built.scan_times[1]
        )
        store.close()
        _persist(built, 1, RUN_2, _MODULES)
        StoreRegistry.get_instance(built.root).finalize_run(RUN_2, "completed", scans_processed=2)
        finished.append(seconds)

    monkeypatch.setattr(time, "sleep", _pipeline_progresses)
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    job = _job(tmp_path, run_id=RUN_2, follow=True)
    assert run(client, config, job) == 2
    written = _read(job.selection_path)
    assert (written["run_id"], written["scan_time"], written["cell_uid"]) == (
        "run-2",
        "2026-06-01T12:05:00Z",
        "uid-r2",
    )


def test_follow_waits_for_tracking_rows_of_a_complete_scan(tmp_path, built, client, monkeypatch):
    """run-2 holds uid-r2 from 12:00. The 12:05 volume turns complete when its
    segmentation2d is linked; its tracking rows commit just after, then the run ends."""
    steps = iter(
        [
            lambda: _acquire_and_persist(built, tmp_path, _UP_TO_SEGMENTATION),
            lambda: _persist(built, 1, RUN_2, _TRACKING_ROWS),
            lambda: StoreRegistry.get_instance(built.root).finalize_run(
                RUN_2, "completed", scans_processed=2
            ),
        ]
    )

    def _pipeline_progresses(seconds):
        step = next(steps, None)
        if step is None:
            raise AssertionError("still polling after the run finished")
        step()

    monkeypatch.setattr(time, "sleep", _pipeline_progresses)
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    job = _job(tmp_path, run_id=RUN_2, follow=True)
    assert run(client, config, job) == 2
    written = _read(job.selection_path)
    assert (written["scan_time"], written["cell_uid"], written["reason"]) == (
        "2026-06-01T12:05:00Z",
        "uid-r2",
        "CONTINUATION",
    )


def _acquire_and_persist(built, tmp_path, modules):
    """run-2 acquires the 12:05 volume and persists ``modules`` for it."""
    store = Store.open(built.root)
    raw = tmp_path / "built" / "raw" / "KTST20260601_120500_V06"
    StoreAcquirer(store.collection(COLLECTION), RUN_2).acquire_existing(
        str(raw), scan_time=built.scan_times[1]
    )
    store.close()
    _persist(built, 1, RUN_2, modules)


def test_follow_on_a_finished_run_returns_while_another_run_is_running(
    tmp_path, client, monkeypatch
):
    """run-1 is completed; run-2 of the same collection is still running."""

    def _no_wait(seconds):
        raise AssertionError("polled a run that has already finished")

    monkeypatch.setattr(time, "sleep", _no_wait)
    config = make_config(gates=_REFL_GE_45, min_age_seconds=0.0, growth_window_scans=2)
    assert run(client, config, _job(tmp_path, follow=True)) == 2
