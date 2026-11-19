# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from dataclasses import dataclass, field

import pandas as pd

from adapt.contracts import (
    DecisionTableWrite,
    NetcdfArtifact,
    ProductTableWrite,
    ProductView,
    SharedConfig,
    check_cell_adjacency,
    check_cell_events,
    check_cell_identity,
    check_cell_tracks,
    check_module_state,
    check_projected_ds,
    check_tracked_cells,
    check_tracking_decisions,
)
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.tracking.config import TrackingConfig, TrackingOptions
from adapt.modules.tracking.lut import attach_cell_uid_lut, attach_registration_uid_lut
from adapt.modules.tracking.module import CellTracker, TrackerState
from adapt.modules.tracking.tables import (
    EVENT_INTEGER_COLUMNS,
    IDENTITY_INTEGER_COLUMNS,
    TRACK_INTEGER_COLUMNS,
    Lifecycle,
    scan_tables,
)

# One row per cell of a scan: its identity joined to its statistics, plus the
# flags a later scan's events set on it (it split, merged away, or ended).
_CELLS_BY_SCAN = """
SELECT *,
    EXISTS (SELECT 1 FROM cell_events e WHERE e.run_id = cell_identity.run_id
            AND e.source_scan_id = cell_identity.scan_id
            AND e.source_cell_uid = cell_identity.cell_uid
            AND e.event_type = 'SPLIT') AS is_split_source_here,
    EXISTS (SELECT 1 FROM cell_events e WHERE e.run_id = cell_identity.run_id
            AND e.source_scan_id = cell_identity.scan_id
            AND e.source_cell_uid = cell_identity.cell_uid
            AND e.event_type = 'MERGE') AS is_merge_source_here,
    EXISTS (SELECT 1 FROM cell_events e WHERE e.run_id = cell_identity.run_id
            AND e.source_scan_id = cell_identity.scan_id
            AND e.source_cell_uid = cell_identity.cell_uid
            AND e.event_type = 'TERMINATION') AS is_terminated_after_here
FROM cell_identity
LEFT JOIN cell_stats USING (run_id, scan_id, scan_time, scan_time_unix, cell_label)
"""


@dataclass(frozen=True)
class TrackingState:
    """What tracking carries from one scan to the next.

    The tracker's own state, the lifecycle rows of the tracks that may still
    change, and the last scan's cells (their labels are the ones the next
    scan's ``registration_minutes`` carry).
    """

    tracker: TrackerState = field(default_factory=TrackerState)
    lifecycle: Lifecycle = field(default_factory=Lifecycle)
    last_cells: pd.DataFrame | None = None


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
    tracking_config : TrackingConfig
        The module's options and what ``configure`` adds for the run.
    scan_time : datetime
        Radar scan timestamp.

    Context outputs
    ---------------
    tracked_cells : pd.DataFrame
        Per-cell observations for the current scan with cell_uid/cell_label.
    cell_identity : pd.DataFrame
        One row per cell: its label, its uid and what is known of it at this
        scan (age, neighbours, how it began). Stored as ``cell_identity``; the
        ``cells_by_scan`` view joins it to ``cell_stats``.
    cell_events : pd.DataFrame
        Lineage event rows: CONTINUE, SPLIT, MERGE, INITIATION, TERMINATION,
        LATENT (a track kept for resumption) and RESUMED, each naming the scan
        its source cell was last observed in.
    cell_tracks : pd.DataFrame
        The lifecycle row of every track this scan touched.
    analysis_ds : xr.Dataset
        ``projected_ds`` plus the cell_uid LUTs (``cell_uid`` for this scan's
        labels, ``registration_cell_uid`` for the previous scan's) — the dataset
        persisted as the analysis NetCDF.
    """

    name = "tracking"
    summary = "link cells across scans"
    options_class = TrackingOptions
    inputs = [
        "projected_ds",
        "cell_stats",
        "cell_adjacency",
        "tracking_config",
        "scan_time",
        "scan_id",
        "tracking_state",
    ]
    # tracking_decisions: every candidate considered this scan and why each
    # cell continued or ended — analysis-only, written to decisions.db.
    outputs = [
        "tracked_cells",
        "cell_identity",
        "cell_events",
        "cell_tracks",
        "analysis_ds",
        "tracking_decisions",
        "tracking_state",
    ]
    input_contracts = {"projected_ds": check_projected_ds, "cell_adjacency": check_cell_adjacency}
    output_contracts = {
        "tracked_cells": check_tracked_cells,
        "cell_identity": check_cell_identity,
        "cell_events": check_cell_events,
        "cell_tracks": check_cell_tracks,
        "analysis_ds": check_projected_ds,
        "tracking_decisions": check_tracking_decisions,
        "tracking_state": check_module_state,
    }
    persistence = (
        NetcdfArtifact(
            key="analysis_ds",
            product_type="segmentation2d",
            description="Radar analysis with segmentation and projections",
        ),
        ProductTableWrite(
            key="cell_identity",
            table="cell_identity",
            primary_key=("run_id", "scan_id", "cell_label"),
            index_columns=("cell_uid",),
            integer_columns=IDENTITY_INTEGER_COLUMNS,
        ),
        ProductTableWrite(
            key="cell_events",
            table="cell_events",
            primary_key=("run_id", "scan_id", "event_seq"),
            index_columns=("source_cell_uid", "target_cell_uid", "event_group_id"),
            integer_columns=EVENT_INTEGER_COLUMNS,
        ),
        ProductTableWrite(
            key="cell_tracks",
            table="cell_tracks",
            primary_key=("run_id", "cell_uid"),
            integer_columns=TRACK_INTEGER_COLUMNS,
        ),
        ProductView(
            name="cells_by_scan",
            select=_CELLS_BY_SCAN,
            rows_from="cell_identity",
            reads=("cell_stats", "cell_events"),
        ),
        DecisionTableWrite(key="tracking_decisions"),
    )

    @classmethod
    def configure(cls, options: TrackingOptions, shared: SharedConfig) -> TrackingConfig:
        """The options, and the run's field and largest scan gap."""
        return TrackingConfig(
            **options.model_dump(),
            field_var=shared.tracking_field,
            max_tracking_gap_minutes=shared.max_scan_gap_minutes,
        )

    @classmethod
    def method_line(cls, config: TrackingConfig) -> str:
        return (
            f"gap {config.max_tracking_gap_minutes:g}min "
            f"· u≤{config.max_overlap_mismatch:g} · c≤{config.max_link_cost:g} "
            f"· vmax {config.max_speed_ms:g}m/s"
        )

    def run(self, context: dict) -> dict:
        state: TrackingState = context["tracking_state"] or TrackingState()
        tracker = CellTracker(context["tracking_config"], state.tracker)
        ds_2d = context["projected_ds"]

        tracked_cells, events = tracker.track(
            ds_projected=ds_2d,
            cell_stats_df=context["cell_stats"],
            scan_id=context["scan_id"],
        )
        tables = scan_tables(
            tracked_cells,
            events,
            context["cell_adjacency"],
            scan_id=context["scan_id"],
            scan_time=context["scan_time"],
            lifecycle=state.lifecycle,
        )
        analysis_ds = attach_cell_uid_lut(ds_2d, tracked_cells)
        analysis_ds = attach_registration_uid_lut(analysis_ds, state.last_cells)
        # A scan without cells leaves the last scan's labels as they were.
        last_cells = state.last_cells if tracked_cells.empty else tracked_cells

        return {
            "tracked_cells": tracked_cells,
            "cell_identity": tables.identity,
            "cell_events": tables.events,
            "cell_tracks": tables.tracks,
            "analysis_ds": analysis_ds,
            "tracking_decisions": tracker.decisions(),
            "tracking_state": TrackingState(tracker.state, tables.lifecycle, last_cells),
        }


registry.register(TrackingModule)
