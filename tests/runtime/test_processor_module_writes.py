# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Each module's outputs are stored as soon as it finishes.

Real processor, real executor, real store; the modules are small probes
registered for the test, so nothing here stands in for the graph.
"""

import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

from adapt.contracts import ProductTableWrite
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from tests.helpers.queue_msg import msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

T1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def _table(name: str) -> tuple[ProductTableWrite, ...]:
    return (
        ProductTableWrite(key=f"{name}_rows", table=name, primary_key=("run_id", "scan_id", "k")),
    )


class _First(BaseModule):
    """Writes one row per scan; identity columns come from the store writer."""

    name = "probe_first"
    inputs = ["scan_time"]
    outputs = ["probe_first_rows"]
    persistence = _table("probe_first")

    def run(self, context: dict) -> dict:
        return {"probe_first_rows": pd.DataFrame([{"k": "a", "v": 1.0}])}


class _Breaks(BaseModule):
    name = "probe_breaks"
    inputs = ["probe_first_rows"]
    outputs = ["probe_breaks_rows"]
    persistence = _table("probe_breaks")

    def run(self, context: dict) -> dict:
        raise ValueError("kaboom")


class _AfterBroken(BaseModule):
    name = "probe_after_broken"
    inputs = ["probe_breaks_rows"]
    outputs = ["probe_after_broken_rows"]
    persistence = _table("probe_after_broken")

    def run(self, context: dict) -> dict:
        return {"probe_after_broken_rows": pd.DataFrame([{"k": "a", "v": 3.0}])}


class _Beside(BaseModule):
    """Needs only the first module, so a failure beside it does not stop it."""

    name = "probe_beside"
    inputs = ["probe_first_rows"]
    outputs = ["probe_beside_rows"]
    persistence = _table("probe_beside")

    def run(self, context: dict) -> dict:
        return {"probe_beside_rows": pd.DataFrame([{"k": "a", "v": 2.0}])}


_PROBES = (_First, _Breaks, _AfterBroken, _Beside)


@pytest.fixture
def probes():
    for probe in _PROBES:
        registry.register(probe)
    yield
    for probe in _PROBES:
        registry.unregister(probe.name)


def _processor(make_processor, pipeline_config, modules):
    return make_processor(config=pipeline_config.model_copy(update={"modules": modules}))


def _rows(store_env, table):
    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if exists is None:
            return None
        return conn.execute(f"SELECT run_id, scan_id, k, v FROM {table}").fetchall()
    finally:
        conn.close()


def test_a_modules_rows_are_stored_with_the_scans_identity(
    probes, make_processor, pipeline_config, store_env, tmp_path
):
    proc = _processor(make_processor, pipeline_config, ["probe_first"])
    message = msg(store_env, tmp_path, "f1", scan_time=T1)

    assert proc.process_file(message) is True

    assert _rows(store_env, "probe_first") == [(store_env.run_id, message["scan_id"], "a", 1.0)]
    assert "probe_first" in store_env.collection.catalog.scan_products(
        store_env.run_id, message["scan_id"]
    )


def test_what_finished_before_a_failure_stays_stored(
    probes, make_processor, pipeline_config, store_env, tmp_path
):
    proc = _processor(
        make_processor,
        pipeline_config,
        ["probe_first", "probe_breaks", "probe_after_broken", "probe_beside"],
    )
    message = msg(store_env, tmp_path, "f1", scan_time=T1)

    assert proc.process_file(message) is False

    assert len(_rows(store_env, "probe_first")) == 1
    assert len(_rows(store_env, "probe_beside")) == 1  # did not need the broken module
    assert _rows(store_env, "probe_after_broken") is None  # needed it: never ran
    catalog = store_env.collection.catalog
    fates = catalog.scan_modules(store_env.run_id, message["scan_id"])
    assert {name: row["status"] for name, row in fates.items()} == {
        "probe_first": "done",
        "probe_breaks": "failed",
        "probe_after_broken": "skipped",
        "probe_beside": "done",
    }
    assert fates["probe_breaks"]["reason"] == "ValueError: kaboom"
    assert fates["probe_after_broken"]["reason"] == "needs 'probe_breaks', which failed"
    assert catalog.get_scan(store_env.run_id, message["scan_id"])["status"] == "failed"
