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
    """A read-only products.db view over product tables: ``SELECT`` text ``select``.

    The view has a row for every row of table ``rows_from``, so each scan with
    rows there is linked to the view by name and readers can wait for it like
    any other product. ``reads`` names the other tables the SELECT uses: all of
    them must exist by the time ``rows_from`` is first written.
    """

    name: str
    select: str
    rows_from: str
    reads: tuple[str, ...]


@dataclass(frozen=True)
class DecisionTableWrite:
    """Write a decision bundle (context key) to the collection's analysis-only
    decisions.db. Never a scan product: no catalog link, no API exposure."""

    key: str


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
