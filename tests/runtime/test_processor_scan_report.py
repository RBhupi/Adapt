# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""What the processor reads of a scan's results: the facts a module reports.

The radar's location and the scan's cell count are outputs under the keys of
``adapt.contracts`` (``scan_report``); the processor reads those keys and no
module's dataset or table. Real processor, executor and store; the modules are
small stand-ins registered for the test.
"""

import logging
import random
from datetime import UTC, datetime, timedelta

import pytest

from adapt.contracts import (
    RADAR_LOCATION,
    SCAN_CELL_COUNT,
    RadarLocation,
    check_radar_location,
    check_scan_cell_count,
)
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.runtime.observability import ObsSettings, build_observability
from tests.helpers.queue_msg import msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

T0 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
LOCATOR, SILENT, COUNTER, LATE_COUNTER = (
    "probe_locator",
    "probe_silent",
    "probe_counter",
    "probe_late_counter",
)
_PROCESSOR_LOG = "adapt.runtime.processor"


class _Locator(BaseModule):
    """Reports where the radar is, and nothing else."""

    name = LOCATOR
    inputs = ["scan_time"]
    outputs = [RADAR_LOCATION]
    output_contracts = {RADAR_LOCATION: check_radar_location}

    def run(self, context: dict) -> dict:
        return {RADAR_LOCATION: RadarLocation(latitude=34.93, longitude=-86.08)}


class _Silent(BaseModule):
    """Always runs and reports nothing the runtime reads."""

    name = SILENT
    inputs = ["scan_time"]
    outputs = ["probe_silent_out"]

    def run(self, context: dict) -> dict:
        return {"probe_silent_out": 1}


class _Counter(BaseModule):
    """Reports seven cells in every scan."""

    name = COUNTER
    inputs = ["scan_time"]
    outputs = [SCAN_CELL_COUNT]
    output_contracts = {SCAN_CELL_COUNT: check_scan_cell_count}

    def run(self, context: dict) -> dict:
        return {SCAN_CELL_COUNT: 7}


class _LateCounter(_Counter):
    """Reports seven cells, once it has one earlier scan."""

    name = LATE_COUNTER
    required_history = 2


_PROBES = (_Locator, _Silent, _Counter, _LateCounter)


@pytest.fixture(autouse=True)
def probes():
    for probe in _PROBES:
        registry.register(probe)
    yield
    for probe in _PROBES:
        registry.unregister(probe.name)


class _Reporter:
    """Records the cell count each scan's console line is given."""

    def __init__(self) -> None:
        self.counts: list[int | None] = []

    def scan(self, scan_id, spans, n_cells):
        self.counts.append(n_cells)


def _obs():
    return build_observability(
        ObsSettings(),
        clock=lambda: 0.0,
        wall_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        rng=random.Random(0),
    )


@pytest.fixture
def processor(make_processor, pipeline_config):
    def _processor(modules, **kw):
        source = kw.pop("source", pipeline_config.source)
        config = pipeline_config.model_copy(update={"modules": list(modules), "source": source})
        return make_processor(config=config, **kw)

    return _processor


@pytest.fixture
def scans(store_env, tmp_path):
    """Two acquired scans, five minutes apart."""
    return [
        msg(store_env, tmp_path, f"f{i}", scan_time=T0 + timedelta(minutes=5 * i)) for i in (0, 1)
    ]


def _location(store_env):
    row = store_env.registry.get_collection("TEST_RADAR")
    return row["location_lat"], row["location_lon"]


def _warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _PROCESSOR_LOG and r.levelno == logging.WARNING
    ]


def test_the_location_a_module_reports_is_recorded_for_the_collection(processor, scans, store_env):
    proc = processor([LOCATOR])

    assert proc.process_file(scans[0]) is True

    assert _location(store_env) == (34.93, -86.08)


def test_a_run_on_new_scans_without_a_location_reporter_says_so_at_start(processor, caplog):
    caplog.set_level(logging.WARNING, logger=_PROCESSOR_LOG)

    processor([SILENT])

    (warning,) = _warnings(caplog)
    assert "radar_location" in warning


def test_a_run_with_a_location_reporter_does_not_warn(processor, caplog):
    caplog.set_level(logging.WARNING, logger=_PROCESSOR_LOG)

    processor([LOCATOR, SILENT])

    assert _warnings(caplog) == []


def test_a_pass_over_a_stored_run_needs_no_location_reporter(processor, scans, store_env, caplog):
    live = processor([LOCATOR])
    for m in scans:
        live.process_file(m)
    before = _location(store_env)
    caplog.clear()
    caplog.set_level(logging.WARNING, logger=_PROCESSOR_LOG)

    over_it = processor([SILENT], source="stored_run")

    assert [over_it.process_file(m) for m in scans] == [True, True]
    assert _warnings(caplog) == []
    assert _location(store_env) == before


def test_the_count_a_module_reports_is_the_scans_cell_count(processor, scans, caplog):
    caplog.set_level(logging.INFO, logger=_PROCESSOR_LOG)
    obs, reporter = _obs(), _Reporter()
    proc = processor([COUNTER], observability=obs, reporter=reporter)

    assert proc.process_file(scans[0]) is True

    assert reporter.counts == [7]
    assert obs.metrics.counter_total("cells_detected_total") == 7.0
    assert any(r.getMessage().startswith("Processed: 7 cells | ") for r in caplog.records)


def test_a_scan_the_counting_module_sat_out_counts_no_cells(processor, scans):
    obs, reporter = _obs(), _Reporter()
    proc = processor([SILENT, LATE_COUNTER], observability=obs, reporter=reporter)

    assert [proc.process_file(m) for m in scans] == [True, True]

    assert reporter.counts == [0, 7]


def test_without_a_counting_module_the_scan_carries_no_cell_count(processor, scans, caplog):
    caplog.set_level(logging.INFO, logger=_PROCESSOR_LOG)
    obs, reporter = _obs(), _Reporter()
    proc = processor([SILENT], observability=obs, reporter=reporter)

    assert proc.process_file(scans[0]) is True

    assert reporter.counts == [None]
    messages = [r.getMessage() for r in caplog.records]
    (processed,) = [m for m in messages if m.startswith("Processed")]
    assert "cells" not in processed
    assert obs.metrics.counter_total("cells_detected_total") == 0.0
