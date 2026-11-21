# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Persistence spec types. Modules declare HOW each output is persisted;
the persistence layer routes mechanically. Types only — zero logic."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NetcdfArtifact:
    """Write an xarray.Dataset (context key) as a NetCDF artifact.

    The file and its catalog row are attributed to the module that declares
    this spec.
    """

    key: str
    product_type: str  # e.g. "segmentation2d"
    description: str  # stamped into ds.attrs["description"]


@dataclass(frozen=True)
class ScanRecord:
    """One scan's registration in the catalog; built by the runtime after persist.

    ``scan_id`` is the content-derived identity (the join key); ``scan_time`` is
    canonical UTC ordering/display metadata. ``start_time``/``end_time`` are
    per-source coverage metadata — not every source has them.
    """

    run_id: str
    scan_id: str
    scan_time: datetime  # tz-aware UTC
    source_file_name: str
    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass(frozen=True)
class ProductTableWrite:
    """Upsert a DataFrame (context key) into a module-owned products.db table.

    The schema freezes on the first non-empty frame (SchemaLedger); the table's
    granularity derives from the primary key: ``valid_time`` in the key marks a
    time-granular product, otherwise ``scan_id`` marks scan granularity,
    otherwise the table is run-granular.
    """

    key: str
    table: str
    primary_key: tuple[str, ...]
    index_columns: tuple[str, ...] = ()
    # Columns frozen as INTEGER: counts and flags that are never missing. Every
    # other numeric column freezes as REAL (see SchemaLedger).
    integer_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductView:
    """A read-only products.db view of the tables that share one key.

    The view has a row for every row of the scan-granular table ``rows_from``,
    and carries the columns of every product table with the same primary key,
    whichever module wrote it. Each scan with rows in ``rows_from`` is linked to
    the view by name, so readers can wait for it like any other product.

    ``computed`` adds columns as (name, SQL expression) pairs. An expression may
    use ``rows_from`` and the declaring module's own tables ``reads``, naming
    every column with its table; the view exists once all of them do.
    """

    name: str
    rows_from: str
    computed: tuple[tuple[str, str], ...] = ()
    reads: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionTableWrite:
    """Write a decision bundle (context key) to the collection's analysis-only
    decisions.db. Never a scan product: no catalog link, no API exposure."""

    key: str


@dataclass(frozen=True)
class StoreRead:
    """Rows of the run, read from products.db into a module input.

    ``source`` is a product table or a view that a module declares
    (``ProductTableWrite.table``, ``ProductView.name``). Just before the module
    runs, the runtime puts the run's rows under ``key`` as a DataFrame with the
    stored columns:

    - ``up_to_scan=True``: the rows of every scan up to and including this one,
      oldest scan first. The source must have a row per scan (``scan_id`` in
      its key). A pass over a stored run sees no later scan.
    - ``up_to_scan=False``: every row of the run as it stands. In a pass over a
      stored run these are the rows the finished run left.

    Within that, rows come in the order of the key of the source's rows.

    The module lists ``key`` in ``inputs``. It runs after every listed module
    that writes a table the source is made of: the table itself; for a view,
    its row table, the tables its computed columns read, and every table with
    the row table's key. A run that makes new scans must list a module that
    writes the source's rows.

    Before any scan has written the source in the collection, the run has no
    rows: the frame is empty and has no columns.
    """

    key: str
    source: str
    up_to_scan: bool


PersistenceSpec = NetcdfArtifact | ProductTableWrite | ProductView | DecisionTableWrite


@dataclass(frozen=True)
class PersistenceMeta:
    """Run-scoped metadata the persistence router needs; built by the runtime per scan.

    Every write is one module's outputs for one scan. ``scan_id`` is the
    content-derived scan identity minted at the source boundary — the single
    join key across tables, catalog rows, and artifacts; ``scan_time`` is that
    scan's observation time, never the wall clock.
    """

    scan_time: datetime  # tz-aware UTC (ordering/display metadata)
    scan_id: str  # content-derived scan identity (join key)
    run_id: str
    source_file: str  # source scan path -> filename_stem, ds.attrs["source"]
    collection_id: str  # collection (radar/site data domain) identity; value = radar ID today
