# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""A stored run's cells as the target selection engine sees them, for store clients.

The only module in this package that imports adapt.api: it reads the tables and
hands them to the same conversion the pipeline's target_selection module uses.
"""

from collections.abc import Sequence
from datetime import datetime

from adapt.api.store_client import StoreClient
from adapt.modules.target_selection import Snapshot, TrackSummary, snapshot_at
from adapt.utils.time import from_scan_iso, to_scan_iso


def build_snapshot(
    client: StoreClient,
    run_id: str,
    collection: str,
    *,
    growth_window_scans: int,
    at: datetime | None = None,
    tracking_field: str | None = None,
    trend_fields: Sequence[str] = (),
    track_summary: TrackSummary = "at_scan",
) -> Snapshot:
    """Return the latest scan of a run as a frozen Snapshot (see ``snapshot_at``).

    With ``at``, the run is replayed as of that instant: only scans at or
    before ``at`` are visible, and the snapshot is of the latest of them.

    ``tracking_field`` names the canonical field whose per-cell max fills
    ``CellSnapshot.field_max``. Default: resolved once from the run's
    stored config provenance (``client.run_config``) — the store is the
    truth for what the run tracked.
    """
    if tracking_field is None:
        tracking_field = client.run_tracking_field(run_id)
    history = client.cells(run_id, collection)
    if history.empty:
        raise ValueError(f"No cells_by_scan rows for run {run_id!r} (collection {collection!r})")
    if at is not None:
        history = history[history["scan_time"] <= to_scan_iso(at)]
        if history.empty:
            raise ValueError(
                f"No scans at or before {to_scan_iso(at)} in run {run_id!r} "
                f"(collection {collection!r})"
            )
    return snapshot_at(
        history,
        client.tracks(collection, run_id=run_id),
        from_scan_iso(history["scan_time"].max()),
        tracking_field=tracking_field,
        growth_window_scans=growth_window_scans,
        trend_fields=trend_fields,
        track_summary=track_summary,
    )
