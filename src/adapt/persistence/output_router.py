# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Mechanical persistence of module-declared outputs.

The router knows spec TYPES, never module key names. Adding a module output
means declaring a spec on the module — this file does not change.

Skip/raise rules: a key absent from the result means the module did not run
this scan (skip); an empty DataFrame means no rows (skip); everything else
that prevents a declared write is an error and raises.
"""

import logging
from collections.abc import Iterable

from adapt.contracts import (
    DecisionTableWrite,
    NetcdfArtifact,
    PersistenceMeta,
    PersistenceSpec,
    ProductTableWrite,
    ProductView,
)
from adapt.persistence.decision_store import DecisionStore
from adapt.persistence.errors import StoreError
from adapt.persistence.objects import ArtifactMeta, ObjectStore, product_object_name
from adapt.persistence.products import SchemaLedger, TableWriter
from adapt.persistence.store import Collection
from adapt.utils.time import to_scan_iso

logger = logging.getLogger(__name__)


class StoreOutputRouter:
    """Persist module-declared outputs into the store. No science names.

    Dispatch: NetcdfArtifact → object named for its run, module and source file
    (rewritten in place when the run writes the scan again) + catalog row +
    lineage to the scan's raw volume + scan-product link; ProductTableWrite →
    products.db rows (frozen first-frame schema) + table link; ProductView → a
    link for each scan that has rows in it.

    ``views`` are the views of the collection, whichever module declares them:
    the store keeps each one the join of the tables that now share its key, so
    a table written by a module that declares none still joins it.
    """

    def __init__(self, collection: Collection, views: Iterable[ProductView]) -> None:
        self._collection = collection
        self._objects = ObjectStore(collection.objects_dir, collection.catalog)
        self._views = tuple(views)
        self._ledger = SchemaLedger(collection.products_path, collection.catalog, self._views)
        self._ledger.build_views()

    def persist(self, modules: Iterable, result: dict, meta: PersistenceMeta) -> None:
        for module in modules:
            for spec in module.persistence:
                self._dispatch(module.name, spec, result, meta)

    def _dispatch(
        self, module_name: str, spec: PersistenceSpec, result: dict, meta: PersistenceMeta
    ) -> None:
        match spec:
            case NetcdfArtifact():
                self._write_netcdf(module_name, spec, result, meta)
            case ProductTableWrite():
                self._write_table(module_name, spec, result, meta)
            case ProductView():
                self._link_view(spec, meta)
            case DecisionTableWrite():
                self._write_decisions(spec, result, meta)
            case _:
                raise TypeError(f"{module_name}: unknown persistence spec {type(spec).__name__}")

    def _write_decisions(
        self, spec: DecisionTableWrite, result: dict, meta: PersistenceMeta
    ) -> None:
        """Analysis-only decision log: never a scan product, so no catalog link.

        One connection per scan, as for the tracking tables.
        """
        if spec.key not in result:
            return
        with DecisionStore(self._collection.decisions_path) as store:
            store.write(result[spec.key], meta)

    def _write_netcdf(
        self, module_name: str, spec: NetcdfArtifact, result: dict, meta: PersistenceMeta
    ) -> None:
        if spec.key not in result:
            return
        scan_time, scan_id = meta.scan_time, meta.scan_id
        raw = self._collection.catalog.find_artifact_by_scan(scan_id, "raw_volume")
        if raw is None:
            raise StoreError(
                f"{module_name}: no raw_volume artifact cataloged for scan '{scan_id}' "
                f"of run '{meta.run_id}' — the source must commit the raw object "
                "before products persist"
            )
        scan = self._collection.catalog.get_scan(meta.run_id, scan_id)
        if scan is None:
            raise StoreError(
                f"{module_name}: scan '{scan_id}' is not registered for run "
                f"'{meta.run_id}' — the source must register the scan before products persist"
            )
        ds = result[spec.key]
        # attrs key stays "radar" — stored-artifact format unchanged. scan_id/
        # scan_time make the artifact self-describing: consumers key on attrs,
        # never on filenames or the time coordinate.
        ds.attrs.update(
            {
                "source": meta.source_file,
                "radar": meta.collection_id,
                "description": spec.description,
                "scan_id": scan_id,
                "scan_time": to_scan_iso(scan_time),
            }
        )
        # One readable file per (run, module, scan, product), named for the source
        # file THIS run read (the same bytes may be stored under another name too).
        # A run that writes a scan again (resume, retry) replaces its own file; the
        # artifact keeps its id.
        handle = self._objects.begin(
            product_object_name(
                meta.run_id, module_name, scan["source_file_name"], spec.product_type
            )
        )
        try:
            ds.to_netcdf(handle.staging_path)
        except Exception:
            self._objects.abort(handle)
            raise
        record = self._objects.commit(
            handle,
            ArtifactMeta(
                artifact_type=spec.product_type,
                producer=module_name,
                run_id=meta.run_id,
                scan_id=scan_id,
                observation_time=scan_time,
            ),
            parents=(raw["artifact_id"],),
            replace=True,
        )
        self._collection.catalog.link_scan_product(
            meta.run_id, scan_id, spec.product_type, artifact_id=record.artifact_id
        )

    def _write_table(
        self, module_name: str, spec: ProductTableWrite, result: dict, meta: PersistenceMeta
    ) -> None:
        df = result.get(spec.key)
        if df is None or df.empty:
            return
        TableWriter(self._ledger, spec, owner_module=module_name).write(df, meta)
        self._collection.catalog.link_scan_product(
            meta.run_id, meta.scan_id, spec.table, table_name=spec.table
        )

    def _link_view(self, spec: ProductView, meta: PersistenceMeta) -> None:
        """Link the scan to the view when the table its rows come from was written for it."""
        if spec.rows_from not in self._collection.catalog.scan_products(meta.run_id, meta.scan_id):
            return
        if spec not in self._views:
            raise StoreError(
                f"View '{spec.name}' is not one of the views this collection's writer was "
                f"given, yet scan '{meta.scan_id}' has '{spec.rows_from}' rows"
            )
        missing = [t for t in (spec.rows_from, *spec.reads) if self._ledger.frozen(t) is None]
        if missing:
            raise StoreError(
                f"View '{spec.name}' reads table(s) {', '.join(missing)}, never written in "
                f"this collection, yet scan '{meta.scan_id}' has '{spec.rows_from}' rows"
            )
        self._collection.catalog.link_scan_product(
            meta.run_id, meta.scan_id, spec.name, table_name=spec.name
        )
