# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module-owned product tables with frozen first-frame schemas.

``SchemaLedger`` freezes a table's schema on its first non-empty frame —
declared primary key + payload columns — and registers it in its own
database; a products.db table's snapshot is copied into catalog.db too, so the
two agree. Frozen types are deterministic: core identity
columns are contracted by name (``_CORE_COLUMN_TYPES``), the columns a spec
declares as ``integer_columns`` freeze as INTEGER, every other numeric column
freezes as REAL, so the schema depends on the column set — never on the values
in whichever scan arrived first. ``TableWriter`` stamps scan identity from
``PersistenceMeta``, enforces the frozen schema (no new columns, no
incompatible dtypes, no duplicate keys, no cross-module writes), and upserts
on the declared key. There is no ALTER TABLE on the new path — ever.

Granularity derives from the declared primary key: ``valid_time`` in the key
marks a time-granular product (interpolations, forecasts; the row's own time
is part of its identity), otherwise ``scan_id`` marks scan granularity,
otherwise the table is run-granular. Identity columns of scan-granular
per-scan writes are stamped by the writer — modules never pass them.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api import types as pdt

from adapt.contracts.persistence import (
    DecisionTableWrite,
    PersistenceMeta,
    ProductTableWrite,
    ProductView,
)
from adapt.persistence.collection_catalog import Catalog
from adapt.persistence.errors import StoreError
from adapt.utils.time import to_scan_iso, to_scan_unix

__all__ = ["SchemaLedger", "TableDeclaration", "TableWriter", "table_granularity"]

_RESERVED_TABLES = frozenset({"table_schemas", "annotations"})

# Core identity columns carry contracted types, fixed by NAME — never inferred
# from a frame's dtype. Everything else is a source-dependent extra whose type
# follows the deterministic rule in _canonical_sqlite_type, so the frozen
# schema is a pure function of the column set: a NaN in whichever scan happens
# to arrive first must never decide the schema.
_CORE_COLUMN_TYPES: dict[str, str] = {
    "run_id": "TEXT",
    "scan_id": "TEXT",
    "scan_time": "TEXT",
    "scan_time_unix": "INTEGER",  # to_scan_unix returns whole epoch seconds
    "valid_time": "TEXT",
    "cell_uid": "TEXT",
    "cell_label": "INTEGER",
}


# What the writer stamps on every row of a scan-granular table beside its key:
# the same for every row of one scan, in every table.
_SCAN_STAMPS = ("scan_time", "scan_time_unix")


def table_granularity(primary_key: Sequence[str]) -> str:
    """Derive a table's granularity from its declared primary key."""
    if "valid_time" in primary_key:
        return "time"
    if "scan_id" in primary_key:
        return "scan"
    return "run"


@dataclass(frozen=True)
class TableDeclaration:
    """One frozen table schema: ownership, keys, and column types."""

    table: str
    owner_module: str
    granularity: str
    primary_key: tuple[str, ...]
    index_columns: tuple[str, ...]
    columns: tuple[tuple[str, str], ...]  # (name, sqlite type) in frozen order


class SchemaLedger:
    """Frozen first-frame schema registry of one database of the collection,
    and the views over the tables that share a key.

    The snapshot of each table is written to ``db_path`` and, when a
    ``catalog`` is given, copied into catalog.db too. ``catalog=None`` means
    the catalog does not list this database's tables.

    A view of ``views`` is rebuilt whenever the set of tables can have changed:
    with a table's first write, and when asked (``build_views``).
    """

    def __init__(
        self, db_path: str | Path, catalog: Catalog | None, views: Sequence[ProductView]
    ) -> None:
        self.db_path = Path(db_path)
        self._catalog = catalog
        self._views = tuple(dict.fromkeys(views))  # one declaration given twice is one view
        # SQLite compares names without regard to case.
        names = [view.name.lower() for view in self._views]
        repeated = sorted({v.name for v in self._views if names.count(v.name.lower()) > 1})
        if repeated:
            raise StoreError(
                f"View name(s) {', '.join(repeated)} declared more than once, differently"
            )

    def frozen(self, table: str) -> TableDeclaration | None:
        """The frozen declaration for ``table``, or None before first freeze."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM table_schemas WHERE table_name = ?", (table,)
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _declaration(row)

    def freeze(self, decl: TableDeclaration) -> None:
        """Create the table + indexes, register it, and copy the snapshot into
        the catalog when the ledger has one.

        The table, its indexes, its snapshot in the ledger's file and the views it joins
        are made together or not at all: a table whose columns repeat a name in
        a view it joins is refused and not created. A view that does not exist
        yet (its own tables were never written) checks nothing: a repeat among
        the tables older than it is refused with the write that first builds it.
        """
        if decl.table in _RESERVED_TABLES:
            raise StoreError(f"'{decl.table}' is a reserved table name; modules cannot own it")
        if decl.table.lower() in {view.name.lower() for view in self._views}:
            raise StoreError(f"'{decl.table}' is the name of a view; a table cannot take it")
        snapshot = _snapshot_row(decl)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")  # closed without a commit, all of it is undone
            self._create(conn, decl, snapshot)
            self._build_views(conn)
            conn.commit()
        finally:
            conn.close()
        if self._catalog is not None:
            self._catalog.register_table_schema(snapshot)

    def build_views(self) -> None:
        """Make every view whose tables exist what the tables now give."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")  # closed without a commit, all of it is undone
            self._build_views(conn)
            conn.commit()
        finally:
            conn.close()

    def _build_views(self, conn: sqlite3.Connection) -> None:
        tables = {
            row["table_name"]: _declaration(row)
            for row in conn.execute("SELECT * FROM table_schemas ORDER BY table_name")
        }
        for view in self._views:
            if any(table not in tables for table in (view.rows_from, *view.reads)):
                continue
            statement = f"CREATE VIEW {view.name} AS {_view_select(view, tables)}"
            stored = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?", (view.name,)
            ).fetchone()
            if stored is not None and stored["sql"] == statement:
                continue
            try:
                conn.execute(f"DROP VIEW IF EXISTS {view.name}")
                conn.execute(statement)
                conn.execute(f"SELECT * FROM {view.name} LIMIT 0")
            except sqlite3.OperationalError as exc:
                raise StoreError(f"View '{view.name}' cannot be read: {exc}") from exc

    @staticmethod
    def _create(conn: sqlite3.Connection, decl: TableDeclaration, snapshot: dict) -> None:
        col_defs = ", ".join(f"{name} {sqlite_type}" for name, sqlite_type in decl.columns)
        pk = ", ".join(decl.primary_key)
        try:
            conn.execute(f"CREATE TABLE {decl.table} ({col_defs}, PRIMARY KEY ({pk}))")
            for col in decl.index_columns:
                conn.execute(f"CREATE INDEX idx_{decl.table}_{col} ON {decl.table}({col})")
        except sqlite3.OperationalError as exc:
            raise StoreError(f"Table '{decl.table}' cannot be created: {exc}") from exc
        try:
            conn.execute(
                "INSERT INTO table_schemas (table_name, owner_module, granularity, "
                " primary_key, index_columns, columns_json, frozen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(snapshot.values()),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"Table '{decl.table}' is already frozen") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), isolation_level="DEFERRED")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


class TableWriter:
    """Writes one module's table; freezes on the first non-empty frame."""

    def __init__(
        self,
        ledger: SchemaLedger,
        spec: ProductTableWrite | DecisionTableWrite,
        owner_module: str,
    ) -> None:
        if spec.table in _RESERVED_TABLES:
            raise StoreError(f"'{spec.table}' is a reserved table name; modules cannot own it")
        self._ledger = ledger
        self._spec = spec
        self._owner = owner_module
        self._granularity = table_granularity(spec.primary_key)

    def write(self, df: pd.DataFrame, meta: PersistenceMeta) -> None:
        """Stamp identity, enforce the frozen schema, and upsert on the declared key."""
        if df is None or df.empty:
            return
        df = self._stamp(df, meta)

        missing_keys = [c for c in self._spec.primary_key if c not in df.columns]
        if missing_keys:
            raise StoreError(
                f"{self._spec.table}: frame is missing primary-key column(s) "
                f"{', '.join(missing_keys)}"
            )
        if df.duplicated(subset=list(self._spec.primary_key)).any():
            raise StoreError(
                f"{self._spec.table}: duplicate primary key rows within one frame — "
                "refusing to persist (rows would silently overwrite each other)"
            )

        frozen = self._ledger.frozen(self._spec.table)
        if frozen is None:
            frozen = TableDeclaration(
                table=self._spec.table,
                owner_module=self._owner,
                granularity=self._granularity,
                primary_key=tuple(self._spec.primary_key),
                index_columns=tuple(self._spec.index_columns),
                columns=tuple((c, self._frozen_type(c, df[c])) for c in df.columns),
            )
            self._ledger.freeze(frozen)
        else:
            if frozen.owner_module != self._owner:
                raise StoreError(
                    f"{self._spec.table}: owned by module '{frozen.owner_module}'; "
                    f"'{self._owner}' may not write to it"
                )
            self._validate_frame(df, frozen)
        self._upsert(df)

    def _frozen_type(self, name: str, series: pd.Series) -> str:
        if name in self._spec.integer_columns:
            return "INTEGER"
        return _canonical_sqlite_type(name, series)

    # -- identity stamping -------------------------------------------------------

    def _stamp(self, df: pd.DataFrame, meta: PersistenceMeta) -> pd.DataFrame:
        df = self._ensure_run_id(df, meta.run_id)
        if self._granularity == "scan":
            return self._stamp_scan_granular(df, meta)
        if self._granularity == "time" and "valid_time" not in df.columns:
            raise StoreError(
                f"{self._spec.table}: time-granular table requires a valid_time column "
                "(the time each row describes) in the frame"
            )
        return _with_scan_time_unix(df)

    def _ensure_run_id(self, df: pd.DataFrame, run_id: str) -> pd.DataFrame:
        """Stamp the runtime-owned run identity, or verify a module-carried column."""
        if "run_id" not in df.columns:
            df = df.copy()
            df["run_id"] = run_id
            return df
        foreign = df.loc[df["run_id"] != run_id, "run_id"].unique()
        if len(foreign):
            raise StoreError(
                f"{self._spec.table}: frame carries run_id value(s) "
                f"{', '.join(map(str, foreign))} that do not match the persist's "
                f"run '{run_id}'"
            )
        return df

    def _stamp_scan_granular(self, df: pd.DataFrame, meta: PersistenceMeta) -> pd.DataFrame:
        carried = [c for c in ("scan_id", *_SCAN_STAMPS) if c in df.columns]
        if carried:
            raise StoreError(
                f"{self._spec.table}: frame carries source-owned identity column(s) "
                f"{', '.join(carried)} — the writer stamps them from PersistenceMeta"
            )
        df = df.copy()
        df["scan_id"] = meta.scan_id
        df["scan_time"] = to_scan_iso(meta.scan_time)
        df["scan_time_unix"] = to_scan_unix(meta.scan_time)
        return df

    # -- enforcement + upsert ------------------------------------------------------

    def _validate_frame(self, df: pd.DataFrame, frozen: TableDeclaration) -> None:
        frozen_types = dict(frozen.columns)
        for col in df.columns:
            if col not in frozen_types:
                raise StoreError(
                    f"{self._spec.table}: column '{col}' is not in the frozen schema — "
                    "new columns are rejected after first-frame freeze"
                )
            actual = _sqlite_type(df[col])
            expected = frozen_types[col]
            # INTEGER and REAL interchange losslessly under SQLite affinity.
            # New freezes canonicalize measures to REAL, but pandas promotes
            # int columns to float64 whenever a NaN appears, so int frames must
            # keep writing into REAL columns, NaN-promoted frames into the
            # INTEGER core columns (cell_label), and legacy stores frozen
            # before canonicalization keep accepting both directions.
            if actual != expected and not ({actual, expected} == {"INTEGER", "REAL"}):
                raise StoreError(
                    f"{self._spec.table}: column '{col}' has incompatible dtype "
                    f"{actual}; frozen as {expected}"
                )

    def _upsert(self, df: pd.DataFrame) -> None:
        cols = list(df.columns)
        placeholders = ", ".join("?" for _ in cols)
        pk = set(self._spec.primary_key)
        update_cols = [c for c in cols if c not in pk]
        conflict = ", ".join(self._spec.primary_key)
        if update_cols:
            action = "DO UPDATE SET " + ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        else:
            action = "DO NOTHING"
        sql = (
            f"INSERT INTO {self._spec.table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) {action}"
        )
        rows = [tuple(_to_sqlite(v) for v in row) for row in df.itertuples(index=False)]
        conn = self._ledger._connect()
        try:
            conn.executemany(sql, rows)
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            conn.close()


def _declaration(row: sqlite3.Row) -> TableDeclaration:
    return TableDeclaration(
        table=row["table_name"],
        owner_module=row["owner_module"],
        granularity=row["granularity"],
        primary_key=tuple(json.loads(row["primary_key"])),
        index_columns=tuple(json.loads(row["index_columns"])),
        columns=tuple((c["name"], c["type"]) for c in json.loads(row["columns_json"])),
    )


def _view_select(view: ProductView, tables: dict[str, TableDeclaration]) -> str:
    """The SELECT of ``view``: its row table, joined with every table of
    ``tables`` that has the same primary key (in name order), then its computed
    columns.

    Raises
    ------
    StoreError
        The view's name is a table's or a reserved one, its row table is not
        scan-granular, or two of its columns would have the same name.
    """
    if view.name.lower() in {name.lower() for name in (*_RESERVED_TABLES, *tables)}:
        raise StoreError(f"Cannot create view '{view.name}': a table of that name exists")
    rows = tables[view.rows_from]
    if rows.granularity != "scan":
        raise StoreError(
            f"View '{view.name}' takes its rows from table '{view.rows_from}', which has "
            f"no row per scan (primary key {', '.join(rows.primary_key)})"
        )
    shared = (*rows.primary_key, *_SCAN_STAMPS)
    joined = [
        table
        for name, table in tables.items()
        if name != view.rows_from and set(table.primary_key) == set(rows.primary_key)
    ]
    holder: dict[str, str] = {}
    _claim(view, holder, [column for column, _ in rows.columns], f"table '{rows.table}'")
    for table in joined:
        own = [column for column, _ in table.columns if column not in shared]
        _claim(view, holder, own, f"table '{table.table}'")
    _claim(view, holder, [name for name, _ in view.computed], "its computed columns")
    columns = ["*", *(f"{expression} AS {name}" for name, expression in view.computed)]
    joins = [f"LEFT JOIN {table.table} USING ({', '.join(shared)})" for table in joined]
    return "\n".join([f"SELECT {', '.join(columns)}", f"FROM {view.rows_from}", *joins])


def _claim(view: ProductView, holder: dict[str, str], columns: list[str], owner: str) -> None:
    """Record ``owner`` as the holder of ``columns``. A name already held is
    refused: SQLite compares names without regard to case and would rename the
    repeat silently."""
    for column in columns:
        name = column.lower()
        if name in holder:
            raise StoreError(
                f"View '{view.name}': column '{column}' is in {holder[name]} and in {owner}. "
                "Tables with the same key are read as one through this view, so each "
                "column name may be used once, whatever its case — rename one"
            )
        holder[name] = owner


def _snapshot_row(decl: TableDeclaration) -> dict:
    return {
        "table_name": decl.table,
        "owner_module": decl.owner_module,
        "granularity": decl.granularity,
        "primary_key": json.dumps(list(decl.primary_key)),
        "index_columns": json.dumps(list(decl.index_columns)),
        "columns_json": json.dumps([{"name": n, "type": t} for n, t in decl.columns]),
        "frozen_at": datetime.now(UTC).isoformat(),
    }


def _with_scan_time_unix(df: pd.DataFrame) -> pd.DataFrame:
    """Every frame with a scan_time column also gets the machine-readable twin."""
    if "scan_time" not in df.columns or "scan_time_unix" in df.columns:
        return df
    df = df.copy()
    df["scan_time_unix"] = df["scan_time"].map(to_scan_unix)
    return df


def _sqlite_type(series: pd.Series) -> str:
    """Map a pandas Series dtype to a SQLite column type (frame validation)."""
    if pdt.is_bool_dtype(series) or pdt.is_integer_dtype(series):
        return "INTEGER"
    if pdt.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def _canonical_sqlite_type(name: str, series: pd.Series) -> str:
    """The FROZEN type of a column: contracted by name for core identity
    columns, deterministic by kind for everything else.

    Numeric measures always freeze as REAL — never as the first frame's luck
    of INTEGER-vs-NaN-promoted-float. Bools stay INTEGER: they never
    NaN-promote (a missing bool becomes object dtype, a genuine data bug that
    must fail validation loudly).
    """
    if name in _CORE_COLUMN_TYPES:
        return _CORE_COLUMN_TYPES[name]
    if pdt.is_bool_dtype(series):
        return "INTEGER"
    if pdt.is_integer_dtype(series) or pdt.is_float_dtype(series):
        return "REAL"
    return "TEXT"


def _to_sqlite(value):
    """Coerce a pandas/numpy scalar to a SQLite-storable Python value.

    Datetimes (python, pandas Timestamp, numpy datetime64) are canonicalized to
    the shared scan-time string so product tables order consistently.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, np.datetime64)):
        return to_scan_iso(value)
    if pd.api.types.is_bool(value):
        return int(value)
    if hasattr(value, "item"):
        return value.item()
    return value
