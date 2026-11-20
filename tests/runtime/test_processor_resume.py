# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""What modules carry between scans, the checkpoint that keeps it, and resuming a run.

Real processor, executor and store; one probe module that counts the scans it
has seen (its carried state) and reads its own previous output back, and one
that fails on demand.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from adapt.contracts import ProductTableWrite, check_module_state
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.persistence.errors import StoreError
from tests.helpers.queue_msg import msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
COUNTER, FLAKY, UNSAVED = "probe_counter", "probe_flaky", "probe_unsaved"


class _Counter(BaseModule):
    """Counts the scans it has seen; says what it read back from the scan before."""

    name = COUNTER
    inputs = ["scan_time", "prior_scans", "probe_count"]
    outputs = ["probe_rows", "probe_out", "probe_unread", "probe_count"]
    prior_inputs = ("probe_out",)
    output_contracts = {"probe_count": check_module_state}
    persistence = (
        ProductTableWrite(
            key="probe_rows", table="probe_counter", primary_key=("run_id", "scan_id")
        ),
    )

    def run(self, context: dict) -> dict:
        count = (context["probe_count"] or 0) + 1
        prior = context["prior_scans"]
        row = {
            "count": count,
            "before": prior[-1]["probe_out"] if prior else "nothing",
            "kept": ",".join(sorted(prior[-1])) if prior else "",
        }
        return {
            "probe_rows": pd.DataFrame([row]),
            "probe_out": f"scan-{count}",
            "probe_unread": "a large output no module reads back",
            "probe_count": count,
        }


class _Flaky(BaseModule):
    """Raises on the scans named in ``failing`` (by minute of their time)."""

    name = FLAKY
    inputs = ["scan_time"]
    outputs = ["flaky_out"]
    failing: set[int] = set()

    def run(self, context: dict) -> dict:
        if context["scan_time"].minute in self.failing:
            raise ValueError("kaboom")
        return {"flaky_out": 1}


class _Unsaved(BaseModule):
    """Counts the scans it has seen; on the scans named in ``unsavable`` (by
    minute) what it carries cannot be saved in a checkpoint."""

    name = UNSAVED
    inputs = ["scan_time", "probe_seen"]
    outputs = ["probe_seen_rows", "probe_seen"]
    persistence = (
        ProductTableWrite(
            key="probe_seen_rows", table="probe_unsaved", primary_key=("run_id", "scan_id")
        ),
    )
    unsavable: set[int] = set()

    def run(self, context: dict) -> dict:
        seen = (context["probe_seen"] or {"n": 0})["n"] + 1
        state: dict = {"n": seen}
        if context["scan_time"].minute in self.unsavable:
            state["handle"] = lambda: None  # cannot be pickled
        return {"probe_seen_rows": pd.DataFrame([{"count": seen}]), "probe_seen": state}


@pytest.fixture(autouse=True)
def probes():
    for probe in (_Counter, _Flaky, _Unsaved):
        registry.register(probe)
    _Flaky.failing = set()
    _Unsaved.unsavable = set()
    yield
    for name in (COUNTER, FLAKY, UNSAVED):
        registry.unregister(name)


@pytest.fixture
def scans(store_env, tmp_path):
    """Five acquired scans, five minutes apart (minutes 0, 5, 10, 15, 20)."""
    return [
        msg(store_env, tmp_path, f"f{i}", scan_time=T0 + timedelta(minutes=5 * i)) for i in range(5)
    ]


@pytest.fixture
def processor(make_processor, pipeline_config):
    """A new processor of the run, as each start of ``adapt run --run-id`` builds one."""

    def _processor(modules=(COUNTER, FLAKY), **overrides):
        config = pipeline_config.model_copy(update={"modules": list(modules), **overrides})
        return make_processor(config=config)

    return _processor


def _counts(store_env) -> list[tuple[int, str]]:
    """(count, what was read back) per stored scan, in time order."""
    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        return conn.execute("SELECT count, before FROM probe_counter ORDER BY scan_time").fetchall()
    finally:
        conn.close()


def test_a_module_is_handed_back_what_it_returned_for_its_next_scan(processor, scans, store_env):
    proc = processor()

    assert [proc.process_file(m) for m in scans[:3]] == [True] * 3

    assert _counts(store_env) == [(1, "nothing"), (2, "scan-1"), (3, "scan-2")]


def test_only_what_modules_read_back_is_kept_of_earlier_scans(processor, scans, store_env):
    proc = processor()
    for m in scans[:2]:
        proc.process_file(m)

    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        (kept,) = conn.execute("SELECT kept FROM probe_counter WHERE count = 2").fetchone()
    finally:
        conn.close()
    assert kept == "probe_out,scan_id,scan_time"


def test_a_failed_scan_leaves_the_modules_state_and_the_history_as_they_were(
    processor, scans, store_env
):
    proc = processor()
    _Flaky.failing = {5}

    assert [proc.process_file(m) for m in scans[:3]] == [True, False, True]

    # The second scan's row was stored before the other module failed; the scan
    # itself never counted: the third scan is the module's second.
    assert _counts(store_env) == [(1, "nothing"), (2, "scan-1"), (2, "scan-1")]


def test_a_scan_whose_checkpoint_cannot_be_saved_fails_and_leaves_the_state_as_it_was(
    processor, scans, store_env
):
    proc = processor(modules=[UNSAVED])
    _Unsaved.unsavable = {5}

    assert [proc.process_file(m) for m in scans[:3]] == [True, False, True]

    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        counts = conn.execute("SELECT count FROM probe_unsaved ORDER BY scan_time").fetchall()
    finally:
        conn.close()
    assert counts == [(1,), (2,), (2,)]  # the third scan is the module's second


def test_a_resumed_run_goes_on_as_if_it_had_not_stopped(processor, scans, store_env):
    first = processor()
    for m in scans[:2]:
        first.process_file(m)

    resumed = processor()  # the run is started again with its id
    assert [resumed.process_file(m) for m in scans] == [True] * 5

    assert _counts(store_env) == [
        (1, "nothing"),
        (2, "scan-1"),
        (3, "scan-2"),
        (4, "scan-3"),
        (5, "scan-4"),
    ]


def test_the_scan_that_failed_last_is_processed_again_on_resume(processor, scans, store_env):
    first = processor()
    _Flaky.failing = {5}
    assert [first.process_file(m) for m in scans[:2]] == [True, False]
    _Flaky.failing = set()  # whatever broke it is fixed

    resumed = processor()
    assert [resumed.process_file(m) for m in scans[:3]] == [True] * 3

    assert _counts(store_env) == [(1, "nothing"), (2, "scan-1"), (3, "scan-2")]
    status = store_env.collection.catalog.get_scan(store_env.run_id, scans[1]["scan_id"])["status"]
    assert status == "complete"


def test_skip_failed_leaves_a_failed_scan_as_it_is(processor, scans, store_env, make_processor):
    first = processor()
    _Flaky.failing = {5}
    for m in scans[:2]:
        first.process_file(m)
    _Flaky.failing = set()

    resumed = make_processor(config=first.config, skip_failed=True)
    assert [resumed.process_file(m) for m in scans[:3]] == [True] * 3

    status = store_env.collection.catalog.get_scan(store_env.run_id, scans[1]["scan_id"])["status"]
    assert status == "failed"
    assert _counts(store_env)[-1] == (2, "scan-1")  # the third scan follows the first


def test_a_scan_the_run_went_past_before_it_stopped_is_not_tried_again(processor, scans, store_env):
    """Its turn has passed: processing it now would put it after later scans."""
    first = processor()
    _Flaky.failing = {5}
    assert [first.process_file(m) for m in scans[:3]] == [True, False, True]
    _Flaky.failing = set()

    resumed = processor()
    for m in scans[:4]:
        resumed.process_file(m)

    status = store_env.collection.catalog.get_scan(store_env.run_id, scans[1]["scan_id"])["status"]
    assert status == "failed"
    assert _counts(store_env)[-1] == (3, "scan-2")


def test_only_the_latest_checkpoint_is_kept(processor, scans, store_env):
    proc = processor()
    for m in scans[:3]:
        proc.process_file(m)

    folder = store_env.collection.objects_dir / store_env.run_id / "checkpoint"
    row = store_env.collection.catalog.run_checkpoint(store_env.run_id)
    assert [p.name for p in folder.iterdir()] == [f"{scans[2]['scan_id']}.ckpt"]
    assert row["scan_id"] == scans[2]["scan_id"]


def test_a_pass_over_the_stored_run_leaves_its_checkpoint_alone(processor, scans, store_env):
    live = processor()
    for m in scans[:3]:
        live.process_file(m)
    before = store_env.collection.catalog.run_checkpoint(store_env.run_id)

    over_it = processor(modules=[FLAKY], source="stored_run")
    assert [over_it.process_file(m) for m in scans[:3]] == [True] * 3

    assert store_env.collection.catalog.run_checkpoint(store_env.run_id) == before


def test_a_run_whose_modules_changed_cannot_go_on_from_its_checkpoint(processor, scans, store_env):
    first = processor(modules=[FLAKY])  # carries no state
    first.process_file(scans[0])

    with pytest.raises(StoreError, match="probe_count"):
        processor(modules=[COUNTER, FLAKY])


def test_a_checkpoint_naming_a_class_that_no_longer_exists_is_refused(processor, scans, store_env):
    """A run checkpointed before its modules' classes moved cannot be resumed:
    its saved state names classes by where they lived then."""
    first = processor()
    for scan in scans[:2]:
        assert first.process_file(scan) is True
    row = store_env.collection.catalog.run_checkpoint(first.run_id)
    path = store_env.collection.objects_dir / row["object_name"]
    path.write_bytes(b"cadapt.execution.nodes.tracking\nTrackingState\n.")  # a pickled class name

    with pytest.raises(StoreError) as refusal:
        processor()

    message = str(refusal.value)
    assert first.run_id in message
    assert "adapt.execution.nodes" in message
    assert "start a new run" in message
