# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Synthetic store builder for API tests — real writers, zero inline DDL.

Builds an initialized store through the SAME machinery the pipeline uses
(init_store, StoreRegistry, StoreAcquirer, StoreOutputRouter), so API tests
read exactly what production writes. Layout built here:

- collection ``KTST`` with one completed run ``run-1``:
  two COMPLETE scans (raw volume + gridded3d + segmentation2d objects,
  cell_stats/cell_adjacency tables, tracking rows) at 12:00 and 12:05,
  plus one PENDING scan at 12:10 (raw acquired, never processed).
- a second running run ``run-2`` over the same first volume (cross-run union).
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import xarray as xr

from adapt.contracts.persistence import NetcdfArtifact, PersistenceMeta
from adapt.modules.tracking.tables import Lifecycle
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store, init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry
from adapt.runtime.acquire import StoreAcquirer
from tests.helpers.tracking_store import VIEWS, going_on, persist_tracking

COLLECTION = "KTST"
RUN_1 = "run-1"
RUN_2 = "run-2"
T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


class _Module(SimpleNamespace):
    """Minimal module stand-in: name + declared persistence specs."""


GRID_SPEC = NetcdfArtifact(key="grid_ds", product_type="gridded3d", description="grid")
ANALYSIS_SPEC = NetcdfArtifact(
    key="analysis_ds", product_type="segmentation2d", description="analysis"
)

# The NetCDF products of a scan; its tables go through ``persist_tracking``.
MODULES = [
    _Module(name="ingest", persistence=(GRID_SPEC,)),
    _Module(name="tracking", persistence=(ANALYSIS_SPEC,)),
]


def _persist(collection, router, meta: PersistenceMeta, result: dict, lifecycle) -> Lifecycle:
    """Store one processed scan as the pipeline does, and mark it complete."""
    router.persist(MODULES, result, meta)
    lifecycle = persist_tracking(router, meta, result, lifecycle)
    done = dict.fromkeys(("ingest", "analysis", "tracking"), ("done", None))
    collection.catalog.record_scan_pass(meta.run_id, meta.scan_id, done)
    return lifecycle


def _analysis_ds(cell_label: int) -> xr.Dataset:
    labels = np.zeros((4, 4), dtype=np.int32)
    labels[0, 0] = cell_label
    return xr.Dataset(
        {
            "reflectivity": (("y", "x"), np.full((4, 4), 40.0 + cell_label)),
            "cell_labels": (("y", "x"), labels),
        },
        coords={"x": np.arange(4), "y": np.arange(4)},
    )


def scan_result(index: int, uid: str, events: pd.DataFrame) -> dict:
    return {
        "grid_ds": _analysis_ds(1),
        "analysis_ds": _analysis_ds(1),
        "cell_stats": pd.DataFrame(
            {
                "cell_label": [1],
                "cell_area_sqkm": [10.0 * (index + 1)],
                "radar_reflectivity_max": [45.0 + index],
                "area_40dbz_km2": [float("nan")],  # analysed, with no 40 dBZ echo
                "cell_centroid_mass_lat": [40.0 + 0.01 * index],
                "cell_centroid_mass_lon": [-88.0 + 0.01 * index],
            }
        ),
        "cell_adjacency": pd.DataFrame(
            columns=["cell_label_a", "cell_label_b", "touching_boundary_pixels"]
        ),
        "tracked_cells": pd.DataFrame(
            {
                "cell_label": [1],
                "cell_uid": [uid],
                "area": [10.0 * (index + 1)],
                "max_reflectivity": [45.0 + index],
            }
        ),
        "cell_events": events,
    }


def initiation(uid: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_type": "INITIATION",
                "source_cell_uid": None,
                "target_cell_uid": uid,
                "source_cell_label": None,
                "target_cell_label": 1,
                "cost": 0.0,
                "is_dominant": True,
                "event_group_id": f"g-{uid}",
            }
        ]
    )


def build_synthetic_store(tmp_path) -> SimpleNamespace:
    """Build the synthetic store; returns roots, ids, and the acquired messages."""
    root = init_store(tmp_path / "store")
    store = Store.open(root)
    collection = store.collection(COLLECTION)
    registry = StoreRegistry.get_instance(root)
    registry.register_collection(COLLECTION, source_kind="nexrad", lat=40.0, lon=-88.0)

    def _begin(run_id: str) -> None:
        registry.begin_run(
            RunStart(
                run_id=run_id,
                collection_id=COLLECTION,
                config_hash=f"hash-{run_id}",
                # Persisted-key spelling matches production: the orchestrator
                # writes InternalConfig.model_dump_json() (no aliases), so the
                # global section is stored under "global_".
                config_json=json.dumps(
                    {"global_": {"tracking_field": "reflectivity", "z_level": 2000.0}}
                ),
                pipeline_version="1.0",
                environment_json="{}",
            )
        )

    _begin(RUN_1)
    router = StoreOutputRouter(collection, VIEWS)
    acquirer = StoreAcquirer(collection, RUN_1)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    messages = []
    for minutes in (0, 5, 10):
        name = f"KTST20260601_12{minutes:02d}00_V06"
        path = raw_dir / name
        path.write_bytes(name.encode())
        messages.append(
            acquirer.acquire_file(path, source_uri=str(path), scan_time=T0.replace(minute=minutes))
        )

    uid = "uid-1"
    lifecycle = Lifecycle()
    for index in (0, 1):  # scan 3 stays pending: acquired, never processed
        message = messages[index]
        meta = PersistenceMeta(
            scan_time=message["scan_time"],
            scan_id=message["scan_id"],
            run_id=RUN_1,
            source_file=f"KTST20260601_12{index * 5:02d}00_V06",
            collection_id=COLLECTION,
        )
        events = initiation(uid) if index == 0 else pd.DataFrame()
        lifecycle = _persist(collection, router, meta, scan_result(index, uid, events), lifecycle)
    registry.finalize_run(RUN_1, "completed", scans_processed=2)

    # Second run: reuses the first raw volume, processes it, stays running.
    _begin(RUN_2)
    acquirer_2 = StoreAcquirer(collection, RUN_2)
    message_2 = acquirer_2.acquire_existing(str(raw_dir / "KTST20260601_120000_V06"), scan_time=T0)
    meta_2 = PersistenceMeta(
        scan_time=T0,
        scan_id=message_2["scan_id"],
        run_id=RUN_2,
        source_file="KTST20260601_120000_V06",
        collection_id=COLLECTION,
    )
    lifecycle_2 = _persist(
        collection, router, meta_2, scan_result(0, "uid-r2", initiation("uid-r2")), Lifecycle()
    )

    store.close()
    return SimpleNamespace(
        root=root,
        collection_id=COLLECTION,
        run_1=RUN_1,
        run_2=RUN_2,
        scan_ids=[m["scan_id"] for m in messages],
        scan_times=[m["scan_time"] for m in messages],
        raw_artifact_ids=[m["artifact_id"] for m in messages],
        uid=uid,
        # Each run's track lifecycle after the scans processed here.
        lifecycles={RUN_1: lifecycle, RUN_2: lifecycle_2},
    )


def persist_scan(built: SimpleNamespace, index: int, run_id: str, uid: str) -> None:
    """Process raw scan ``index`` for ``run_id`` (already acquired by it): one cell,
    going on as ``uid``, stored as the pipeline stores a scan."""
    store = Store.open(built.root)
    collection = store.collection(COLLECTION)
    meta = PersistenceMeta(
        scan_time=built.scan_times[index],
        scan_id=built.scan_ids[index],
        run_id=run_id,
        source_file=f"KTST20260601_12{index * 5:02d}00_V06",
        collection_id=COLLECTION,
    )
    result = scan_result(index, uid, going_on_events(uid))
    built.lifecycles[run_id] = _persist(
        collection, StoreOutputRouter(collection, VIEWS), meta, result, built.lifecycles[run_id]
    )
    store.close()


def going_on_events(uid: str) -> pd.DataFrame:
    """The events of a scan whose one cell simply goes on as ``uid``."""
    return going_on(pd.DataFrame({"cell_label": [1], "cell_uid": [uid]}))
