"""What the processor hands the per-scan graph: ``prior_scans``, usable history, skips.

The graph itself is replaced by recorders, so these tests see exactly what the
processor passes in and keeps afterwards.
"""

import logging
import queue
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from adapt.execution.graph.executor import GraphRun
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.persistence.errors import StoreError
from adapt.runtime.processor import RadarProcessor
from tests.helpers.fake_graph import NEEDS_PRIOR_SCAN
from tests.helpers.pipeline_files import MODULES
from tests.helpers.queue_msg import msg as _msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

T1 = datetime(2024, 5, 18, 12, 0, 0, tzinfo=UTC)
T2 = T1 + timedelta(minutes=5)


def _fake_ds():
    return xr.Dataset(
        {
            "reflectivity": (("y", "x"), np.ones((4, 4))),
            "cell_labels": (("y", "x"), np.zeros((4, 4), dtype=int)),
        },
        coords={"x": np.arange(4), "y": np.arange(4)},
        attrs={"z_level_m": 2000},
    )


class _Recorder:
    """A graph pass that records what it was given and returns canned outputs."""

    def __init__(self, outputs: dict, skips_without_history: tuple[str, ...] = ()):
        self.calls: list[dict] = []
        self._outputs = outputs
        self._skips = skips_without_history

    def __call__(self, context, *, history, before_node=None, after_node=None):
        self.calls.append({"context": context, "history": history})
        skipped = dict.fromkeys(self._skips, "too few prior scans") if history < 1 else {}
        return GraphRun(context={**context, **self._outputs}, skipped=skipped, failed={})


def _wire(proc, monkeypatch) -> _Recorder:
    graph = _Recorder(
        {
            "grid_ds": _fake_ds(),
            "grid_ds_2d": _fake_ds(),
            "segmented_ds": _fake_ds(),
            "num_cells": 0,
            "seed_carry": (),
            "cell_stats": pd.DataFrame(),
        },
        skips_without_history=NEEDS_PRIOR_SCAN,
    )
    monkeypatch.setattr(proc._executor, "run", graph)
    return graph


def test_prior_scans_is_empty_first_then_holds_the_previous_scan(
    tmp_path, monkeypatch, make_processor, store_env
):
    proc = make_processor()
    core = _wire(proc, monkeypatch)

    assert proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1)) is True
    assert proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=T2)) is True

    first, second = core.calls
    assert first["context"]["prior_scans"] == []
    assert first["history"] == 0
    assert [p["scan_time"] for p in second["context"]["prior_scans"]] == [T1]
    assert second["history"] == 1


def test_prior_scans_is_empty_when_the_time_gap_is_too_large(
    tmp_path, monkeypatch, make_processor, store_env
):
    proc = make_processor()
    core = _wire(proc, monkeypatch)
    too_late = T1 + timedelta(minutes=proc._max_time_gap_minutes + 1)

    proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
    proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=too_late))

    assert core.calls[1]["context"]["prior_scans"] == []
    assert core.calls[1]["history"] == 0


def test_prior_scans_reaches_back_only_as_far_as_the_modules_need(
    tmp_path, monkeypatch, make_processor, store_env
):
    """Projection pairs exactly two frames: the third scan gets the second, not both."""
    proc = make_processor()
    core = _wire(proc, monkeypatch)
    t3 = T2 + timedelta(minutes=5)

    for name, scan_time in (("f1", T1), ("f2", T2), ("f3", t3)):
        proc.process_file(_msg(store_env, tmp_path, name, scan_time=scan_time))

    third = core.calls[2]
    assert [p["scan_time"] for p in third["context"]["prior_scans"]] == [T2]
    assert third["history"] == 1


class _DeepHistoryModule(BaseModule):
    """Reads three scans back (four frames): deeper than any catalog module."""

    name = "deep_history_probe"
    outputs = ["deep_history_probe_out"]
    required_history = 4

    def run(self, context: dict) -> dict:
        return {}


def test_history_reaches_back_as_far_as_the_most_demanding_module_needs(
    tmp_path, monkeypatch, make_processor, pipeline_config, store_env
):
    """How far the history goes is the modules' need, not a separate setting."""
    registry.register(_DeepHistoryModule)
    try:
        config = pipeline_config.model_copy(update={"modules": ["ingest", "deep_history_probe"]})
        proc = make_processor(config=config)
        core = _Recorder({"grid_ds": _fake_ds(), "grid_ds_2d": _fake_ds()})
        monkeypatch.setattr(proc._executor, "run", core)
        times = [T1 + timedelta(minutes=5 * i) for i in range(5)]

        for i, scan_time in enumerate(times):
            proc.process_file(_msg(store_env, tmp_path, f"f{i}", scan_time=scan_time))
    finally:
        registry.unregister("deep_history_probe")

    fourth, fifth = core.calls[3], core.calls[4]
    assert [p["scan_time"] for p in fourth["context"]["prior_scans"]] == times[:3]
    assert fourth["history"] == 3
    assert [p["scan_time"] for p in fifth["context"]["prior_scans"]] == times[1:4]


def test_a_pipeline_whose_modules_read_no_earlier_scan_is_given_none(
    tmp_path, monkeypatch, make_processor, pipeline_config, store_env, caplog
):
    """No enabled module waits for or reads earlier scans: none is kept, scan
    after scan, and nothing waits for history or is skipped for a gap."""
    config = pipeline_config.model_copy(update={"modules": ["ingest"]})
    proc = make_processor(config=config)
    core = _Recorder({"grid_ds": _fake_ds(), "grid_ds_2d": _fake_ds()})
    monkeypatch.setattr(proc._executor, "run", core)
    t3 = T2 + timedelta(minutes=5)
    too_late = t3 + timedelta(minutes=proc._max_time_gap_minutes + 1)

    with caplog.at_level(logging.INFO, logger="adapt.runtime.processor"):
        for name, scan_time in (("f1", T1), ("f2", T2), ("f3", t3), ("f4", too_late)):
            proc.process_file(_msg(store_env, tmp_path, name, scan_time=scan_time))

    assert [call["context"]["prior_scans"] for call in core.calls] == [[]] * 4
    assert [call["history"] for call in core.calls] == [0] * 4
    messages = [r.getMessage() for r in caplog.records]
    assert not any(m.startswith(("Waiting for history", "Time gap")) for m in messages)


def test_a_pass_listing_only_target_selection_is_given_no_earlier_scan(
    tmp_path, monkeypatch, make_processor, make_config, store_env
):
    config = make_config(modules=[*MODULES, "target_selection"]).model_copy(
        update={"source": "stored_run", "modules": ["target_selection"]}
    )
    proc = make_processor(config=config)
    core = _Recorder({})
    monkeypatch.setattr(proc._executor, "run", core)

    for name, scan_time in (("f1", T1), ("f2", T2)):
        assert proc.process_file(_msg(store_env, tmp_path, name, scan_time=scan_time)) is True

    assert [call["context"]["prior_scans"] for call in core.calls] == [[], []]


def test_a_processor_with_no_modules_starts(make_processor, pipeline_config):
    proc = make_processor(config=pipeline_config.model_copy(update={"modules": []}))

    assert proc.module_names == ()


def test_history_entries_do_not_retain_prior_scans(
    tmp_path, monkeypatch, make_processor, store_env
):
    """An entry holding its predecessor would keep every scan's grids alive."""
    proc = make_processor()
    _wire(proc, monkeypatch)

    proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
    proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=T2))

    assert "prior_scans" not in proc._scan_history[-1]


def test_what_is_kept_of_a_scan_is_what_the_modules_options_make_them_read_back(
    tmp_path, monkeypatch, make_processor, pipeline_config, store_env
):
    """With the seed carry off detection reads nothing back, so its carry ages and
    cell count are not kept (nor saved in the checkpoint); projection still reads
    the labels."""
    no_carry = {**pipeline_config.module_params, "detection": {"seed_carry_frames": 0}}
    proc = make_processor(config=pipeline_config.model_copy(update={"module_params": no_carry}))
    core = _wire(proc, monkeypatch)

    proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
    proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=T2))

    (kept,) = core.calls[1]["context"]["prior_scans"]
    assert set(kept) == {"segmented_ds", "scan_id", "scan_time"}


def test_with_the_seed_carry_on_the_carry_ages_and_cell_count_are_kept_of_a_scan(
    tmp_path, monkeypatch, make_processor, store_env
):
    proc = make_processor()
    core = _wire(proc, monkeypatch)

    proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
    proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=T2))

    (kept,) = core.calls[1]["context"]["prior_scans"]
    assert set(kept) == {"segmented_ds", "seed_carry", "num_cells", "scan_id", "scan_time"}


def test_each_modules_fate_in_a_scan_is_recorded(tmp_path, monkeypatch, make_processor, store_env):
    proc = make_processor()
    _wire(proc, monkeypatch)
    first = _msg(store_env, tmp_path, "f1", scan_time=T1)

    proc.process_file(first)

    fates = store_env.collection.catalog.scan_modules(store_env.run_id, first["scan_id"])
    assert fates["ingest"]["status"] == "done"
    assert fates["tracking"]["status"] == "skipped"
    assert fates["tracking"]["reason"] == "too few prior scans"
    assert store_env.collection.catalog.scan_is_complete(store_env.run_id, first["scan_id"])


def test_a_failed_scan_is_counted_once_whatever_else_breaks(
    tmp_path, monkeypatch, make_processor, store_env
):
    """A module failed, and then the scan's pass could not be recorded either:
    still one failed scan."""
    proc = make_processor()
    graph = _wire(proc, monkeypatch)
    good_run = graph.__call__

    def fail(context, *, history, before_node=None, after_node=None):
        run = good_run(context, history=history, after_node=after_node)
        return GraphRun(run.context, run.skipped, {"analysis": ValueError("kaboom")})

    def unrecordable(*args, **kwargs):
        raise StoreError("the catalog cannot be written")

    monkeypatch.setattr(proc._executor, "run", fail)
    monkeypatch.setattr(store_env.collection.catalog, "record_scan_pass", unrecordable)

    assert proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1)) is False

    assert proc.failed_scans == 1


def test_scans_that_complete_are_not_counted_as_failed(
    tmp_path, monkeypatch, make_processor, store_env
):
    proc = make_processor()
    _wire(proc, monkeypatch)

    assert proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1)) is True

    assert proc.failed_scans == 0


def test_a_scan_with_a_failed_module_is_a_scan_that_never_arrived(
    tmp_path, monkeypatch, make_processor, store_env
):
    """It is marked failed and does not join the history: the next scan pairs
    with the last good one."""
    proc = make_processor()
    graph = _wire(proc, monkeypatch)
    t3 = T2 + timedelta(minutes=5)
    good_run = graph.__call__

    def fail_the_second(context, *, history, before_node=None, after_node=None):
        run = good_run(context, history=history, after_node=after_node)
        if context["scan_time"] == T2:
            return GraphRun(run.context, run.skipped, {"analysis": ValueError("kaboom")})
        return run

    monkeypatch.setattr(proc._executor, "run", fail_the_second)
    messages = [
        _msg(store_env, tmp_path, name, scan_time=scan_time)
        for name, scan_time in (("f1", T1), ("f2", T2), ("f3", t3))
    ]

    assert [proc.process_file(m) for m in messages] == [True, False, True]

    catalog = store_env.collection.catalog
    assert catalog.get_scan(store_env.run_id, messages[1]["scan_id"])["status"] == "failed"
    failed = catalog.scan_modules(store_env.run_id, messages[1]["scan_id"])["analysis"]
    assert (failed["status"], failed["reason"]) == ("failed", "ValueError: kaboom")
    assert [p["scan_time"] for p in graph.calls[2]["context"]["prior_scans"]] == [T1]


def test_first_scan_logs_that_it_waits_for_history(
    tmp_path, monkeypatch, make_processor, store_env, caplog
):
    proc = make_processor()
    _wire(proc, monkeypatch)

    with caplog.at_level(logging.INFO, logger="adapt.runtime.processor"):
        proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
        proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=T2))

    waiting = [r for r in caplog.records if r.getMessage().startswith("Waiting for history")]
    assert len(waiting) == 1
    assert waiting[0].levelno == logging.INFO
    assert waiting[0].getMessage().startswith("Waiting for history: need 1 prior scans, have 0 (")


def test_too_large_gap_logs_the_multi_scan_skip(
    tmp_path, monkeypatch, make_processor, store_env, caplog
):
    proc = make_processor()
    _wire(proc, monkeypatch)
    limit = proc._max_time_gap_minutes
    too_late = T1 + timedelta(minutes=limit + 1)

    proc.process_file(_msg(store_env, tmp_path, "f1", scan_time=T1))
    with caplog.at_level(logging.WARNING, logger="adapt.runtime.processor"):
        proc.process_file(_msg(store_env, tmp_path, "f2", scan_time=too_late))

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logging.WARNING,
            f"Time gap {limit + 1:.1f} min > {limit:.1f} min, skipping multi-scan modules.",
        )
    ]


def test_module_names_lists_every_enabled_module(make_processor):
    assert set(make_processor().module_names) == {
        "ingest",
        "detection",
        "projection",
        "analysis",
        "tracking",
        "cell_volume_stats",
    }


class _UnboundInputModule(BaseModule):
    name = "unbound_probe"
    inputs = ["no_such_key"]
    outputs = ["unbound_probe_out"]

    def run(self, context: dict) -> dict:
        return {}


def test_a_module_input_with_no_source_refuses_to_start(pipeline_config, store_env):
    registry.register(_UnboundInputModule)
    config = pipeline_config.model_copy(update={"modules": ["ingest", "unbound_probe"]})
    try:
        with pytest.raises(ValueError, match="'unbound_probe' needs input 'no_such_key'"):
            RadarProcessor(
                queue.Queue(),
                config,
                collection=store_env.collection,
                registry=store_env.registry,
                run_id=store_env.run_id,
                history=store_env.history,
            )
    finally:
        registry.unregister("unbound_probe")
