# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module inputs the runtime reads from the store, one scan at a time.

Two kinds, both fetched just before the module that reads them runs (so it sees
what earlier modules of the same scan have stored):

- **Stored outputs.** A module output is read back from where its module's
  persistence spec stored it — a NetCDF artifact as a dataset, a product table
  as the scan's rows. This is what lets a later run over a stored run
  (``run.source: stored_run``) feed a module without re-running its producers.
- **Declared store reads.** The rows of the run a module declares it reads
  (``adapt.contracts.StoreRead``): a table or a view some module declares, read
  up to the scan or as it stands. The read names its source; this file names
  none.

Readers import persistence lazily inside the function body, so heavy
dependencies load only when a module declares the input.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
import xarray as xr

from adapt.contracts import NetcdfArtifact, ProductTableWrite, StoreRead
from adapt.execution.module_registry import registry as module_registry
from adapt.execution.pipeline_builder import store_read_tables
from adapt.utils.time import to_scan_iso

if TYPE_CHECKING:
    from adapt.persistence.store import Collection

__all__ = ["NOT_STORED", "ScanAt", "read_rows", "read_store_input"]

# The writer stamps these onto every scan's rows; a frame read back is handed
# on as the module returned it, without them.
_STAMPED = ("run_id", "scan_id", "scan_time", "scan_time_unix")

NOT_STORED: Any = object()  # the store holds nothing under this key for the scan


@dataclass(frozen=True)
class ScanAt:
    """The scan an input is read for."""

    collection: "Collection"
    run_id: str
    scan_id: str
    scan_time: datetime


def read_rows(read: StoreRead, scan: ScanAt) -> pd.DataFrame:
    """The rows of the scan's run that ``read`` declares.

    Up to the scan: the rows of every scan up to and including it, oldest scan
    first; else every row of the run as it stands. Within that, in the order of
    the key of the source's rows. Before any scan has written the source in
    this collection the run has no rows: an empty frame without columns.
    """
    if not _exists(scan.collection, read.source):
        return pd.DataFrame()
    key = ", ".join(store_read_tables(read.source)[0].primary_key)
    if read.up_to_scan:
        return _query(
            scan.collection,
            f"SELECT * FROM {read.source} WHERE run_id = ? AND scan_time <= ? "
            f"ORDER BY scan_time, {key}",
            (scan.run_id, to_scan_iso(scan.scan_time)),
        )
    return _query(
        scan.collection,
        f"SELECT * FROM {read.source} WHERE run_id = ? ORDER BY {key}",
        (scan.run_id,),
    )


def read_store_input(key: str, scan: ScanAt) -> Any:
    """Input ``key`` for ``scan``: the stored output of that key.

    Returns ``NOT_STORED`` when ``key`` is a stored output the run has not
    written for this scan.

    Raises
    ------
    KeyError
        ``key`` is not a stored output of a registered module.
    """
    for name in module_registry.list_modules():
        for spec in module_registry.get(name).persistence:
            if isinstance(spec, NetcdfArtifact) and spec.key == key:
                return _stored_dataset(spec, scan)
            if isinstance(spec, ProductTableWrite) and spec.key == key:
                return _stored_rows(spec, scan)
    raise KeyError(
        f"'{key}' is not read from the store: no registered module stores an output of that name"
    )


def _stored_dataset(spec: NetcdfArtifact, scan: ScanAt) -> Any:
    artifact = scan.collection.catalog.find_artifact(scan.run_id, scan.scan_id, spec.product_type)
    if artifact is None:
        return NOT_STORED
    # Fully in memory: no file handle outlives the read.
    return xr.load_dataset(scan.collection.objects_dir / artifact["object_name"])


def _stored_rows(spec: ProductTableWrite, scan: ScanAt) -> Any:
    if spec.table not in scan.collection.catalog.scan_products(scan.run_id, scan.scan_id):
        return NOT_STORED
    rows = _query(
        scan.collection,
        f"SELECT * FROM {spec.table} WHERE run_id = ? AND scan_id = ?",
        (scan.run_id, scan.scan_id),
    )
    return rows.drop(columns=[c for c in _STAMPED if c in rows.columns])


def _query(collection: "Collection", sql: str, params: tuple) -> pd.DataFrame:
    conn = sqlite3.connect(str(collection.products_path))
    try:
        return pd.read_sql_query(sql, conn, params=list(params))
    finally:
        conn.close()


def _exists(collection: "Collection", name: str) -> bool:
    conn = sqlite3.connect(str(collection.products_path))
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (name,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()
