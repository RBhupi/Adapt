# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Synthetic store for radar command tests — real production writers, known geometry.

Collection ``KTST``, completed run ``RUN``, three scans at 12:00:37, 12:05:37
and 12:10:37. The first scan of a run has no projection (no previous scan to
take a flow from); the other two carry 15 projection frames at the whole
minutes after the scan (12:06..12:20 and 12:11..12:25).

Grid: 21 x 21, x spacing 600 m, y spacing 800 m (pixel diagonal 1000 m).
``lat[j, i] = 40.0 + 0.01 * j``, ``lon[j, i] = -88.0 + 0.01 * i``.
origin_altitude 369.0 m; the analysis level z is 2000.0 m above it.

Cells (label: uid): 1 ``uid-a`` rows 4-5, cols 2-4 at scan time; projection
frame k (k + 1 minutes after the scan) moves it one column east per minute, so
frame k covers rows 4-5, cols 3+k..5+k. 2 ``uid-b`` is a stationary single
pixel at row 15, col 15.

cell_volume_stats (optional), keyed by cell label: scan index s gives label 1
(uid-a) base 1500 + 100 s, top 9500 + 100 s (metres above the origin); label 2
(uid-b) has no echo aloft, so its base and top are NaN, as the module writes them.

``add_run`` processes the same volumes as another run of the collection, with
volume stats for chosen scans only; ``add_volume_stats`` writes one scan's rows
late, as the module does when it finishes after the scan's cells are stored.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import xarray as xr

from adapt.contracts.persistence import NetcdfArtifact, PersistenceMeta
from adapt.execution.nodes.cell_volume_stats import CellVolumeStatsModule
from adapt.modules.tracking.tables import Lifecycle
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store, init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry
from adapt.runtime.acquire import StoreAcquirer
from tests.helpers.run_configs import new_run_config
from tests.helpers.tracking_store import persist_tracking

COLLECTION = "KTST"
RUN = "2026JUN01-1200-KTST"
UID_A = "uid-a"
UID_B = "uid-b"
SCAN_TIMES = tuple(datetime(2026, 6, 1, 12, m, 37, tzinfo=UTC) for m in (0, 5, 10))

_N = 21
_FRAMES = 15


class _Module(SimpleNamespace):
    """Minimal module stand-in: name + declared persistence specs."""


_INGEST = _Module(
    name="ingest",
    persistence=(NetcdfArtifact(key="grid_ds", product_type="gridded3d", description="grid"),),
)
_ANALYSIS = _Module(
    name="projection",
    persistence=(
        NetcdfArtifact(
            key="analysis_ds",
            product_type="segmentation2d",
            description="analysis",
        ),
    ),
)
_VOLUME = _Module(name="cell_volume_stats", persistence=CellVolumeStatsModule.persistence)


def _labels(cols: range) -> np.ndarray:
    labels = np.zeros((_N, _N), dtype=np.int32)
    labels[4:6, cols.start : cols.stop] = 1
    labels[15, 15] = 2
    return labels


def _analysis_ds(scan_time: datetime, projected: bool, absent_frame: int | None) -> xr.Dataset:
    rows, cols = np.mgrid[0:_N, 0:_N]
    ds = xr.Dataset(
        {
            "reflectivity": (("y", "x"), np.full((_N, _N), 40.0)),
            "cell_labels": (("y", "x"), _labels(range(2, 5))),
            "cell_uid": (("cell_label",), np.array(["NONE", UID_A, UID_B])),
        },
        coords={
            "x": np.arange(_N) * 600.0,
            "y": np.arange(_N) * 800.0,
            "lat": (("y", "x"), 40.0 + 0.01 * rows),
            "lon": (("y", "x"), -88.0 + 0.01 * cols),
            "origin_altitude": 369.0,
            "z": 2000.0,
        },
    )
    if not projected:
        return ds
    frames = np.stack([_labels(range(3 + k, 6 + k)) for k in range(_FRAMES)])
    if absent_frame is not None:
        frames[absent_frame][frames[absent_frame] == 1] = 0
    first = scan_time.replace(second=0, tzinfo=None) + timedelta(minutes=1)
    minutes = pd.date_range(first, periods=_FRAMES, freq="1min")
    ds["projection_minutes"] = xr.DataArray(
        frames,
        dims=("projection_minute", "y", "x"),
        coords={"projection_minute": minutes},
    )
    return ds


def _result(index: int, ds: xr.Dataset) -> dict:
    events = pd.DataFrame(
        [
            {
                "event_type": "INITIATION",
                "source_cell_uid": None,
                "target_cell_uid": uid,
                "source_cell_label": None,
                "target_cell_label": label,
                "cost": 0.0,
                "is_dominant": True,
                "event_group_id": f"g-{uid}",
            }
            for label, uid in ((1, UID_A), (2, UID_B))
        ]
    )
    return {
        "grid_ds": ds,
        "analysis_ds": ds,
        "cell_stats": pd.DataFrame(
            {
                "cell_label": [1, 2],
                "cell_area_sqkm": [2.88, 0.48],
                "radar_reflectivity_max": [50.0, 40.0],
                "area_40dbz_km2": [float("nan")] * 2,  # analysed, with no 40 dBZ echo
                "cell_centroid_mass_lat": [40.045, 40.15],
                "cell_centroid_mass_lon": [-87.97, -87.85],
            }
        ),
        "cell_adjacency": pd.DataFrame(
            columns=["cell_label_a", "cell_label_b", "touching_boundary_pixels"]
        ),
        "tracked_cells": pd.DataFrame(
            {
                "cell_label": [1, 2],
                "cell_uid": [UID_A, UID_B],
                "area": [2.88, 0.48],
                "max_reflectivity": [50.0, 40.0],
            }
        ),
        "cell_events": events if index == 0 else pd.DataFrame(),
        "cell_volume_stats_rows": pd.DataFrame(
            {
                "cell_label": [1, 2],
                "cell_base_m": [1500.0 + 100 * index, np.nan],
                "cell_top_m": [9500.0 + 100 * index, np.nan],
            }
        ),
    }


def analysis_ds(index: int, absent_frame: int | None = None) -> xr.Dataset:
    """Scan ``index``'s analysis dataset, as tracking hands it on."""
    return _analysis_ds(SCAN_TIMES[index], projected=index > 0, absent_frame=absent_frame)


def scan_outputs(index: int) -> dict:
    """What the modules up to tracking and cell_volume_stats hand on at scan
    ``index``: the datasets, the cell tables and the tracker's raw output."""
    return _result(index, analysis_ds(index))


def volume_rows(index: int) -> pd.DataFrame:
    """Scan ``index``'s cell_volume_stats rows, as the module hands them on."""
    return _result(index, analysis_ds(index))["cell_volume_stats_rows"]


def build_store(tmp_path, volume_stats: bool, absent_frame: int | None = None) -> SimpleNamespace:
    """Build the store under ``tmp_path`` with completed run ``RUN``; returns its
    root and scan ids.

    ``volume_stats=False`` leaves the collection without a cell_volume_stats
    table; ``absent_frame`` removes uid-a from that projection frame.
    """
    root = init_store(tmp_path / "store")
    StoreRegistry.get_instance(root).register_collection(
        COLLECTION, source_kind="nexrad", lat=40.0, lon=-88.0
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for scan_time in SCAN_TIMES:
        name = _raw_name(scan_time)
        (raw_dir / name).write_bytes(name.encode())
    built = SimpleNamespace(root=root, raw_dir=raw_dir, absent_frame=absent_frame)
    built.scan_ids = add_run(built, RUN, volume_scans=(0, 1, 2) if volume_stats else ())
    return built


def add_run(built: SimpleNamespace, run_id: str, volume_scans: tuple[int, ...]) -> tuple[str, ...]:
    """Process the three volumes again as completed run ``run_id``
    (``YYYYMONDD-HHMM-KTST``) of the same collection; only the scans indexed in
    ``volume_scans`` get cell_volume_stats rows."""
    store = Store.open(built.root)
    collection = store.collection(COLLECTION)
    registry = StoreRegistry.get_instance(built.root)
    registry.begin_run(
        RunStart(
            run_id=run_id,
            collection_id=COLLECTION,
            config_hash=f"hash-{run_id}",
            config_json=_run_config(built.root, run_id),
            pipeline_version="1.0",
            environment_json="{}",
        )
    )
    router = StoreOutputRouter(collection)
    acquirer = StoreAcquirer(collection, run_id)
    scan_ids = []
    lifecycle = Lifecycle()
    for index, scan_time in enumerate(SCAN_TIMES):
        path = built.raw_dir / _raw_name(scan_time)
        message = acquirer.acquire_file(path, source_uri=str(path), scan_time=scan_time)
        meta = _meta(run_id, index, message["scan_id"])
        ds = _analysis_ds(scan_time, projected=index > 0, absent_frame=built.absent_frame)
        modules = [_INGEST, _ANALYSIS] + ([_VOLUME] if index in volume_scans else [])
        result = _result(index, ds)
        router.persist(modules, result, meta)
        lifecycle = persist_tracking(router, meta, result, lifecycle)
        collection.catalog.record_scan_pass(
            run_id, message["scan_id"], {"tracking": ("done", None)}
        )
        scan_ids.append(message["scan_id"])
    registry.finalize_run(run_id, "completed", scans_processed=len(SCAN_TIMES))
    store.close()
    return tuple(scan_ids)


def add_volume_stats(built: SimpleNamespace, run_id: str, index: int) -> None:
    """Write scan ``index``'s cell_volume_stats rows for ``run_id``, after the
    scan's cells are already stored."""
    store = Store.open(built.root)
    meta = _meta(run_id, index, built.scan_ids[index])
    ds = _analysis_ds(SCAN_TIMES[index], projected=index > 0, absent_frame=built.absent_frame)
    StoreOutputRouter(store.collection(COLLECTION)).persist([_VOLUME], _result(index, ds), meta)
    store.close()


def _run_config(root, run_id: str) -> str:
    """The config a run records, as the orchestrator writes it: that of new run
    ``run_id`` of a plain NEXRAD pipeline file of the collection."""
    return new_run_config(root, run={"radar": COLLECTION}, run_id=run_id).model_dump_json()


def _raw_name(scan_time: datetime) -> str:
    return f"KTST{scan_time:%Y%m%d_%H%M%S}_V06"


def _meta(run_id: str, index: int, scan_id: str) -> PersistenceMeta:
    return PersistenceMeta(
        scan_time=SCAN_TIMES[index],
        scan_id=scan_id,
        run_id=run_id,
        source_file=_raw_name(SCAN_TIMES[index]),
        collection_id=COLLECTION,
    )
