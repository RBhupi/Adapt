# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""StoreOutputRouter: spec dispatch onto the store (objects + products + links)."""

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
import xarray as xr

from adapt.contracts.persistence import (
    NetcdfArtifact,
    PersistenceMeta,
    ProductTableWrite,
    ProductView,
    ScanRecord,
)
from adapt.modules.tracking.tables import Lifecycle
from adapt.persistence.errors import StoreError
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store, init_store
from tests.helpers.tracking_store import (
    TRACKING_TABLES,
    VIEWS,
    going_on,
    no_adjacency,
    tracking_outputs,
)

pytestmark = pytest.mark.unit

SCAN_TIME = datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC)
RUN = "run-1"
SID = "abc123"

NC_SPEC = NetcdfArtifact(key="analysis_ds", product_type="segmentation2d", description="test")
NC_NAME = "run-1/tracking/KILX20260813_173158_V06.segmentation2d.nc"
TABLE_SPEC = ProductTableWrite(
    key="cell_stats", table="cell_stats", primary_key=("run_id", "scan_id", "cell_label")
)


def _module(name: str, *specs) -> SimpleNamespace:
    return SimpleNamespace(name=name, persistence=tuple(specs))


def _meta(
    scan_id: str | None = SID, scan_time: datetime | None = SCAN_TIME, run_id: str = RUN
) -> PersistenceMeta:
    return PersistenceMeta(
        scan_time=scan_time,
        scan_id=scan_id,
        run_id=run_id,
        source_file="KILX20260813_173158_V06",
        collection_id="KILX",
    )


def _dataset() -> xr.Dataset:
    return xr.Dataset({"reflectivity": ("x", [1.0, 2.0])})


def _tracking_result() -> dict:
    tracked = pd.DataFrame(
        {"cell_label": [1], "cell_uid": ["u1"], "area": [10.0], "max_reflectivity": [45.0]}
    )
    tables, _ = tracking_outputs(
        tracked,
        going_on(tracked),
        no_adjacency(),
        scan_id=SID,
        scan_time=SCAN_TIME,
        lifecycle=Lifecycle(),
    )
    return {"cell_stats": pd.DataFrame({"cell_label": [1], "cell_area_sqkm": [10.0]}), **tables}


@pytest.fixture
def store(tmp_path):
    root = init_store(tmp_path / "store")
    st = Store.open(root)
    yield st
    st.close()


@pytest.fixture
def collection(store):
    return store.collection("KILX")


@pytest.fixture
def raw_artifact_id(collection):
    """The raw Level-II object every per-scan product descends from."""
    from adapt.persistence.objects import ArtifactMeta, ObjectStore

    objects = ObjectStore(collection.objects_dir, collection.catalog)
    handle = objects.begin("raw/KILX20260813_173158_V06")
    handle.staging_path.write_bytes(b"level2")
    record = objects.commit(
        handle,
        ArtifactMeta(
            artifact_type="raw_volume",
            producer="acquisition",
            run_id=RUN,
            scan_id=SID,
            observation_time=SCAN_TIME,
            original_filename="KILX20260813_173158_V06",
            source_uri="s3://nexrad/KILX20260813_173158_V06",
        ),
    )
    collection.catalog.register_scan(
        ScanRecord(
            run_id=RUN,
            scan_id=SID,
            scan_time=SCAN_TIME,
            source_file_name="KILX20260813_173158_V06",
        )
    )
    return record.artifact_id


@pytest.fixture
def router(collection):
    return StoreOutputRouter(collection, VIEWS)


def _products_rows(collection, sql):
    conn = sqlite3.connect(collection.products_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


class TestNetcdfArtifact:
    def test_writes_object_with_catalog_row_lineage_and_link(
        self, router, collection, raw_artifact_id
    ):
        ds = _dataset()

        router.persist([_module("tracking", NC_SPEC)], {"analysis_ds": ds}, _meta())

        products = collection.catalog.scan_products(RUN, SID)
        artifact_id = products["segmentation2d"]["artifact_id"]
        row = collection.catalog.get_artifact(artifact_id)
        assert row["artifact_type"] == "segmentation2d"
        assert row["producer"] == "tracking"  # the module that declares the file
        assert row["observation_time"] == "2026-08-13T17:31:58Z"
        object_path = collection.objects_dir / row["object_name"]
        assert object_path.exists()
        assert collection.catalog.parents_of(artifact_id) == [raw_artifact_id]

        reopened = xr.open_dataset(object_path)
        try:
            assert reopened.attrs["scan_id"] == SID
            assert reopened.attrs["scan_time"] == "2026-08-13T17:31:58Z"
            assert reopened.attrs["radar"] == "KILX"
        finally:
            reopened.close()

    def test_the_file_is_named_for_its_run_module_source_and_product(
        self, router, collection, raw_artifact_id
    ):
        router.persist([_module("tracking", NC_SPEC)], {"analysis_ds": _dataset()}, _meta())

        artifact_id = collection.catalog.scan_products(RUN, SID)["segmentation2d"]["artifact_id"]
        assert collection.catalog.get_artifact(artifact_id)["object_name"] == NC_NAME
        assert (collection.objects_dir / NC_NAME).is_file()

    def test_writing_a_scan_again_replaces_the_file_and_keeps_its_identity(
        self, router, collection, raw_artifact_id
    ):
        module = _module("tracking", NC_SPEC)
        router.persist([module], {"analysis_ds": _dataset()}, _meta())
        first = collection.catalog.scan_products(RUN, SID)["segmentation2d"]["artifact_id"]

        second_pass = xr.Dataset({"reflectivity": ("x", [7.0, 8.0, 9.0])})
        router.persist([module], {"analysis_ds": second_pass}, _meta())

        rows = collection.catalog.list_artifacts(artifact_type="segmentation2d")
        assert [row["artifact_id"] for row in rows] == [first]
        assert collection.catalog.scan_products(RUN, SID)["segmentation2d"]["artifact_id"] == first
        assert collection.catalog.parents_of(first) == [raw_artifact_id]
        reopened = xr.open_dataset(collection.objects_dir / NC_NAME)
        try:
            assert reopened["reflectivity"].values.tolist() == [7.0, 8.0, 9.0]
        finally:
            reopened.close()

    def test_two_runs_over_one_scan_each_keep_their_own_file(
        self, router, collection, raw_artifact_id
    ):
        collection.catalog.register_scan(
            ScanRecord(
                run_id="run-2",
                scan_id=SID,
                scan_time=SCAN_TIME,
                source_file_name="KILX20260813_173158_V06",
            )
        )
        module = _module("tracking", NC_SPEC)

        router.persist([module], {"analysis_ds": _dataset()}, _meta())
        router.persist([module], {"analysis_ds": _dataset()}, _meta(run_id="run-2"))

        names = sorted(
            row["object_name"]
            for row in collection.catalog.list_artifacts(artifact_type="segmentation2d")
        )
        assert names == [NC_NAME, NC_NAME.replace("run-1", "run-2")]

    def test_the_file_is_named_for_the_file_this_run_read(
        self, router, collection, raw_artifact_id
    ):
        """The same bytes stored later under another name must not rename this run's file."""
        from adapt.persistence.objects import ArtifactMeta, ObjectStore

        objects = ObjectStore(collection.objects_dir, collection.catalog)
        handle = objects.begin("raw/KILX20260813_173158_V06_renamed")
        handle.staging_path.write_bytes(b"level2")
        objects.commit(
            handle,
            ArtifactMeta(
                artifact_type="raw_volume",
                producer="acquisition",
                run_id="run-2",
                scan_id=SID,
                observation_time=SCAN_TIME,
                original_filename="KILX20260813_173158_V06_renamed",
            ),
        )

        router.persist([_module("tracking", NC_SPEC)], {"analysis_ds": _dataset()}, _meta())

        artifact_id = collection.catalog.scan_products(RUN, SID)["segmentation2d"]["artifact_id"]
        assert collection.catalog.get_artifact(artifact_id)["object_name"] == NC_NAME

    def test_a_scan_the_run_never_registered_cannot_be_written(self, router, collection):
        from adapt.persistence.objects import ArtifactMeta, ObjectStore

        objects = ObjectStore(collection.objects_dir, collection.catalog)
        handle = objects.begin("raw/KILX20260813_173158_V06")
        handle.staging_path.write_bytes(b"level2")
        objects.commit(
            handle,
            ArtifactMeta(
                artifact_type="raw_volume",
                producer="acquisition",
                run_id="run-0",
                scan_id=SID,
                observation_time=SCAN_TIME,
                original_filename="KILX20260813_173158_V06",
            ),
        )

        with pytest.raises(StoreError, match="not registered"):
            router.persist([_module("tracking", NC_SPEC)], {"analysis_ds": _dataset()}, _meta())

        assert collection.catalog.list_artifacts(artifact_type="segmentation2d") == []

    def test_without_raw_artifact_raises(self, router, collection):
        collection.catalog.register_scan(
            ScanRecord(run_id=RUN, scan_id=SID, scan_time=SCAN_TIME, source_file_name="f")
        )

        with pytest.raises(StoreError, match="raw_volume"):
            router.persist([_module("tracking", NC_SPEC)], {"analysis_ds": _dataset()}, _meta())

    def test_absent_key_skips(self, router, raw_artifact_id):
        router.persist([_module("tracking", NC_SPEC)], {}, _meta())


class TestProductTableWrite:
    def test_rows_written_and_table_linked(self, router, collection, raw_artifact_id):
        df = pd.DataFrame({"run_id": [RUN], "cell_label": [1], "area": [10.5]})

        router.persist([_module("analysis", TABLE_SPEC)], {"cell_stats": df}, _meta())

        rows = _products_rows(collection, "SELECT * FROM cell_stats")
        assert len(rows) == 1
        assert rows[0]["scan_id"] == SID
        link = collection.catalog.scan_products(RUN, SID)["cell_stats"]
        assert link["kind"] == "table"
        assert link["table_name"] == "cell_stats"

    def test_empty_frame_skips(self, router, collection, raw_artifact_id):
        router.persist([_module("analysis", TABLE_SPEC)], {"cell_stats": pd.DataFrame()}, _meta())

        assert "cell_stats" not in collection.catalog.scan_products(RUN, SID)


class TestProductView:
    """The ``cells_by_scan`` view tracking declares: its cells, and every table with their key."""

    def test_a_scan_with_cells_is_linked_to_the_view(self, router, collection, raw_artifact_id):
        modules = [_module("analysis", TABLE_SPEC), _module("tracking", *TRACKING_TABLES)]
        router.persist(modules, _tracking_result(), _meta())

        rows = _products_rows(collection, "SELECT * FROM cells_by_scan")
        assert len(rows) == 1
        assert rows[0]["scan_id"] == SID
        assert rows[0]["cell_uid"] == "u1"
        assert rows[0]["cell_area_sqkm"] == 10.0
        assert collection.catalog.scan_products(RUN, SID)["cells_by_scan"]["kind"] == "table"

    def test_a_scan_without_cells_is_not_linked(self, router, collection, raw_artifact_id):
        empty = {key: frame.iloc[0:0] for key, frame in _tracking_result().items()}
        modules = [_module("analysis", TABLE_SPEC), _module("tracking", *TRACKING_TABLES)]
        router.persist(modules, empty, _meta())

        assert "cells_by_scan" not in collection.catalog.scan_products(RUN, SID)

    def test_cells_without_statistics_are_still_in_the_view(
        self, router, collection, raw_artifact_id
    ):
        # No module wrote cell_stats: the view has the cells, with what tracking knows.
        router.persist([_module("tracking", *TRACKING_TABLES)], _tracking_result(), _meta())

        rows = _products_rows(collection, "SELECT * FROM cells_by_scan")
        assert [row["cell_uid"] for row in rows] == ["u1"]
        assert "cell_area_sqkm" not in rows[0]
        assert "cells_by_scan" in collection.catalog.scan_products(RUN, SID)

    def test_a_table_another_module_writes_later_joins_the_view(
        self, router, collection, raw_artifact_id
    ):
        router.persist([_module("tracking", *TRACKING_TABLES)], _tracking_result(), _meta())
        volume = ProductTableWrite(
            key="volume_rows", table="cell_volume_stats", primary_key=TABLE_SPEC.primary_key
        )

        # A writer that persists only that module, as a pass over a stored run does.
        StoreOutputRouter(collection, VIEWS).persist(
            [_module("cell_volume_stats", volume)],
            {"volume_rows": pd.DataFrame({"cell_label": [1], "cell_top_m": [9000.0]})},
            _meta(),
        )

        rows = _products_rows(collection, "SELECT * FROM cells_by_scan")
        assert [(row["cell_uid"], row["cell_top_m"]) for row in rows] == [("u1", 9000.0)]

    def test_cells_without_a_table_the_view_reads_are_refused(self, collection, raw_artifact_id):
        # The view's computed column reads a table no module ever wrote.
        view = ProductView(
            name="cells",
            rows_from="cell_stats",
            computed=(("n_events", "(SELECT COUNT(*) FROM cell_events)"),),
            reads=("cell_events",),
        )
        router = StoreOutputRouter(collection, (view,))

        with pytest.raises(StoreError, match="cells.*cell_events"):
            router.persist(
                [_module("analysis", TABLE_SPEC, view)],
                {"cell_stats": pd.DataFrame({"cell_label": [1], "cell_area_sqkm": [10.0]})},
                _meta(),
            )

    def test_a_view_the_router_was_not_given_is_refused(self, collection, raw_artifact_id):
        router = StoreOutputRouter(collection, ())

        with pytest.raises(StoreError, match="cells_by_scan"):
            router.persist([_module("tracking", *TRACKING_TABLES)], _tracking_result(), _meta())

    def test_a_router_rebuilds_a_view_left_by_other_code(self, router, collection, raw_artifact_id):
        modules = [_module("analysis", TABLE_SPEC), _module("tracking", *TRACKING_TABLES)]
        router.persist(modules, _tracking_result(), _meta())
        conn = sqlite3.connect(collection.products_path)
        conn.execute("DROP VIEW cells_by_scan")
        conn.execute("CREATE VIEW cells_by_scan AS SELECT cell_uid FROM cell_identity")
        conn.commit()
        conn.close()

        StoreOutputRouter(collection, VIEWS)

        rows = _products_rows(collection, "SELECT * FROM cells_by_scan")
        assert rows[0]["cell_area_sqkm"] == 10.0


class TestScanPass:
    def test_a_pass_with_no_failure_completes_the_scan(self, collection, raw_artifact_id):
        collection.catalog.record_scan_pass(
            RUN, SID, {"ingest": ("done", None), "tracking": ("skipped", "needs 1 prior scan(s)")}
        )

        assert collection.catalog.scan_is_complete(RUN, SID)
        modules = collection.catalog.scan_modules(RUN, SID)
        assert modules["ingest"]["status"] == "done"
        assert modules["tracking"]["reason"] == "needs 1 prior scan(s)"

    def test_a_failed_module_fails_the_scan_until_a_later_pass(self, collection, raw_artifact_id):
        collection.catalog.record_scan_pass(RUN, SID, {"ingest": ("failed", "OSError: bad file")})
        assert not collection.catalog.scan_is_complete(RUN, SID)
        assert collection.catalog.get_scan(RUN, SID)["status"] == "failed"

        collection.catalog.record_scan_pass(RUN, SID, {"ingest": ("done", None)})
        assert collection.catalog.scan_is_complete(RUN, SID)
        assert collection.catalog.scan_modules(RUN, SID)["ingest"]["reason"] is None

    def test_a_pass_over_an_unregistered_scan_is_refused(self, collection):
        with pytest.raises(StoreError, match="ghost"):
            collection.catalog.record_scan_pass(RUN, "ghost", {"ingest": ("done", None)})
