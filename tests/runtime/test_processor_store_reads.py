# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A module's declared store read is made just before it runs.

Real processor, real executor, real store; the modules are small probes
registered for the test: a writer of per-scan rows and a reader of them, which
stores what it was handed.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from adapt.contracts import ProductTableWrite, StoreRead
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from tests.helpers.queue_msg import msg

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]

T1 = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
_ROWS = ProductTableWrite(
    key="probe_rows", table="probe_rows", primary_key=("run_id", "scan_id", "k")
)


class _Reader(BaseModule):
    """Stores how many rows of ``probe_rows`` it was handed, and of which scans."""

    name = "probe_reader"
    inputs = ["probe_so_far"]
    outputs = ["probe_seen"]
    persistence = (
        ProductTableWrite(key="probe_seen", table="probe_seen", primary_key=("run_id", "scan_id")),
    )
    store_reads = (StoreRead(key="probe_so_far", source="probe_rows", up_to_scan=True),)

    def run(self, context: dict) -> dict:
        rows = context["probe_so_far"]
        return {"probe_seen": pd.DataFrame([{"scans": ",".join(rows["source_file"])}])}


class _Writer(BaseModule):
    """Writes one row per scan, naming the scan's source file."""

    name = "probe_writer"
    inputs = ["nexrad_file"]
    outputs = ["probe_rows"]
    persistence = (_ROWS,)

    def run(self, context: dict) -> dict:
        source = str(context["nexrad_file"]).rsplit("/", 1)[-1]
        return {"probe_rows": pd.DataFrame([{"k": "a", "source_file": source}])}


class _BreakingWriter(BaseModule):
    name = "probe_breaking_writer"
    inputs = ["scan_time"]
    outputs = ["probe_breaking_rows"]
    persistence = (
        ProductTableWrite(
            key="probe_breaking_rows", table="probe_rows", primary_key=("run_id", "scan_id", "k")
        ),
    )

    def run(self, context: dict) -> dict:
        raise ValueError("kaboom")


# The reader is registered first: a run lists the registry's order, so it comes
# before the writer, and only the declared read can put it after.
_PROBES = (_Reader, _Writer, _BreakingWriter)


@pytest.fixture
def probes():
    for probe in _PROBES:
        registry.register(probe)
    yield
    for probe in _PROBES:
        registry.unregister(probe.name)


def _seen(store_env) -> list[str]:
    conn = sqlite3.connect(str(store_env.collection.products_path))
    try:
        return [scans for (scans,) in conn.execute("SELECT scans FROM probe_seen ORDER BY scans")]
    finally:
        conn.close()


def _scans(store_env, tmp_path) -> list[dict]:
    return [
        msg(store_env, tmp_path, f"f{i}", scan_time=T1 + timedelta(minutes=5 * i)) for i in (1, 2)
    ]


def test_a_reader_gets_this_scans_rows_although_it_is_listed_before_the_writer(
    probes, make_processor, pipeline_config, store_env, tmp_path
):
    config = pipeline_config.model_copy(update={"modules": ["probe_reader", "probe_writer"]})
    proc = make_processor(config=config)

    assert [proc.process_file(message) for message in _scans(store_env, tmp_path)] == [True, True]

    assert _seen(store_env) == ["f1", "f1,f2"]


def test_a_reader_is_skipped_in_a_scan_whose_writer_failed(
    probes, make_processor, pipeline_config, store_env, tmp_path
):
    config = pipeline_config.model_copy(
        update={"modules": ["probe_reader", "probe_breaking_writer"]}
    )
    proc = make_processor(config=config)
    message = msg(store_env, tmp_path, "f1", scan_time=T1)

    assert proc.process_file(message) is False

    fates = store_env.collection.catalog.scan_modules(store_env.run_id, message["scan_id"])
    assert fates["probe_reader"]["status"] == "skipped"
    assert fates["probe_reader"]["reason"] == "needs 'probe_breaking_writer', which failed"


def test_over_a_stored_run_a_reader_listed_alone_reads_what_the_run_stored(
    probes, make_processor, pipeline_config, store_env, tmp_path
):
    live = make_processor(config=pipeline_config.model_copy(update={"modules": ["probe_writer"]}))
    assert [live.process_file(message) for message in _scans(store_env, tmp_path)] == [True, True]

    stored = pipeline_config.model_copy(
        update={"source": "stored_run", "modules": ["probe_reader"]}
    )
    proc = make_processor(config=stored)
    assert [proc.process_file(m) for m in store_env.acquirer.stored_scans()] == [True, True]

    assert _seen(store_env) == ["f1", "f1,f2"]
