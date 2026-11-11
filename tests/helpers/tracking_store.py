"""Write one scan's analysis and tracking tables as the pipeline does: the real
modules' persistence specs, the real row builder, the real router."""

from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from adapt.contracts.persistence import PersistenceMeta, ScanRecord
from adapt.execution.nodes.analysis import AnalysisModule
from adapt.execution.nodes.tracking import TrackingModule
from adapt.modules.tracking.tables import Lifecycle, scan_tables
from adapt.persistence.output_router import StoreOutputRouter

# The tracking module's tables and the view over them (not its NetCDF or decisions).
TRACKING_TABLES = tuple(
    spec for spec in TrackingModule.persistence if hasattr(spec, "table") or hasattr(spec, "select")
)
_TABLES = (
    SimpleNamespace(name="analysis", persistence=AnalysisModule.persistence),
    SimpleNamespace(name="tracking", persistence=TRACKING_TABLES),
)


def going_on(tracked: pd.DataFrame) -> pd.DataFrame:
    """The events of a scan in which every cell simply goes on."""
    return pd.DataFrame(
        [
            {
                "event_type": "CONTINUE",
                "source_cell_uid": uid,
                "target_cell_uid": uid,
                "source_cell_label": label,
                "target_cell_label": label,
                "cost": 0.0,
                "is_dominant": True,
                "event_group_id": f"CONTINUE:{uid}",
            }
            for label, uid in zip(tracked["cell_label"], tracked["cell_uid"], strict=True)
        ]
    )


def no_adjacency() -> pd.DataFrame:
    return pd.DataFrame(columns=["cell_label_a", "cell_label_b", "touching_boundary_pixels"])


def tracking_outputs(
    tracked: pd.DataFrame,
    events: pd.DataFrame,
    adjacency: pd.DataFrame,
    *,
    scan_id: str,
    scan_time: datetime,
    lifecycle: Lifecycle,
) -> tuple[dict, Lifecycle]:
    """The tracking node's table outputs for one scan, and the lifecycle after it."""
    tables = scan_tables(
        tracked, events, adjacency, scan_id=scan_id, scan_time=scan_time, lifecycle=lifecycle
    )
    outputs = {
        "cell_identity": tables.identity,
        "cell_events": tables.events,
        "cell_tracks": tables.tracks,
    }
    return outputs, tables.lifecycle


def persist_tracking(
    router: StoreOutputRouter, meta: PersistenceMeta, result: dict, lifecycle: Lifecycle
) -> Lifecycle:
    """Store the analysis and tracking tables of one registered scan.

    ``result`` holds what the modules pass on in memory: ``cell_stats``,
    ``cell_adjacency``, and the tracker's ``tracked_cells`` and ``cell_events``.
    Returns the lifecycle to hand to the run's next scan.
    """
    outputs, lifecycle = tracking_outputs(
        result["tracked_cells"],
        result["cell_events"],
        result["cell_adjacency"],
        scan_id=meta.scan_id,
        scan_time=meta.scan_time,
        lifecycle=lifecycle,
    )
    router.persist(_TABLES, {**result, **outputs}, meta)
    return lifecycle


class TrackedScans:
    """Writes scans of one run into a collection, carrying the track lifecycle."""

    def __init__(self, collection, run_id: str, lifecycle: Lifecycle | None = None) -> None:
        self._collection = collection
        self._run_id = run_id
        self._router = StoreOutputRouter(collection)
        self._lifecycle = Lifecycle() if lifecycle is None else lifecycle

    def write(
        self,
        scan_id: str,
        scan_time: datetime,
        *,
        stats: pd.DataFrame,
        tracked: pd.DataFrame,
        events: pd.DataFrame | None = None,
        adjacency: pd.DataFrame | None = None,
    ) -> None:
        """Register scan ``scan_id`` if it is new, then store its tables.

        Without ``events``, every cell simply goes on.
        """
        if self._collection.catalog.get_scan(self._run_id, scan_id) is None:
            self._collection.catalog.register_scan(
                ScanRecord(self._run_id, scan_id, scan_time, source_file_name=scan_id)
            )
        meta = PersistenceMeta(
            scan_time=scan_time,
            scan_id=scan_id,
            run_id=self._run_id,
            source_file=scan_id,
            collection_id=self._collection.collection_id,
        )
        result = {
            "cell_stats": stats,
            "cell_adjacency": no_adjacency() if adjacency is None else adjacency,
            "tracked_cells": tracked,
            "cell_events": going_on(tracked) if events is None else events,
        }
        self._lifecycle = persist_tracking(self._router, meta, result, self._lifecycle)


def one_cell_tables(uid: str, scan_time: datetime, scan_id: str = "the-scan") -> dict:
    """The tracking node's table outputs for a scan whose one cell (label 1) goes on as ``uid``."""
    tracked = pd.DataFrame(
        {"cell_label": [1], "cell_uid": [uid], "area": [10.0], "max_reflectivity": [45.0]}
    )
    outputs, _ = tracking_outputs(
        tracked,
        going_on(tracked),
        no_adjacency(),
        scan_id=scan_id,
        scan_time=scan_time,
        lifecycle=Lifecycle(),
    )
    return outputs
