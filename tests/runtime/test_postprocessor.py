# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""PostProcessor enriches an existing stored run by composing the shared infra.

It reuses the registry, GraphBuilder/GraphExecutor (the real resolution path),
and the store's frozen-schema writer — module-owned extension tables only,
never tracking core tables.
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from itertools import count
from random import Random

import pandas as pd
import pytest

from adapt.contracts import ProductTableWrite
from adapt.execution.module_registry import registry
from adapt.modules.base import POSTPROCESS_PHASE, BaseModule
from adapt.runtime import store_inputs
from adapt.runtime.observability import ObsSettings, build_observability
from adapt.runtime.postprocessor import PostProcessor

pytestmark = [pytest.mark.unit, pytest.mark.pipeline]


@pytest.fixture(autouse=True)
def _clean_xlma_registration():
    """PostProcessor.run auto-imports the real xlma_stat node (postprocess_defaults).

    Remove it from the registry and the import cache after each test so the
    global singleton stays clean for the rest of the session and re-imports
    register it fresh next time.
    """
    yield
    registry.unregister("xlma_stat")
    sys.modules.pop("adapt.execution.nodes.xlma_stat", None)


class _FakePostModule(BaseModule):
    name = "fake_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["run_id"]
    outputs = ["fake_rows"]
    persistence = (
        ProductTableWrite(key="fake_rows", table="fake_ext", primary_key=("run_id", "cell_uid")),
    )

    def run(self, context: dict) -> dict:
        return {
            "fake_rows": pd.DataFrame(
                [{"run_id": context["run_id"], "cell_uid": "abc", "value": 1.0}]
            )
        }


class _CoreWritingModule(BaseModule):
    name = "rogue_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["run_id"]
    outputs = ["rogue_rows"]
    persistence = (
        ProductTableWrite(
            key="rogue_rows", table="cells_by_scan", primary_key=("run_id", "cell_uid")
        ),
    )

    def run(self, context: dict) -> dict:
        return {"rogue_rows": pd.DataFrame([{"run_id": context["run_id"], "cell_uid": "x"}])}


class _TwoTableModule(BaseModule):
    name = "two_table_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["run_id"]
    outputs = ["rows_a", "rows_b"]
    persistence = (
        ProductTableWrite(key="rows_a", table="ext_a", primary_key=("cell_uid",)),
        ProductTableWrite(key="rows_b", table="ext_b", primary_key=("flash_id",)),
    )

    def run(self, context: dict) -> dict:
        return {
            "rows_a": pd.DataFrame([{"cell_uid": "c1", "v": 1.0}]),
            "rows_b": pd.DataFrame([{"flash_id": 7, "w": 2.0}]),
        }


def _register(module_cls):
    registry.register(module_cls)
    return module_cls.name


def test_module_can_write_multiple_extension_tables(pipeline_config, store_env):
    _register(_TwoTableModule)
    try:
        PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        ).run(modules=["two_table_post"])
        conn = sqlite3.connect(store_env.collection.products_path)
        try:
            a = conn.execute("SELECT cell_uid, v FROM ext_a").fetchall()
            b = conn.execute("SELECT flash_id, w FROM ext_b").fetchall()
        finally:
            conn.close()
        assert a == [("c1", 1.0)]
        assert b == [(7, 2.0)]
    finally:
        registry.unregister("two_table_post")


def test_runs_single_module_and_writes_extension_table(pipeline_config, store_env):
    _register(_FakePostModule)
    try:
        pp = PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        )
        result = pp.run(modules=["fake_post"])

        # DAG resolution path produced the module's output into the context.
        assert "fake_rows" in result

        conn = sqlite3.connect(store_env.collection.products_path)
        try:
            rows = conn.execute("SELECT cell_uid, value FROM fake_ext").fetchall()
        finally:
            conn.close()
        assert rows == [("abc", 1.0)]
    finally:
        registry.unregister("fake_post")


def test_cannot_write_core_table(pipeline_config, store_env):
    from adapt.persistence.errors import StoreError

    _register(_CoreWritingModule)
    try:
        pp = PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        )
        with pytest.raises(StoreError, match="tracking core table"):
            pp.run(modules=["rogue_post"])
    finally:
        registry.unregister("rogue_post")


class _NeedsMasksModule(BaseModule):
    name = "needs_masks_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["minute_masks", "radar_origin"]
    outputs = ["probe_rows"]
    persistence = (ProductTableWrite(key="probe_rows", table="probe_ext", primary_key=("n",)),)
    captured: dict = {}

    def run(self, context: dict) -> dict:
        _NeedsMasksModule.captured = {
            "n_masks": len(context["minute_masks"]),
            "radar_origin": context["radar_origin"],
        }
        return {"probe_rows": pd.DataFrame([{"n": len(context["minute_masks"]), "ok": 1}])}


def test_injects_minute_masks_and_radar_origin(pipeline_config, store_env):
    from adapt.persistence.objects import ArtifactMeta, ObjectStore
    from tests.helpers.analysis_nc import cell_block, make_analysis_ds

    ds = make_analysis_ds(
        "2024-05-18T12:03:00",
        "2024-05-18T12:00:00",
        cell_labels=cell_block(col=6),
        cell_uids=["uid-A"],
        minute_labels={
            "2024-05-18T12:01:00": cell_block(col=4),
            "2024-05-18T12:02:00": cell_block(col=5),
            "2024-05-18T12:03:00": cell_block(col=6),
        },
        registration_uids=["uid-A"],
    )
    objects = ObjectStore(store_env.collection.objects_dir, store_env.collection.catalog)
    handle = objects.begin(suffix=".nc")
    ds.to_netcdf(handle.staging_path)
    objects.commit(
        handle,
        ArtifactMeta(
            artifact_type="segmentation2d",
            producer="test",
            run_id=store_env.run_id,
            scan_id="sid-mask",
            observation_time=datetime(2024, 5, 18, 12, 3, 0, tzinfo=UTC),
        ),
    )
    store_env.registry.ensure_collection_location("TEST_RADAR", lat=40.0, lon=-88.0)

    _register(_NeedsMasksModule)
    try:
        PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        ).run(modules=["needs_masks_post"])
        # 12:01, 12:02 advected + 12:03 real segmentation (replaces fraction-1 frame)
        assert _NeedsMasksModule.captured["n_masks"] == 3
        assert _NeedsMasksModule.captured["radar_origin"] == (40.0, -88.0)
    finally:
        registry.unregister("needs_masks_post")


def test_unknown_module_name_raises(pipeline_config, store_env):
    _register(_FakePostModule)
    try:
        pp = PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        )
        with pytest.raises(ValueError, match="Unknown module"):
            pp.run(modules=["does_not_exist"])
    finally:
        registry.unregister("fake_post")


class _CustomInputModule(BaseModule):
    name = "custom_input_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["probe_feed", "run_id"]
    outputs = ["custom_rows"]
    persistence = (
        ProductTableWrite(key="custom_rows", table="custom_ext", primary_key=("run_id", "k")),
    )

    def run(self, context: dict) -> dict:
        return {
            "custom_rows": pd.DataFrame(
                [{"run_id": context["run_id"], "k": context["probe_feed"]["k"], "v": 1.0}]
            )
        }


def test_custom_store_input_provider_feeds_module(pipeline_config, store_env):
    store_inputs.register_store_input("probe_feed", lambda col, reg, run_id: {"k": run_id})
    _register(_CustomInputModule)
    try:
        PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        ).run(modules=["custom_input_post"])
        conn = sqlite3.connect(store_env.collection.products_path)
        try:
            rows = conn.execute("SELECT k, v FROM custom_ext").fetchall()
        finally:
            conn.close()
        assert rows == [(store_env.run_id, 1.0)]
    finally:
        registry.unregister("custom_input_post")
        store_inputs.unregister_store_input("probe_feed")


class _GhostInputModule(BaseModule):
    name = "ghost_input_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["no_such_input", "run_id"]
    outputs = ["ghost_rows"]

    def run(self, context: dict) -> dict:
        return {"ghost_rows": pd.DataFrame()}


def test_missing_store_input_fails_fast_with_names(pipeline_config, store_env):
    _register(_GhostInputModule)
    try:
        pp = PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        )
        with pytest.raises(ValueError, match="no_such_input") as excinfo:
            pp.run(modules=["ghost_input_post"])
        assert "minute_masks" in str(excinfo.value)  # lists available providers
    finally:
        registry.unregister("ghost_input_post")


class _ProducerModule(BaseModule):
    name = "producer_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["run_id"]
    outputs = ["derived_x"]

    def run(self, context: dict) -> dict:
        return {"derived_x": {"marker": context["run_id"]}}


class _ChainedModule(BaseModule):
    name = "chained_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["derived_x", "run_id"]
    outputs = ["chained_rows"]
    persistence = (
        ProductTableWrite(key="chained_rows", table="chained_ext", primary_key=("run_id",)),
    )

    def run(self, context: dict) -> dict:
        return {
            "chained_rows": pd.DataFrame(
                [{"run_id": context["run_id"], "marker": context["derived_x"]["marker"]}]
            )
        }


class _FakeLiveModule(BaseModule):
    name = "fake_live"
    pipeline_phase = 0
    inputs = ["run_id"]
    outputs = ["fake_live_rows"]

    def run(self, context: dict) -> dict:
        return {}


def test_requesting_live_module_raises(pipeline_config, store_env):
    registry.register(_FakeLiveModule)
    try:
        pp = PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        )
        with pytest.raises(ValueError, match="not a post-process module"):
            pp.run(modules=["fake_live"])
    finally:
        registry.unregister("fake_live")


def _test_observability():
    """Deterministic provider: counting clock, fixed wall clock, seeded rng."""
    ticks = count()
    return build_observability(
        ObsSettings(enabled=True),
        clock=lambda: float(next(ticks)),
        wall_clock=lambda: datetime(2024, 5, 18, 12, 0, 0, tzinfo=UTC),
        rng=Random(0),
    )


def test_records_module_execution_history(pipeline_config, store_env):
    _register(_FakePostModule)
    try:
        PostProcessor(
            store_env.collection,
            store_env.registry,
            store_env.run_id,
            pipeline_config,
            observability=_test_observability(),
        ).run(modules=["fake_post"])
        rows = {r["module"]: r for r in store_env.registry.run_modules(store_env.run_id)}
        assert "fake_post" in rows
        assert rows["fake_post"]["status"] == "completed"
        assert rows["fake_post"]["duration_seconds"] >= 0.0
        assert json.loads(rows["fake_post"]["detail_json"])["calls"] == 1
    finally:
        registry.unregister("fake_post")


class _ExplodingModule(BaseModule):
    name = "exploding_post"
    pipeline_phase = POSTPROCESS_PHASE
    inputs = ["run_id"]
    outputs = ["boom_rows"]

    def run(self, context: dict) -> dict:
        raise RuntimeError("boom")


def test_failed_module_recorded_as_failed(pipeline_config, store_env):
    _register(_ExplodingModule)
    try:
        pp = PostProcessor(
            store_env.collection,
            store_env.registry,
            store_env.run_id,
            pipeline_config,
            observability=_test_observability(),
        )
        with pytest.raises(RuntimeError, match="boom"):
            pp.run(modules=["exploding_post"])
        rows = {r["module"]: r for r in store_env.registry.run_modules(store_env.run_id)}
        assert rows["exploding_post"]["status"] == "failed"
    finally:
        registry.unregister("exploding_post")


def test_no_observability_records_nothing(pipeline_config, store_env):
    _register(_FakePostModule)
    try:
        PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        ).run(modules=["fake_post"])
        assert store_env.registry.run_modules(store_env.run_id) == []
    finally:
        registry.unregister("fake_post")


def test_sibling_produced_input_needs_no_provider(pipeline_config, store_env):
    _register(_ProducerModule)
    _register(_ChainedModule)
    try:
        PostProcessor(
            store_env.collection, store_env.registry, store_env.run_id, pipeline_config
        ).run(modules=["producer_post", "chained_post"])
        conn = sqlite3.connect(store_env.collection.products_path)
        try:
            rows = conn.execute("SELECT marker FROM chained_ext").fetchall()
        finally:
            conn.close()
        assert rows == [(store_env.run_id,)]
    finally:
        registry.unregister("producer_post")
        registry.unregister("chained_post")
