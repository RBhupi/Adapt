# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``wall_time``: the machine's time the processor hands each scan's modules."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from tests.helpers.queue_msg import msg

pytestmark = pytest.mark.unit

WATCH, LATER = "probe_watch", "probe_later"
T0 = datetime(2024, 5, 18, 12, 0, tzinfo=UTC)
PAUSE = timedelta(milliseconds=30)


class _Watch(BaseModule):
    """Notes the machine's time it is told, then takes a moment over its work."""

    name = WATCH
    inputs = ["scan_time", "wall_time"]
    outputs = ["watch_out"]
    told: list[tuple[datetime, datetime]] = []

    def run(self, context: dict) -> dict:
        self.told.append((context["scan_time"], context["wall_time"]))
        time.sleep(PAUSE.total_seconds())
        return {"watch_out": 1}


class _Later(BaseModule):
    """Runs after ``_Watch`` and notes the machine's time it is told."""

    name = LATER
    inputs = ["watch_out", "wall_time"]
    outputs = ["later_out"]
    told: list[datetime] = []

    def run(self, context: dict) -> dict:
        self.told.append(context["wall_time"])
        return {"later_out": 1}


@pytest.fixture(autouse=True)
def probes():
    for probe in (_Watch, _Later):
        registry.register(probe)
        probe.told = []
    yield
    for name in (WATCH, LATER):
        registry.unregister(name)


@pytest.fixture
def run_scans(make_processor, pipeline_config, store_env, tmp_path):
    def _run(count: int) -> None:
        config = pipeline_config.model_copy(update={"modules": [WATCH, LATER]})
        proc = make_processor(config=config)
        for i in range(count):
            scan = msg(store_env, tmp_path, f"f{i}", scan_time=T0 + timedelta(minutes=5 * i))
            assert proc.process_file(scan) is True

    return _run


def test_a_module_is_told_the_machines_time_not_the_scans(run_scans):
    before = datetime.now(UTC)
    run_scans(2)
    after = datetime.now(UTC)

    (first_scan, first_wall), (second_scan, second_wall) = _Watch.told
    assert (first_scan, second_scan) == (T0, T0 + timedelta(minutes=5))
    assert first_wall.utcoffset() == timedelta(0)
    assert before <= first_wall <= second_wall <= after


def test_the_time_is_read_as_each_module_starts_not_once_for_the_scan(run_scans):
    run_scans(1)

    ((_, watch_wall),) = _Watch.told
    (later_wall,) = _Later.told
    assert later_wall - watch_wall >= PAUSE  # the earlier module's work is counted
