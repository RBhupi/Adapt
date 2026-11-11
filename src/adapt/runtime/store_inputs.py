# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Module inputs the runtime reads from the store, one scan at a time.

Two kinds, both fetched just before the module that reads them runs (so it sees
what earlier modules of the same scan have stored):

- **Stored outputs.** A module output is read back from where its module's
  persistence spec stored it — a NetCDF artifact as a dataset, a product table
  as the scan's rows. This is what lets a later run over a stored run
  (``run.source: stored_run``) feed a module without re-running its producers.
- **Store views.** Registered by key (the keys are ``contracts.STORE_VIEWS``):
  an input no module produces, built from the run's tables up to the scan.

Readers import persistence lazily inside the function body, so heavy
dependencies load only when a module declares the input.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
import xarray as xr

from adapt.contracts import STORE_VIEWS, NetcdfArtifact, ProductTableWrite
from adapt.execution.module_registry import registry as module_registry
from adapt.utils.time import to_scan_iso

if TYPE_CHECKING:
    from adapt.persistence.store import Collection

__all__ = [
    "NOT_STORED",
    "ScanAt",
    "read_store_input",
    "register_store_view",
    "unregister_store_view",
]

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


StoreView = Callable[[ScanAt], Any]

_VIEWS: dict[str, StoreView] = {}


def register_store_view(key: str, view: StoreView) -> None:
    """Register the reader that builds input ``key``; duplicates raise."""
    if not key:
        raise ValueError(f"Cannot register a store view with an empty key: {view}")
    if key in _VIEWS:
        raise RuntimeError(
            f"Store view '{key}' is already registered by {_VIEWS[key]}. "
            f"Cannot register {view} with the same key."
        )
    _VIEWS[key] = view


def unregister_store_view(key: str) -> None:
    """Remove a view (primarily for testing)."""
    _VIEWS.pop(key, None)


def read_store_input(key: str, scan: ScanAt) -> Any:
    """Input ``key`` for ``scan``: a store view, else the stored output of that key.

    Returns ``NOT_STORED`` when ``key`` is a stored output the run has not
    written for this scan.

    Raises
    ------
    KeyError
        ``key`` is neither a store view nor a stored output of a registered module.
    """
    if key in _VIEWS:
        return _VIEWS[key](scan)
    for name in module_registry.list_modules():
        for spec in module_registry.get(name).persistence:
            if isinstance(spec, NetcdfArtifact) and spec.key == key:
                return _stored_dataset(spec, scan)
            if isinstance(spec, ProductTableWrite) and spec.key == key:
                return _stored_rows(spec, scan)
    raise KeyError(
        f"'{key}' is not read from the store: it is neither a store view "
        f"({', '.join(sorted(_VIEWS))}) nor an output a module stores"
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


def _cells_so_far(scan: ScanAt) -> pd.DataFrame:
    """Every ``cells_by_scan`` row of the run up to and including the scan."""
    if not _exists(scan.collection, "cells_by_scan"):
        return pd.DataFrame()
    return _query(
        scan.collection,
        "SELECT * FROM cells_by_scan WHERE run_id = ? AND scan_time <= ? ORDER BY scan_time",
        (scan.run_id, to_scan_iso(scan.scan_time)),
    )


def _tracks_so_far(scan: ScanAt) -> pd.DataFrame:
    """The run's ``cell_tracks`` rows as they stand."""
    if not _exists(scan.collection, "cell_tracks"):
        return pd.DataFrame(columns=["cell_uid"])
    return _query(
        scan.collection,
        "SELECT * FROM cell_tracks WHERE run_id = ? ORDER BY first_seen_time",
        (scan.run_id,),
    )


register_store_view("cells_so_far", _cells_so_far)
register_store_view("tracks_so_far", _tracks_so_far)
if set(_VIEWS) != STORE_VIEWS:
    raise RuntimeError(
        f"The store views registered here ({sorted(_VIEWS)}) are not the ones "
        f"modules may read ({sorted(STORE_VIEWS)}); keep contracts.STORE_VIEWS in step"
    )
