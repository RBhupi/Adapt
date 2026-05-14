# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import check_cell_events, check_projected_ds, check_tracked_cells
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.tracking.module import RadarCellTracker


class TrackingModule(BaseModule):
    """Assign stable `cell_uid` identities to convective cells across consecutive radar scans.

    Produces scan-local tracking outputs. Any higher-level grouping/aggregation
    is outside this module's scope.

    Context inputs
    --------------
    projected_ds : xr.Dataset
        2D dataset with projections (output of ProjectionModule).
    cell_stats : pd.DataFrame
        Per-cell statistics (output of AnalysisModule).
    tracking_config : TrackingModuleConfig
        Runtime configuration for the tracker.
    scan_time : datetime
        Radar scan timestamp.

    Context outputs
    ---------------
    tracked_cells : pd.DataFrame
        Per-cell observations for the current scan with cell_uid/cell_label.
    cell_events : pd.DataFrame
        Explicit event rows for CONTINUE, SPLIT, MERGE, INITIATION, TERMINATION.
    """

    name = "tracking"
    inputs = ["projected_ds", "cell_stats", "tracking_config", "scan_time"]
    outputs = ["tracked_cells", "cell_events"]
    input_contracts  = {"projected_ds": check_projected_ds}
    output_contracts = {"tracked_cells": check_tracked_cells, "cell_events": check_cell_events}

    def __init__(self) -> None:
        self._tracker = None

    def run(self, context: dict) -> dict:
        config = context["tracking_config"]
        ds_2d = context["projected_ds"]
        cell_stats = context["cell_stats"]

        if self._tracker is None:
            self._tracker = RadarCellTracker(config)

        tracked_cells, cell_events = self._tracker.track(
            ds_projected=ds_2d,
            cell_stats_df=cell_stats,
        )

        return {
            "tracked_cells": tracked_cells,
            "cell_events": cell_events,
        }


registry.register(TrackingModule)
