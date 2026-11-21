# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""decisions.db: what the decision log stores for each record.

Written through the store's output router only, with the real modules'
decision declarations, and read back with sqlite3. The map of the nine tables
below is written out here, not taken from the declarations.
"""

import json
import math
import sqlite3
import typing
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from types import SimpleNamespace, UnionType

import pytest

from adapt.contracts import (
    DecisionTableWrite,
    PersistenceMeta,
    SegmentationComponent,
    SegmentationDecisions,
    SegmentationFrame,
    SegmentationSeed,
    TrackingCell,
    TrackingDecisions,
    TrackingIdentity,
    TrackingLatent,
    TrackingLineage,
    TrackingPair,
    TrackingScan,
)
from adapt.modules.detection.node import DetectModule
from adapt.modules.tracking.node import TrackingModule
from adapt.persistence.errors import StoreError
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store, init_store
from adapt.utils.frames import record_integer_columns

pytestmark = pytest.mark.unit

RUN = "run-1"

# table -> (bundle key, bundle part, rows numbered by `seq`, primary key)
TABLES: dict[str, tuple[str, str, bool, tuple[str, ...]]] = {
    "tracking_scans": ("tracking_decisions", "scan", False, ("run_id", "scan_id")),
    "tracking_cells": (
        "tracking_decisions",
        "cells",
        False,
        ("run_id", "scan_id", "cell_label"),
    ),
    "tracking_pairs": (
        "tracking_decisions",
        "pairs",
        False,
        ("run_id", "scan_id", "prev_cell_uid", "curr_cell_label"),
    ),
    "tracking_lineage": ("tracking_decisions", "lineage", True, ("run_id", "scan_id", "seq")),
    "tracking_identity": (
        "tracking_decisions",
        "identity",
        False,
        ("run_id", "scan_id", "kind", "identity_uid", "candidate_side", "candidate_label"),
    ),
    "tracking_latent": ("tracking_decisions", "latent", False, ("run_id", "scan_id", "cell_uid")),
    "segmentation_frames": ("detection_decisions", "frame", False, ("run_id", "scan_id")),
    "segmentation_seeds": ("detection_decisions", "seeds", True, ("run_id", "scan_id", "seq")),
    "segmentation_components": (
        "detection_decisions",
        "components",
        False,
        ("run_id", "scan_id", "component_id"),
    ),
}

# Two scans of one run: in the first every optional field is None, in the
# second every optional field has a value.
SCANS = (
    ("scan-a", datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC), "2026-08-13T17:31:58Z", False),
    ("scan-b", datetime(2026, 8, 13, 17, 36, 30, tzinfo=UTC), "2026-08-13T17:36:30Z", True),
)


def _value(kind: type, name: str, position: int, row: int, scan: int):
    """A value of ``kind``, distinct per field, row and scan; flags take both values."""
    if kind is bool:
        return (position + row) % 2 == 0
    if kind is int:
        return 100 * scan + 10 * row + position
    if kind is float:
        return scan + row + position / 4
    return f"{name}-{scan}-{row}"


def _make(record_type: type, row: int, scan: int, filled: bool):
    hints = typing.get_type_hints(record_type)
    values = {}
    for position, f in enumerate(fields(record_type)):
        hint = hints[f.name]
        optional = isinstance(hint, UnionType)
        if optional and not filled:
            values[f.name] = None
            continue
        kind = next(a for a in typing.get_args(hint) if a is not type(None)) if optional else hint
        values[f.name] = _value(kind, f.name, position, row, scan)
    return record_type(**values)


def _bundles(scan: int, filled: bool) -> dict:
    def two(record_type):
        return tuple(_make(record_type, row, scan, filled) for row in range(2))

    cells = two(TrackingCell)
    if scan == 0:
        cells = (TrackingCell(**{**asdict(cells[0]), "score": -math.inf}), cells[1])
    return {
        "tracking_decisions": TrackingDecisions(
            scan=_make(TrackingScan, 0, scan, filled),
            cells=cells,
            pairs=two(TrackingPair),
            lineage=two(TrackingLineage),
            identity=two(TrackingIdentity),
            latent=two(TrackingLatent),
        ),
        "detection_decisions": SegmentationDecisions(
            frame=_make(SegmentationFrame, 0, scan, filled),
            seeds=two(SegmentationSeed),
            components=two(SegmentationComponent),
        ),
    }


def _meta(scan: int) -> PersistenceMeta:
    scan_id, scan_time, _, _ = SCANS[scan]
    return PersistenceMeta(
        scan_time=scan_time, scan_id=scan_id, run_id=RUN, source_file="f", collection_id="KILX"
    )


def _modules() -> list:
    return [
        SimpleNamespace(
            name=module.name,
            persistence=tuple(s for s in module.persistence if isinstance(s, DecisionTableWrite)),
        )
        for module in (DetectModule, TrackingModule)
    ]


@pytest.fixture
def store(tmp_path):
    st = Store.open(init_store(tmp_path / "store"))
    yield st
    st.close()


def _write(store, scan: int) -> None:
    _, _, _, filled = SCANS[scan]
    StoreOutputRouter(store.collection("KILX"), ()).persist(
        _modules(), _bundles(scan, filled), _meta(scan)
    )


@pytest.fixture
def written(store):
    for scan in range(len(SCANS)):
        _write(store, scan)
    return store.collection("KILX")


def _connect(path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _stored(path, table: str, scan_id: str) -> dict[tuple, dict[str, tuple]]:
    """Rows of one scan keyed by primary key: column -> (value, storage class)."""
    pk = TABLES[table][3]
    conn = _connect(path)
    try:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        select = ", ".join(f'"{c}", typeof("{c}")' for c in columns)
        rows = conn.execute(f"SELECT {select} FROM {table} WHERE scan_id = ?", (scan_id,))
        out = {}
        for row in rows:
            cells = {c: (row[2 * i], row[2 * i + 1]) for i, c in enumerate(columns)}
            out[tuple(cells[c][0] for c in pk)] = cells
        return out
    finally:
        conn.close()


def _expected(table: str, scan: int) -> dict[tuple, dict]:
    """Rows of one scan keyed by primary key: the record's fields plus the stamps."""
    key, part, sequenced, pk = TABLES[table]
    scan_id, _, scan_iso, filled = SCANS[scan]
    records = getattr(_bundles(scan, filled)[key], part)
    records = records if isinstance(records, tuple) else (records,)
    out = {}
    for seq, record in enumerate(records):
        row = {**asdict(record), "run_id": RUN, "scan_id": scan_id, "scan_time": scan_iso}
        if sequenced:
            row["seq"] = seq
        out[tuple(row[c] for c in pk)] = row
    return out


def _storage_class(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, (bool, int)):
        return "integer"
    if isinstance(value, float):
        return "real"
    return "text"


def test_each_row_holds_its_records_fields_and_the_scan_stamps(written):
    for table in TABLES:
        for scan in range(len(SCANS)):
            stored = _stored(written.decisions_path, table, SCANS[scan][0])
            expected = _expected(table, scan)
            assert stored.keys() == expected.keys(), table
            for key, row in expected.items():
                assert {c: stored[key][c][0] for c in row} == row, (table, key)


def test_each_value_is_stored_in_the_class_of_its_field(written):
    for table in TABLES:
        for scan in range(len(SCANS)):
            stored = _stored(written.decisions_path, table, SCANS[scan][0])
            for key, row in _expected(table, scan).items():
                classes = {c: stored[key][c][1] for c in row}
                assert classes == {c: _storage_class(v) for c, v in row.items()}, (table, key)


def test_a_field_empty_in_the_first_scan_takes_its_values_in_the_next(written):
    seen = 0
    for table in TABLES:
        first = _expected(table, 0)
        empty = {c for row in first.values() for c, v in row.items() if v is None}
        stored = _stored(written.decisions_path, table, SCANS[1][0])
        for key, row in _expected(table, 1).items():
            for column in empty:
                assert row[column] is not None
                assert stored[key][column] == (row[column], _storage_class(row[column]))
                seen += 1
    assert seen


def test_the_primary_keys_are_the_declared_ones(written):
    conn = _connect(written.decisions_path)
    try:
        for table, (_, _, _, pk) in TABLES.items():
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            assert tuple(r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]) == pk, table
    finally:
        conn.close()


def test_writing_a_scan_again_replaces_its_rows(store):
    _write(store, 0)
    _write(store, 0)
    path = store.collection("KILX").decisions_path
    for table in TABLES:
        assert len(_stored(path, table, SCANS[0][0])) == len(_expected(table, 0)), table


def test_decisions_are_no_scan_product(written):
    for path in (written.products_path, written.catalog.db_path):
        conn = _connect(path)
        try:
            assert conn.execute("SELECT count(*) FROM table_schemas").fetchone() == (0,)
        finally:
            conn.close()
    for scan_id, _, _, _ in SCANS:
        assert written.catalog.scan_products(RUN, scan_id) == {}


# ── A decision table is a module's table ─────────────────────────────────────


def _registry(path) -> dict[str, dict]:
    conn = _connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return {r["table_name"]: dict(r) for r in conn.execute("SELECT * FROM table_schemas")}
    finally:
        conn.close()


def _tables(path) -> set[str]:
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {name for (name,) in rows}


def test_a_table_is_made_with_its_first_row(store):
    collection = store.collection("KILX")
    router = StoreOutputRouter(collection, ())
    tracking = [m for m in _modules() if m.name == "tracking"]
    without_pairs = replace(_bundles(0, False)["tracking_decisions"], pairs=())

    router.persist(tracking, {"tracking_decisions": without_pairs}, _meta(0))
    assert "tracking_scans" in _tables(collection.decisions_path)
    assert "tracking_pairs" not in _tables(collection.decisions_path)

    router.persist(tracking, _bundles(1, True), _meta(1))
    assert len(_stored(collection.decisions_path, "tracking_pairs", SCANS[1][0])) == 2


def test_each_table_is_registered_to_its_module(written):
    registry = _registry(written.decisions_path)

    assert set(registry) == set(TABLES)
    for table, (key, _, _, pk) in TABLES.items():
        owner = "tracking" if key == "tracking_decisions" else "detection"
        assert registry[table]["owner_module"] == owner, table
        assert registry[table]["granularity"] == "scan", table
        assert tuple(json.loads(registry[table]["primary_key"])) == pk, table


def test_every_decision_table_has_scan_time_unix(written):
    expected = int(SCANS[0][1].timestamp())
    for table in TABLES:
        for row in _stored(written.decisions_path, table, SCANS[0][0]).values():
            assert row["scan_time_unix"] == (expected, "integer"), table


def test_a_module_that_did_not_run_writes_nothing(store):
    collection = store.collection("KILX")

    StoreOutputRouter(collection, ()).persist(_modules(), {}, _meta(0))

    assert _tables(collection.decisions_path) == {"table_schemas"}


def test_a_bundle_without_the_declared_part_is_refused(store):
    module = SimpleNamespace(
        name="tracking",
        persistence=(
            DecisionTableWrite(
                key="tracking_decisions",
                records="no_such_part",
                table="tracking_probe",
                primary_key=("run_id", "scan_id"),
            ),
        ),
    )

    with pytest.raises(StoreError) as excinfo:
        StoreOutputRouter(store.collection("KILX"), ()).persist(
            [module], _bundles(0, False), _meta(0)
        )

    message = str(excinfo.value)
    assert "tracking" in message and "tracking_decisions" in message
    assert "no_such_part" in message


def test_another_module_cannot_write_a_decision_table(written):
    (tracking,) = [m for m in _modules() if m.name == "tracking"]
    other = SimpleNamespace(name="other", persistence=tracking.persistence)

    with pytest.raises(StoreError, match="owned by module 'tracking'"):
        StoreOutputRouter(written, ()).persist([other], _bundles(0, False), _meta(0))


@dataclass(frozen=True)
class _ProbeRecord:
    value: float
    count: int
    note: str | None


@dataclass(frozen=True)
class _ProbeLog:
    rows: tuple[_ProbeRecord, ...]


def test_a_third_module_keeps_a_decision_log_with_its_own_records(store):
    collection = store.collection("KILX")
    probe = SimpleNamespace(
        name="probe",
        persistence=(
            DecisionTableWrite(
                key="probe_log",
                records="rows",
                table="probe_rows",
                primary_key=("run_id", "scan_id", "seq"),
                integer_columns=("seq", *record_integer_columns(_ProbeRecord)),
                sequenced=True,
            ),
        ),
    )
    log = _ProbeLog((_ProbeRecord(0.5, 3, None), _ProbeRecord(1.5, 4, "x")))

    StoreOutputRouter(collection, ()).persist([probe], {"probe_log": log}, _meta(0))

    conn = _connect(collection.decisions_path)
    try:
        rows = conn.execute(
            "SELECT seq, value, count, note, run_id, scan_id FROM probe_rows ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(0, 0.5, 3, None, RUN, "scan-a"), (1, 1.5, 4, "x", RUN, "scan-a")]
    assert _registry(collection.decisions_path)["probe_rows"]["owner_module"] == "probe"
