# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for RadarProcessor extended-module (post-persistence) writes.

An extended module's returned DataFrame is written generically via the module's
declared table spec, stamped with the scan's identity. Which extended modules
run on a scan is the graph's business (``tests/execution/test_scan_graph.py``).
"""

import queue
import sqlite3

import pandas as pd
import pytest

from adapt.contracts import ProductTableWrite
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.runtime.processor import RadarProcessor

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


class _ProbeEnrichModule(BaseModule):
    """Synthetic phase-3 module that emits a fixed DataFrame."""

    name = "enrich_probe"
    pipeline_phase = 3
    required_history = 1
    inputs = ["scan_time"]
    outputs = ["enrich_probe_rows"]
    persistence = (
        ProductTableWrite(
            key="enrich_probe_rows",
            table="enrich_probe",
            primary_key=("run_id", "scan_id", "cell_uid"),
            index_columns=("cell_uid",),
        ),
    )

    def run(self, context: dict) -> dict:
        # Identity columns are stamped by the store writer — never module-supplied.
        return {"enrich_probe_rows": pd.DataFrame([{"cell_uid": "a", "v": 1.0}])}


@pytest.fixture
def proc_with_enrich(pipeline_config, store_env):
    registry.register(_ProbeEnrichModule)
    try:
        proc = RadarProcessor(
            queue.Queue(),
            pipeline_config,
            collection=store_env.collection,
            registry=store_env.registry,
            run_id=store_env.run_id,
            history=store_env.history,
        )
        yield proc
    finally:
        registry.unregister("enrich_probe")


class TestEnrichWrite:
    def test_enrich_results_routed_to_declared_table(self, proc_with_enrich, store_env):
        from datetime import UTC, datetime

        from adapt.contracts import PersistenceMeta
        from adapt.contracts.persistence import ScanRecord

        store_env.collection.catalog.register_scan(
            ScanRecord(
                run_id=store_env.run_id,
                scan_id="sid-test",
                scan_time=datetime(2024, 1, 1, tzinfo=UTC),
                source_file_name="probe",
            )
        )
        ext_result = _ProbeEnrichModule().run({})
        meta = PersistenceMeta(
            scan_time=datetime(2024, 1, 1, tzinfo=UTC),
            scan_id="sid-test",
            run_id=store_env.run_id,
            source_file="",
            collection_id="TEST_RADAR",
        )
        proc_with_enrich._router.persist(proc_with_enrich._extended_modules, ext_result, meta)

        conn = sqlite3.connect(str(store_env.collection.products_path))
        try:
            rows = conn.execute("SELECT run_id, scan_id, cell_uid, v FROM enrich_probe").fetchall()
        finally:
            conn.close()
        # The writer stamped run and scan identity from PersistenceMeta — enrich
        # modules never supply them.
        assert rows == [(store_env.run_id, "sid-test", "a", 1.0)]


def test_a_processed_scan_stores_its_extended_modules_rows(
    proc_with_enrich, store_env, tmp_path, monkeypatch
):
    """process_file runs the extended pass after the core one and stores what it returns."""
    from datetime import UTC, datetime

    from adapt.execution.graph.executor import GraphRun
    from tests.helpers.fake_graph import NEEDS_PRIOR_SCAN
    from tests.helpers.queue_msg import msg

    # Stand in for the core pass only (a first scan); the extended pass and the store are real.
    monkeypatch.setattr(
        proc_with_enrich._core_executor,
        "run",
        lambda context, *, history, skipped=frozenset(): GraphRun(
            context=dict(context), skipped=NEEDS_PRIOR_SCAN
        ),
    )
    message = msg(store_env, tmp_path, "f1", scan_time=datetime(2024, 1, 1, tzinfo=UTC))

    assert proc_with_enrich.process_file(message) is True

    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        rows = conn.execute("SELECT run_id, scan_id, cell_uid, v FROM enrich_probe").fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert rows == [(store_env.run_id, message["scan_id"], "a", 1.0)]
    assert "cell_volume_stats" not in tables  # downstream of tracking, skipped on a first scan
