# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""products.db frozen first-frame schemas: freeze, dual snapshots, enforcement."""

import json
import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

from adapt.contracts.persistence import PersistenceMeta, ProductTableWrite, ProductView
from adapt.persistence.errors import StoreError
from adapt.persistence.products import SchemaLedger, TableDeclaration, TableWriter
from adapt.persistence.sqlite_store import SqliteStore
from adapt.persistence.store import Store, init_store

pytestmark = pytest.mark.unit

SCAN_TIME = datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC)

SPEC = ProductTableWrite(
    key="stats",
    table="cell_stats",
    primary_key=("run_id", "scan_id", "cell_label"),
    index_columns=("cell_label",),
)


def _scan_meta(scan_id: str = "abc123") -> PersistenceMeta:
    return PersistenceMeta(
        scan_time=SCAN_TIME,
        scan_id=scan_id,
        run_id="run-1",
        source_file="KILX20260813_173158_V06",
        collection_id="KILX",
    )


def _frame(**overrides) -> pd.DataFrame:
    data = {"run_id": ["run-1", "run-1"], "cell_label": [1, 2], "area": [10.5, 20.5]}
    data.update(overrides)
    return pd.DataFrame(data)


@pytest.fixture
def collection(tmp_path):
    root = init_store(tmp_path / "store")
    store = Store.open(root)
    yield store.collection("KILX")
    store.close()


@pytest.fixture
def ledger(collection):
    return SchemaLedger(collection.products_path, collection.catalog, ())


@pytest.fixture
def writer(ledger):
    return TableWriter(ledger, SPEC, owner_module="analysis")


def _rows(collection, sql):
    conn = sqlite3.connect(collection.products_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _snapshot(db_path, table_name):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM table_schemas WHERE table_name = ?", (table_name,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


class TestFirstFrameFreeze:
    def test_empty_frame_does_not_freeze(self, writer, collection):
        writer.write(pd.DataFrame(), _scan_meta())

        assert _snapshot(collection.products_path, "cell_stats") is None
        assert _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cell_stats'") == []

    def test_first_frame_creates_table_with_declared_key_and_payload(self, writer, collection):
        writer.write(_frame(), _scan_meta())

        rows = _rows(collection, "SELECT * FROM cell_stats ORDER BY cell_label")
        assert len(rows) == 2
        assert rows[0]["scan_id"] == "abc123"
        assert rows[0]["scan_time"] == "2026-08-13T17:31:58Z"
        assert rows[0]["area"] == pytest.approx(10.5)

        conn = sqlite3.connect(collection.products_path)
        try:
            info = conn.execute("PRAGMA table_info(cell_stats)").fetchall()
            index_cols = {r[1] for r in conn.execute("PRAGMA index_list(cell_stats)")}
        finally:
            conn.close()
        pk_cols = {r[1] for r in info if r[5] > 0}
        assert pk_cols == {"run_id", "scan_id", "cell_label"}
        assert any("cell_label" in name for name in index_cols)

    def test_identical_snapshots_in_products_and_catalog(self, writer, collection):
        writer.write(_frame(), _scan_meta())

        products_snap = _snapshot(collection.products_path, "cell_stats")
        catalog_snap = _snapshot(collection.catalog.db_path, "cell_stats")

        assert products_snap is not None
        assert products_snap == catalog_snap
        assert products_snap["owner_module"] == "analysis"
        assert products_snap["granularity"] == "scan"
        assert json.loads(products_snap["primary_key"]) == ["run_id", "scan_id", "cell_label"]
        frozen_names = {c["name"] for c in json.loads(products_snap["columns_json"])}
        assert {"run_id", "scan_id", "scan_time", "scan_time_unix", "cell_label", "area"} == (
            frozen_names
        )

    def test_a_ledger_without_a_catalog_registers_the_table_in_its_own_database_only(
        self, tmp_path
    ):
        store = Store.open(init_store(tmp_path / "store"))
        try:
            coll = store.collection("KILX")
            other = coll.path / "other.db"
            SqliteStore(other, "collection_products.sql").close()

            TableWriter(SchemaLedger(other, None, ()), SPEC, owner_module="analysis").write(
                _frame(), _scan_meta()
            )

            assert _snapshot(other, "cell_stats")["owner_module"] == "analysis"
            assert _snapshot(coll.catalog.db_path, "cell_stats") is None
            assert _snapshot(coll.products_path, "cell_stats") is None
            store.validate_collection("KILX")
        finally:
            store.close()

    def test_a_table_whose_first_write_fails_is_not_left_half_made(self, ledger, collection):
        spec = ProductTableWrite(
            key="stats",
            table="cell_stats",
            primary_key=("run_id", "scan_id", "cell_label"),
            index_columns=("no_such_column",),
        )

        with pytest.raises(StoreError, match="cell_stats.*no_such_column"):
            TableWriter(ledger, spec, owner_module="analysis").write(_frame(), _scan_meta())

        assert _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cell_stats'") == []
        assert _snapshot(collection.products_path, "cell_stats") is None
        assert _snapshot(collection.catalog.db_path, "cell_stats") is None

    def test_reserved_table_names_rejected(self, ledger):
        for reserved in ("table_schemas", "annotations"):
            spec = ProductTableWrite(key="x", table=reserved, primary_key=("run_id",))
            with pytest.raises(StoreError, match=reserved):
                TableWriter(ledger, spec, owner_module="rogue")


class TestEnforcement:
    def test_omitted_optional_column_becomes_null(self, writer, collection):
        writer.write(_frame(), _scan_meta())

        writer.write(_frame(area=None).drop(columns=["area"]), _scan_meta("def456"))

        rows = _rows(collection, "SELECT * FROM cell_stats WHERE scan_id = 'def456'")
        assert len(rows) == 2
        assert all(r["area"] is None for r in rows)

    def test_new_column_rejected(self, writer):
        writer.write(_frame(), _scan_meta())

        with pytest.raises(StoreError, match="novel"):
            writer.write(_frame(novel=[1, 2]), _scan_meta("def456"))

    def test_incompatible_dtype_rejected(self, writer):
        writer.write(_frame(), _scan_meta())

        with pytest.raises(StoreError, match="area"):
            writer.write(_frame(area=["high", "low"]), _scan_meta("def456"))

    def test_int_into_real_column_allowed(self, writer, collection):
        writer.write(_frame(), _scan_meta())

        writer.write(_frame(area=[1, 2]), _scan_meta("def456"))

        rows = _rows(collection, "SELECT area FROM cell_stats WHERE scan_id = 'def456'")
        assert sorted(r["area"] for r in rows) == [1, 2]

    def test_nan_promoted_float_into_int_column_allowed(self, writer, collection):
        # A cell with no match promotes an int column to float64 (NaN). The
        # values are still integers; freezing must not make later scans fail.
        writer.write(_frame(pixel_count=[40, 50]), _scan_meta())

        writer.write(_frame(pixel_count=[60.0, float("nan")]), _scan_meta("def456"))

        rows = _rows(collection, "SELECT pixel_count FROM cell_stats WHERE scan_id = 'def456'")
        values = sorted(r["pixel_count"] for r in rows if r["pixel_count"] is not None)
        assert values == [60]
        assert sum(r["pixel_count"] is None for r in rows) == 1

    def test_duplicate_primary_key_within_frame_rejected(self, writer):
        with pytest.raises(StoreError, match="duplicate"):
            writer.write(_frame(cell_label=[7, 7]), _scan_meta())

    def test_upsert_replaces_on_declared_key(self, writer, collection):
        writer.write(_frame(), _scan_meta())

        writer.write(_frame(area=[99.0, 98.0]), _scan_meta())

        rows = _rows(collection, "SELECT * FROM cell_stats ORDER BY cell_label")
        assert len(rows) == 2
        assert rows[0]["area"] == pytest.approx(99.0)

    def test_write_to_another_modules_table_rejected(self, ledger, writer):
        writer.write(_frame(), _scan_meta())
        intruder = TableWriter(ledger, SPEC, owner_module="tracking")

        with pytest.raises(StoreError, match="analysis"):
            intruder.write(_frame(), _scan_meta("def456"))


class TestIdentityStamping:
    def test_run_id_stamped_when_frame_omits_it(self, writer, collection):
        frame = pd.DataFrame({"cell_label": [1], "area": [1.5]})

        writer.write(frame, _scan_meta())

        rows = _rows(collection, "SELECT run_id FROM cell_stats")
        assert rows == [{"run_id": "run-1"}]

    def test_mismatched_run_id_column_raises(self, writer):
        with pytest.raises(StoreError, match="run_id"):
            writer.write(_frame(run_id=["other-run", "other-run"]), _scan_meta())

    def test_scan_granular_frame_may_not_carry_identity_columns(self, writer):
        with pytest.raises(StoreError, match="scan_id"):
            writer.write(_frame(scan_id=["x", "y"]), _scan_meta())

    def test_time_granular_table_requires_valid_time(self, ledger, collection):
        spec = ProductTableWrite(
            key="x", table="xlma_stat_minutes", primary_key=("run_id", "valid_time", "cell_uid")
        )
        writer = TableWriter(ledger, spec, owner_module="xlma_stat")

        with pytest.raises(StoreError, match="valid_time"):
            writer.write(pd.DataFrame({"run_id": ["run-1"], "cell_uid": ["c1"]}), _scan_meta())

        writer.write(
            pd.DataFrame(
                {"run_id": ["run-1"], "cell_uid": ["c1"], "valid_time": ["2026-08-13T17:32:00Z"]}
            ),
            _scan_meta(),
        )
        assert _snapshot(collection.products_path, "xlma_stat_minutes")["granularity"] == "time"


def _frozen_types(collection, table):
    snap = _snapshot(collection.products_path, table)
    return {c["name"]: c["type"] for c in json.loads(snap["columns_json"])}


def _freeze_with_first_frame(tmp_path, name, frame):
    """Freeze cell_stats in a fresh store from *frame* and return its frozen types."""
    root = init_store(tmp_path / name)
    store = Store.open(root)
    try:
        collection = store.collection("KILX")
        ledger = SchemaLedger(collection.products_path, collection.catalog, ())
        TableWriter(ledger, SPEC, owner_module="analysis").write(frame, _scan_meta())
        return _frozen_types(collection, "cell_stats")
    finally:
        store.close()


class TestDeterministicFreeze:
    """The frozen schema is a function of the column NAMES, never of the
    values in whichever scan happened to arrive first."""

    def test_integer_measure_freezes_as_real(self, writer, collection):
        writer.write(_frame(pixel_count=[40, 50]), _scan_meta())

        assert _frozen_types(collection, "cell_stats")["pixel_count"] == "REAL"

    def test_frozen_schema_is_independent_of_first_frame_values(self, tmp_path):
        # Same column set; one first frame is all-int, the other NaN-promoted
        # float64. Both stores must freeze the identical schema.
        int_first = _freeze_with_first_frame(tmp_path, "a", _frame(pixel_count=[40, 50]))
        nan_first = _freeze_with_first_frame(
            tmp_path, "b", _frame(pixel_count=[60.0, float("nan")])
        )

        assert int_first == nan_first

    def test_core_identity_columns_freeze_to_contracted_types(self, writer, collection):
        writer.write(_frame(is_edge=[True, False], note=["a", "b"]), _scan_meta())

        types = _frozen_types(collection, "cell_stats")
        assert types["run_id"] == "TEXT"
        assert types["scan_id"] == "TEXT"
        assert types["scan_time"] == "TEXT"
        assert types["scan_time_unix"] == "INTEGER"
        assert types["cell_label"] == "INTEGER"  # core: typed by name, not by dtype
        assert types["is_edge"] == "INTEGER"  # bools never NaN-promote
        assert types["note"] == "TEXT"

    def test_legacy_integer_freeze_still_accepts_nan_promoted_floats(self, ledger, writer):
        # Stores frozen before the deterministic rule carry INTEGER measure
        # columns; frames NaN-promoted to float64 must keep writing into them.
        ledger.freeze(
            TableDeclaration(
                table="cell_stats",
                owner_module="analysis",
                granularity="scan",
                primary_key=("run_id", "scan_id", "cell_label"),
                index_columns=(),
                columns=(
                    ("run_id", "TEXT"),
                    ("scan_id", "TEXT"),
                    ("scan_time", "TEXT"),
                    ("scan_time_unix", "INTEGER"),
                    ("cell_label", "INTEGER"),
                    ("area", "REAL"),
                    ("pixel_count", "INTEGER"),
                ),
            )
        )

        writer.write(_frame(pixel_count=[60.0, float("nan")]), _scan_meta())


# -- the view over the tables that share a key ---------------------------------

CELL_KEY = ("run_id", "scan_id", "cell_label")
CELLS = ProductView(name="cells", rows_from="cell_identity")
FLAGGED = ProductView(
    name="cells",
    rows_from="cell_identity",
    computed=(
        (
            "has_event",
            "EXISTS (SELECT 1 FROM cell_events e WHERE e.run_id = cell_identity.run_id"
            " AND e.scan_id = cell_identity.scan_id AND e.cell_label = cell_identity.cell_label)",
        ),
    ),
    reads=("cell_events",),
)


def _ledger(collection, *views: ProductView) -> SchemaLedger:
    return SchemaLedger(collection.products_path, collection.catalog, views)


def _write(ledger, table, columns: dict, *, key=CELL_KEY, scan_id="abc123") -> None:
    """Write rows of ``columns`` to ``table`` (owned by a module named for it)."""
    spec = ProductTableWrite(key=table, table=table, primary_key=key)
    TableWriter(ledger, spec, owner_module=table).write(pd.DataFrame(columns), _scan_meta(scan_id))


def _identity(ledger, labels=(1, 2)) -> None:
    _write(ledger, "cell_identity", {"cell_label": list(labels), "cell_uid": ["A", "B"]})


def _view(collection, name="cells") -> pd.DataFrame:
    conn = sqlite3.connect(collection.products_path)
    try:
        return pd.read_sql_query(f"SELECT * FROM {name} ORDER BY cell_label", conn)
    finally:
        conn.close()


class TestViewOverTablesWithOneKey:
    def test_a_view_has_a_row_for_every_row_of_its_table(self, collection):
        _identity(_ledger(collection, CELLS))

        cells = _view(collection)

        assert list(cells["cell_label"]) == [1, 2]
        assert list(cells["cell_uid"]) == ["A", "B"]

    def test_a_table_with_the_same_key_adds_its_columns_to_the_view(self, collection):
        ledger = _ledger(collection, CELLS)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})
        _identity(ledger)

        cells = _view(collection)

        assert list(cells["cell_uid"]) == ["A", "B"]
        assert list(cells["area"]) == [10.5, 20.5]

    def test_a_table_first_written_after_the_view_adds_its_columns(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        assert "volume" not in _view(collection).columns

        _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "volume": [3.0, 4.0]})

        assert list(_view(collection)["volume"]) == [3.0, 4.0]

    def test_a_table_with_another_key_is_left_out_of_the_view(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)

        _write(
            ledger, "cell_tracks", {"cell_uid": ["A"], "n_scans": [4]}, key=("run_id", "cell_uid")
        )

        assert "n_scans" not in _view(collection).columns

    def test_a_row_missing_from_a_joined_table_keeps_its_place_with_nulls(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        _write(ledger, "cell_stats", {"cell_label": [2], "area": [20.5]})
        _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "volume": [3.0, 4.0]})

        cells = _view(collection)

        assert pd.isna(cells["area"][0]) and cells["area"][1] == 20.5
        assert list(cells["volume"]) == [3.0, 4.0]

    def test_the_scan_columns_appear_once(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})
        _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "volume": [3.0, 4.0]})

        columns = list(_view(collection).columns)

        assert len(columns) == len(set(columns))
        for name in ("run_id", "scan_id", "cell_label", "scan_time", "scan_time_unix"):
            assert columns.count(name) == 1

    def test_columns_follow_the_tables_in_name_order(self, collection):
        ledger = _ledger(collection, CELLS)
        _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "volume": [3.0, 4.0]})
        _identity(ledger)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})

        columns = list(_view(collection).columns)

        assert columns == [
            "cell_label",
            "cell_uid",
            "run_id",
            "scan_id",
            "scan_time",
            "scan_time_unix",
            "area",
            "volume",
        ]

    def test_a_table_repeating_a_column_of_the_view_is_refused_and_not_created(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})

        with pytest.raises(StoreError, match="cells.*area.*cell_stats.*cell_volume_stats"):
            _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "area": [1.0, 2.0]})

        assert (
            _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cell_volume_stats'")
            == []
        )
        assert _snapshot(collection.products_path, "cell_volume_stats") is None
        assert list(_view(collection)["area"]) == [10.5, 20.5]

    def test_a_table_with_the_name_of_a_computed_column_is_refused(self, collection):
        ledger = _ledger(collection, FLAGGED)
        _write(ledger, "cell_events", {"cell_label": [1], "kind": ["SPLIT"]})
        _identity(ledger)

        with pytest.raises(StoreError, match="cells.*has_event.*cell_stats"):
            _write(ledger, "cell_stats", {"cell_label": [1, 2], "has_event": [0, 1]})

    def test_a_computed_column_is_worked_out_for_each_row(self, collection):
        ledger = _ledger(collection, FLAGGED)
        _identity(ledger)
        _write(ledger, "cell_events", {"cell_label": [1], "kind": ["SPLIT"]})

        cells = _view(collection)

        assert list(cells["has_event"]) == [1, 0]
        assert list(cells.columns)[-1] == "has_event"

    def test_the_view_waits_for_the_tables_its_computed_columns_read(self, collection):
        ledger = _ledger(collection, FLAGGED)

        _identity(ledger)

        assert _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cells'") == []

    def test_a_computed_column_that_cannot_be_read_is_refused(self, collection):
        broken = ProductView(
            name="cells", rows_from="cell_identity", computed=(("bad", "no_such_column + 1"),)
        )

        with pytest.raises(StoreError, match="cells.*no_such_column"):
            _identity(_ledger(collection, broken))

        assert (
            _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cell_identity'") == []
        )

    def test_a_view_cannot_take_the_name_of_a_table(self, collection):
        # The table is older than the declaration of the view.
        _write(_ledger(collection), "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})
        taken = ProductView(name="cell_stats", rows_from="cell_identity")

        with pytest.raises(StoreError, match="cell_stats"):
            _identity(_ledger(collection, taken))

    def test_a_view_of_rows_that_are_not_per_scan_is_refused(self, collection):
        per_run = ProductView(name="tracks", rows_from="cell_tracks")

        with pytest.raises(StoreError, match="tracks.*cell_tracks"):
            _write(
                _ledger(collection, per_run),
                "cell_tracks",
                {"cell_uid": ["A"], "n_scans": [4]},
                key=("run_id", "cell_uid"),
            )

    def test_a_reader_with_an_open_connection_sees_the_added_columns(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        reader = sqlite3.connect(collection.products_path)
        try:
            before = [d[0] for d in reader.execute("SELECT * FROM cells").description]

            _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})

            after = [d[0] for d in reader.execute("SELECT * FROM cells").description]
        finally:
            reader.close()
        assert "area" not in before
        assert "area" in after

    def test_a_view_left_by_other_code_is_replaced_when_the_views_are_built(self, collection):
        _identity(_ledger(collection))
        conn = sqlite3.connect(collection.products_path)
        conn.execute("CREATE VIEW cells AS SELECT cell_uid FROM cell_identity")
        conn.commit()
        conn.close()

        _ledger(collection, CELLS).build_views()

        assert "cell_label" in _view(collection).columns

    def test_building_the_views_again_changes_nothing_in_the_file(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        before = _rows(collection, "PRAGMA schema_version")

        ledger.build_views()

        assert _rows(collection, "PRAGMA schema_version") == before


class TestViewNamesAreUnambiguous:
    """SQLite compares names without regard to case and renames a repeated column
    silently (``name:1``); every such repeat is refused instead."""

    def test_columns_that_differ_only_in_case_are_refused(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})

        with pytest.raises(StoreError, match="cells.*Area.*cell_stats.*cell_volume_stats"):
            _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "Area": [1.0, 2.0]})

    def test_two_computed_columns_of_one_name_are_refused(self, collection):
        twice = ProductView(
            name="cells", rows_from="cell_identity", computed=(("flag", "1"), ("flag", "2"))
        )

        with pytest.raises(StoreError, match="cells.*flag"):
            _identity(_ledger(collection, twice))

    def test_a_table_cannot_take_the_name_of_a_view(self, collection):
        # Written before the view's own table: the view does not exist yet.
        with pytest.raises(StoreError, match="cells.*view"):
            _write(_ledger(collection, CELLS), "cells", {"cell_label": [1, 2], "area": [1.0, 2.0]})

        assert _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cells'") == []

    def test_two_views_of_one_name_are_refused(self, collection):
        other = ProductView(name="cells", rows_from="cell_stats")

        with pytest.raises(StoreError, match="cells"):
            _ledger(collection, CELLS, other)

    def test_one_view_declared_twice_is_one_view(self, collection):
        _identity(_ledger(collection, CELLS, CELLS))

        assert list(_view(collection)["cell_uid"]) == ["A", "B"]

    def test_a_table_with_the_key_in_another_order_is_joined(self, collection):
        ledger = _ledger(collection, CELLS)
        _identity(ledger)

        _write(
            ledger,
            "cell_volume_stats",
            {"cell_label": [1, 2], "volume": [3.0, 4.0]},
            key=("run_id", "cell_label", "scan_id"),
        )

        assert list(_view(collection)["volume"]) == [3.0, 4.0]

    def test_a_clash_among_tables_older_than_the_view_is_told_when_the_view_is_first_built(
        self, collection
    ):
        """Before the view's own table exists nothing joins the others, so a repeat
        among them is found, and refused, with the write that would build the view."""
        ledger = _ledger(collection, CELLS)
        _write(ledger, "cell_stats", {"cell_label": [1, 2], "area": [10.5, 20.5]})
        _write(ledger, "cell_volume_stats", {"cell_label": [1, 2], "area": [1.0, 2.0]})

        with pytest.raises(StoreError, match="cells.*area.*cell_stats.*cell_volume_stats"):
            _identity(ledger)

        assert (
            _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'cell_identity'") == []
        )

    def test_a_table_named_like_a_view_in_other_case_is_refused(self, collection):
        with pytest.raises(StoreError, match="Cells.*view"):
            _write(_ledger(collection, CELLS), "Cells", {"cell_label": [1, 2], "area": [1.0, 2.0]})

        assert _rows(collection, "SELECT name FROM sqlite_master WHERE name = 'Cells'") == []

    def test_two_views_whose_names_differ_only_in_case_are_refused(self, collection):
        other = ProductView(name="CELLS", rows_from="cell_stats")

        with pytest.raises(StoreError, match="CELLS"):
            _ledger(collection, CELLS, other)
