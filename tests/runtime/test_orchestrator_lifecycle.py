# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The orchestrator's run lifecycle, observed end to end through ``start()``.

Each test runs a real orchestrator over a synthetic local directory (one
NEXRAD-named file) in historical mode, with one fake core module, and checks
what a launcher sees: the run-registered signal, the status ``start()``
returns, and the terminal status recorded in the store registry.
"""

import json
import logging
import threading
import time

import pytest

from adapt.api import StoreClient
from adapt.contracts import ContractViolation
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.persistence.errors import StoreError
from adapt.persistence.store_registry import StoreRegistry
from adapt.runtime.orchestrator import PipelineOrchestrator
from adapt.runtime.run_reporter import RunReporter
from adapt.runtime.sources import LocalDirectorySource

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

PROBE = "lifecycle_probe"
VIOLATOR = "lifecycle_violator"


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """start() configures and shuts down root logging; give pytest its handlers back."""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved
    root.setLevel(saved_level)


@pytest.fixture
def gate():
    """Register a probe core module that processes a scan only once the gate opens."""
    opened = threading.Event()

    class _GatedProbe(BaseModule):
        name = PROBE
        inputs = ["scan_id"]
        outputs = ["lifecycle_probe_out"]

        def run(self, context: dict) -> dict:
            if not opened.wait(timeout=30):
                raise TimeoutError("test gate never opened")
            return {"lifecycle_probe_out": context["scan_id"]}

    registry.register(_GatedProbe)
    yield opened
    opened.set()
    registry.unregister(PROBE)


@pytest.fixture
def violator():
    """Register a core module whose output always violates its contract."""

    class _Violator(BaseModule):
        name = VIOLATOR
        inputs = ["scan_id"]
        outputs = ["lifecycle_violator_out"]

        def run(self, context: dict) -> dict:
            raise ContractViolation("synthetic contract violation")

    registry.register(_Violator)
    yield VIOLATOR
    registry.unregister(VIOLATOR)


def _scan_dir(parent, filename="TEST_RADAR20240518_120000_V06"):
    directory = parent / "scans"
    directory.mkdir()
    (directory / filename).write_bytes(b"x" * 6000)
    return directory


def _config(pipeline_config, source_dir, modules):
    """A finite run: every file in ``source_dir`` through ``modules`` only."""
    return pipeline_config.model_copy(
        update={
            "mode": "historical",
            "source": "local_directory",
            "source_dir": str(source_dir),
            "downloader": pipeline_config.downloader.model_copy(
                update={"start_time": "2024-05-18T11:00:00Z", "end_time": "2024-05-18T13:00:00Z"}
            ),
            "modules": modules,
            # Seed carry needs the projection module, which these runs leave out.
            "module_params": {
                **pipeline_config.module_params,
                "detection": {"seed_carry_frames": 0},
            },
        }
    )


def _run_row(config) -> dict:
    return StoreRegistry.get_instance(config.base_dir).get_run(config.run_id)


def _start_in_thread(orch: PipelineOrchestrator) -> tuple[threading.Thread, dict]:
    outcome: dict = {}

    def _run() -> None:
        outcome["status"] = orch.start()

    thread = threading.Thread(target=_run, name="TestOrchestratorRunner", daemon=True)
    thread.start()
    return thread, outcome


def test_finished_run_returns_completed_and_records_it(pipeline_config, gate, tmp_path):
    gate.set()
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])

    status = PipelineOrchestrator(config).start()

    assert status == "completed"
    assert _run_row(config)["status"] == "completed"


def test_run_record_lists_the_enabled_modules(pipeline_config, gate, tmp_path):
    gate.set()
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])

    PipelineOrchestrator(config).start()

    environment = json.loads(_run_row(config)["environment_json"])
    assert environment["enabled_modules"] == [PROBE]


def test_the_store_api_reads_the_tracking_field_from_the_run_record(
    pipeline_config, gate, tmp_path
):
    """The record names the shared settings ``global_``, the key the API reads."""
    gate.set()
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])

    PipelineOrchestrator(config).start()

    with StoreClient(config.base_dir) as client:
        assert client.run_tracking_field(config.run_id) == config.global_.tracking_field
        assert client.run_config(config.run_id)["global_"] == config.global_.model_dump()


def test_run_registered_is_set_once_the_running_run_is_recorded(pipeline_config, gate, tmp_path):
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])
    orch = PipelineOrchestrator(config)
    assert not orch.run_registered.is_set()

    thread, outcome = _start_in_thread(orch)
    try:
        assert orch.run_registered.wait(timeout=30)
        assert _run_row(config)["status"] == "running"  # the gated scan holds the run open
    finally:
        gate.set()
        thread.join(timeout=60)

    assert outcome["status"] == "completed"


def test_requested_stop_finalizes_the_run_cancelled(pipeline_config, gate, tmp_path):
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])
    orch = PipelineOrchestrator(config)

    thread, outcome = _start_in_thread(orch)
    assert orch.run_registered.wait(timeout=30)
    orch.request_stop()
    gate.set()
    thread.join(timeout=60)

    assert outcome["status"] == "cancelled"
    assert _run_row(config)["status"] == "cancelled"


def test_contract_violation_finalizes_the_run_failed(pipeline_config, violator, tmp_path):
    config = _config(pipeline_config, _scan_dir(tmp_path), [violator])

    status = PipelineOrchestrator(config).start()

    assert status == "failed"
    assert _run_row(config)["status"] == "failed"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_dead_source_finalizes_the_run_failed(pipeline_config, gate, tmp_path, monkeypatch):
    gate.set()
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])

    def _die(self) -> None:
        raise RuntimeError("synthetic source failure")

    monkeypatch.setattr(LocalDirectorySource, "run", _die)  # the source thread dies at once

    status = PipelineOrchestrator(config).start()

    assert status == "failed"
    assert _run_row(config)["status"] == "failed"


@pytest.mark.parametrize(
    "broken",
    [
        {"modules": ["no_such_module"]},  # the processor's module set cannot be built
        {"source_dir": None},  # the local-directory source cannot be built
    ],
    ids=["unknown-module", "source-without-directory"],
)
def test_startup_failure_records_no_run(pipeline_config, gate, tmp_path, broken):
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE]).model_copy(update=broken)
    orch = PipelineOrchestrator(config)

    with pytest.raises(ValueError):
        orch.start()

    assert not orch.run_registered.is_set()
    with pytest.raises(StoreError, match="does not exist"):
        _run_row(config)


def test_error_after_the_run_is_recorded_finalizes_it_failed(
    pipeline_config, gate, tmp_path, monkeypatch
):
    gate.set()
    config = _config(pipeline_config, _scan_dir(tmp_path), [PROBE])

    def _boom(self, lines):
        raise RuntimeError("synthetic header failure")

    monkeypatch.setattr(RunReporter, "methods", _boom)

    with pytest.raises(RuntimeError, match="synthetic header failure"):
        PipelineOrchestrator(config).start()

    assert _run_row(config)["status"] == "failed"


def test_a_stop_request_starts_no_new_scan(pipeline_config, gate, tmp_path):
    """Three queued scans; the stop is requested while the first is in flight: that
    scan finishes and is saved, the other two are never started."""
    directory = tmp_path / "scans"
    directory.mkdir()
    for stamp in ("120000", "120500", "121000"):
        (directory / f"TEST_RADAR20240518_{stamp}_V06").write_bytes(b"x" * 6000)
    config = _config(pipeline_config, directory, [PROBE])
    orch = PipelineOrchestrator(config)
    thread, outcome = _start_in_thread(orch)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:  # the first scan is in flight once the probe waits
        if orch.processor is not None and orch.processor.current_activity() is not None:
            break
        time.sleep(0.02)

    orch.request_stop()
    gate.set()
    thread.join(timeout=60)

    assert outcome["status"] == "cancelled"
    assert _run_row(config)["scans_processed"] == 1
