# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""ObjectStore: the caller names the object, a uuid identifies it, and nothing is
visible until the atomic commit. A name is taken once; only its owner may rewrite it."""

import hashlib
import logging
from datetime import UTC, datetime

import pytest

from adapt.persistence.objects import (
    ArtifactMeta,
    ObjectStore,
    product_object_name,
    raw_object_name,
)
from adapt.persistence.store import Store, StoreError, init_store

pytestmark = pytest.mark.unit

PAYLOAD = b"level-two radar volume bytes"
SCAN_TIME = datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC)


RAW = "raw/KILX20260813_173158_V06"
GRID = "run-1/ingest/KILX20260813_173158_V06.gridded3d.nc"


def _meta(scan_id: str | None = "abc123", run_id: str = "run-1") -> ArtifactMeta:
    return ArtifactMeta(
        artifact_type="raw_volume",
        producer="acquisition",
        run_id=run_id,
        scan_id=scan_id,
        observation_time=SCAN_TIME,
        original_filename="KILX20260813_173158_V06",
        source_uri="s3://noaa-nexrad-level2/KILX20260813_173158_V06",
    )


@pytest.fixture
def collection(tmp_path):
    root = init_store(tmp_path / "store")
    store = Store.open(root)
    coll = store.collection("KILX")
    yield coll
    store.close()


@pytest.fixture
def objects(collection):
    return ObjectStore(collection.objects_dir, collection.catalog)


def _visible_objects(collection) -> set[str]:
    """Every published object, as its name relative to ``objects/``."""
    root = collection.objects_dir
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".staging" not in p.relative_to(root).parts
    }


def _stage(objects, name: str, payload: bytes = PAYLOAD):
    handle = objects.begin(name)
    handle.staging_path.write_bytes(payload)
    return handle


class TestNames:
    def test_a_raw_file_keeps_its_original_name(self):
        assert raw_object_name("KILX20260813_173158_V06") == RAW

    def test_a_module_file_says_run_module_source_and_product(self):
        name = product_object_name("run-1", "ingest", "KILX20260813_173158_V06", "gridded3d")

        assert name == GRID

    def test_a_source_suffix_is_not_repeated_in_the_module_file(self):
        name = product_object_name("run-1", "ingest", "KILX20260813_173158_V06.gz", "gridded3d")

        assert name == GRID


class TestBegin:
    def test_allocates_uuid_before_any_bytes_exist(self, objects, collection):
        handle = objects.begin(RAW)

        assert len(handle.artifact_id) == 32
        int(handle.artifact_id, 16)  # uuid4 hex
        assert handle.staging_path.parent == collection.objects_dir / ".staging"
        assert _visible_objects(collection) == set()

    def test_staged_bytes_are_invisible_and_uncataloged(self, objects, collection):
        handle = _stage(objects, RAW)

        assert _visible_objects(collection) == set()
        assert collection.catalog.get_artifact(handle.artifact_id) is None

    @pytest.mark.parametrize(
        "name",
        [
            "",
            ".",
            "/abs/KILX",
            "../outside",
            "raw/../../outside",
            ".staging/x",
            ".STAGING/x",
            "raw/",
            "raw/.",
            "raw/./KILX",
            "raw//KILX",
            "./raw/KILX",
        ],
    )
    def test_a_name_outside_the_objects_folder_is_refused(self, objects, name):
        with pytest.raises(StoreError, match="object name"):
            objects.begin(name)


class TestCommit:
    def test_commit_publishes_object_and_full_catalog_row(self, objects, collection):
        handle = _stage(objects, RAW)

        record = objects.commit(handle, _meta())

        assert _visible_objects(collection) == {RAW}
        assert (collection.objects_dir / RAW).read_bytes() == PAYLOAD

        row = collection.catalog.get_artifact(handle.artifact_id)
        assert row is not None
        assert row["artifact_type"] == "raw_volume"
        assert row["producer"] == "acquisition"
        assert row["run_id"] == "run-1"
        assert row["scan_id"] == "abc123"
        assert row["observation_time"] == "2026-08-13T17:31:58Z"
        assert row["original_filename"] == "KILX20260813_173158_V06"
        assert row["source_uri"].startswith("s3://")
        assert row["object_name"] == RAW
        assert row["checksum_sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
        assert row["size_bytes"] == len(PAYLOAD)
        assert record.artifact_id == handle.artifact_id

    def test_commit_creates_the_folders_of_a_nested_name(self, objects, collection):
        objects.commit(_stage(objects, GRID), _meta())

        assert _visible_objects(collection) == {GRID}

    def test_commit_clears_staging(self, objects, collection):
        handle = _stage(objects, RAW)

        objects.commit(handle, _meta())

        assert not handle.staging_path.exists()

    def test_commit_twice_raises(self, objects):
        handle = _stage(objects, RAW)
        objects.commit(handle, _meta())

        with pytest.raises(StoreError, match="staged"):
            objects.commit(handle, _meta())

    def test_commit_without_staged_bytes_raises(self, objects):
        handle = objects.begin(RAW)

        with pytest.raises(StoreError, match="staged"):
            objects.commit(handle, _meta())

    def test_a_taken_name_is_refused_and_the_object_untouched(self, objects, collection):
        objects.commit(_stage(objects, RAW), _meta())

        with pytest.raises(StoreError, match="already exists"):
            objects.commit(_stage(objects, RAW, b"other bytes"), _meta())

        assert (collection.objects_dir / RAW).read_bytes() == PAYLOAD

    def test_a_name_differing_only_in_case_is_the_same_name(self, objects, collection):
        """Stores live on case-insensitive filesystems too (macOS, Windows)."""
        objects.commit(_stage(objects, RAW), _meta())

        with pytest.raises(StoreError, match="already exists"):
            objects.commit(_stage(objects, RAW.lower(), b"other bytes"), _meta(scan_id="zzz"))

        assert (collection.objects_dir / RAW).read_bytes() == PAYLOAD
        assert len(collection.catalog.list_artifacts()) == 1

    def test_an_uncataloged_leftover_is_overwritten(self, objects, collection, caplog):
        """A commit interrupted after publishing leaves a file nothing references."""
        leftover = collection.objects_dir / RAW
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(b"half a commit")

        with caplog.at_level(logging.WARNING, logger="adapt.persistence.objects"):
            record = objects.commit(_stage(objects, RAW), _meta())

        assert leftover.read_bytes() == PAYLOAD
        assert collection.catalog.get_artifact(record.artifact_id)["object_name"] == RAW
        assert [r.getMessage() for r in caplog.records] == [
            f"Overwriting uncataloged object '{RAW}'"
        ]  # never silent


def _grid_meta(**changes) -> ArtifactMeta:
    """A module's file for one scan of run-1, as the router describes it."""
    owner = {
        "artifact_type": "gridded3d",
        "producer": "ingest",
        "run_id": "run-1",
        "scan_id": "abc123",
    }
    return ArtifactMeta(observation_time=SCAN_TIME, **{**owner, **changes})


class TestReplace:
    def test_the_owner_rewrites_its_object_and_keeps_its_identity(self, objects, collection):
        first = objects.commit(_stage(objects, GRID), _grid_meta())
        written = collection.catalog.get_artifact(first.artifact_id)["created_at"]

        second = objects.commit(_stage(objects, GRID, b"second pass"), _grid_meta(), replace=True)

        assert second.artifact_id == first.artifact_id
        assert (collection.objects_dir / GRID).read_bytes() == b"second pass"
        rows = collection.catalog.list_artifacts()
        assert len(rows) == 1
        assert rows[0]["checksum_sha256"] == hashlib.sha256(b"second pass").hexdigest()
        assert rows[0]["size_bytes"] == len(b"second pass")
        assert rows[0]["created_at"] > written  # the row says when the bytes were last written

    def test_replace_of_a_new_name_just_publishes(self, objects, collection):
        record = objects.commit(_stage(objects, GRID), _grid_meta(), replace=True)

        assert collection.catalog.get_artifact(record.artifact_id)["object_name"] == GRID

    @pytest.mark.parametrize(
        "other",
        [
            {"run_id": "run-2"},
            {"producer": "tracking"},
            {"artifact_type": "segmentation2d"},
            {"scan_id": "another-scan"},
        ],
    )
    def test_only_the_owner_may_rewrite_the_object(self, objects, collection, other):
        """Owner = the same run, producer, artifact type and scan."""
        objects.commit(_stage(objects, GRID), _grid_meta())

        with pytest.raises(StoreError, match="only its owner"):
            objects.commit(_stage(objects, GRID, b"intruder"), _grid_meta(**other), replace=True)

        assert (collection.objects_dir / GRID).read_bytes() == PAYLOAD

    def test_a_raw_object_is_never_replaced_even_by_its_owner(self, objects, collection):
        objects.commit(_stage(objects, RAW), _meta())

        with pytest.raises(StoreError, match="raw"):
            objects.commit(_stage(objects, RAW, b"tampered"), _meta(), replace=True)

        assert (collection.objects_dir / RAW).read_bytes() == PAYLOAD

    def test_a_rewrite_keeps_one_lineage_edge(self, objects, collection):
        parent = objects.commit(_stage(objects, RAW), _meta())
        child = objects.commit(_stage(objects, GRID), _grid_meta(), parents=(parent.artifact_id,))

        objects.commit(
            _stage(objects, GRID, b"second pass"),
            _grid_meta(),
            parents=(parent.artifact_id,),
            replace=True,
        )

        assert collection.catalog.parents_of(child.artifact_id) == [parent.artifact_id]

    def test_a_rewrite_records_a_parent_it_did_not_have(self, objects, collection):
        first_parent = objects.commit(_stage(objects, RAW), _meta())
        second_parent = objects.commit(_stage(objects, RAW + "_b"), _meta(scan_id="def456"))
        child = objects.commit(
            _stage(objects, GRID), _grid_meta(), parents=(first_parent.artifact_id,)
        )

        objects.commit(
            _stage(objects, GRID, b"second pass"),
            _grid_meta(),
            parents=(first_parent.artifact_id, second_parent.artifact_id),
            replace=True,
        )

        assert collection.catalog.parents_of(child.artifact_id) == sorted(
            [first_parent.artifact_id, second_parent.artifact_id]
        )

    def test_updating_an_uncataloged_artifact_raises(self, collection):
        with pytest.raises(StoreError, match="ghost"):
            collection.catalog.update_artifact("ghost", "0" * 64, 1)


class TestAbort:
    def test_abort_leaves_no_trace(self, objects, collection):
        handle = _stage(objects, RAW)

        objects.abort(handle)

        assert not handle.staging_path.exists()
        assert _visible_objects(collection) == set()
        assert collection.catalog.get_artifact(handle.artifact_id) is None

    def test_abort_before_write_is_clean(self, objects, collection):
        handle = objects.begin(RAW)

        objects.abort(handle)

        assert _visible_objects(collection) == set()
