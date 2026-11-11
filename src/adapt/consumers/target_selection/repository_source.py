# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Boundary between the repository and the engine.

The only module in this package that imports adapt.api. Repository
tables are converted to the frozen Snapshot exactly once, here; the
engine never sees pandas or the StoreClient.
"""

import re
from collections.abc import Iterable, Sequence
from datetime import datetime

import numpy as np
import pandas as pd

from adapt.api.store_client import StoreClient
from adapt.consumers.target_selection.config import TrackSummary
from adapt.consumers.target_selection.snapshot import (
    CellSnapshot,
    Snapshot,
    TrajectoryPoint,
)
from adapt.contracts import stat_column
from adapt.utils.time import from_scan_iso, to_scan_iso

# cell_tracks is a running summary rewritten every scan, so at replay time it
# already holds the future. The causal view recomputes these columns from the
# history visible at the snapshot; the oracle view reads them from cell_tracks.
_TRACK_SUMMARY_COLUMNS = ("n_scans", "duration_seconds", "max_area_sqkm", "max_reflectivity")
_TRACK_TEXT_COLUMNS = ("origin_type",)


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
    """Return the latest scan of a run as a frozen Snapshot.

    With ``at``, the run is replayed as of that instant: only scans at or
    before ``at`` are visible (growth rates and scan cadence included).

    ``trend_fields`` names cells_by_scan columns whose per-minute OLS slope
    over the last ``growth_window_scans`` scans is added to ``values`` as
    ``trend_<column>_per_min``; ``values`` also carries ``origin_is_split``
    (1.0 for a split child, 0.0 otherwise) from cell_tracks, and the track
    summary ``n_scans``, ``duration_seconds``, ``max_area_sqkm`` and
    ``max_reflectivity`` (max of the tracking field): as of the snapshot's scan
    with ``track_summary="at_scan"``, or the stored end-of-run cell_tracks values
    with ``"end_of_run"`` (a perfect-knowledge oracle for replay studies).

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
    tracks = client.tracks(collection, run_id=run_id)

    scan_times = sorted(history["scan_time"].unique())
    latest_iso = scan_times[-1]
    scan_interval = _scan_interval_seconds(scan_times)

    latest = history[history["scan_time"] == latest_iso]
    oracle = track_summary == "end_of_run"
    wanted = (*_TRACK_SUMMARY_COLUMNS, *_TRACK_TEXT_COLUMNS) if oracle else _TRACK_TEXT_COLUMNS
    track_cols = [c for c in wanted if c in tracks.columns]
    merged = latest.merge(tracks[["cell_uid", *track_cols]], on="cell_uid", how="left").sort_values(
        "cell_uid"
    )
    missing = [c for c in trend_fields if c not in history.columns]
    if missing:
        raise ValueError(
            f"trend_fields {missing} not in cells_by_scan; available: {sorted(history.columns)}"
        )

    cells = tuple(
        _to_cell(
            row, history, scan_interval, growth_window_scans, tracking_field, trend_fields, oracle
        )
        for _, row in merged.iterrows()
    )
    return Snapshot(scan_time=from_scan_iso(latest_iso), cells=cells)


def _to_cell(
    row: pd.Series,
    history: pd.DataFrame,
    scan_interval: float | None,
    growth_window_scans: int,
    tracking_field: str,
    trend_fields: Sequence[str] = (),
    oracle: bool = False,
) -> CellSnapshot:
    uid = row["cell_uid"]
    cell_history = history[history["cell_uid"] == uid].sort_values("scan_time")
    values = {
        col: float(v)
        for col, v in row.items()
        if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)
    }
    values["origin_is_split"] = 1.0 if row.get("origin_type") == "SPLIT" else 0.0
    if not oracle:
        values.update(_track_summary(cell_history, stat_column(tracking_field, "max")))
    for col in trend_fields:
        values[f"trend_{col}_per_min"] = _trend(cell_history, col, growth_window_scans)
    # Derived values the gates and weights may name like any column.
    values["field_max"] = float(row[stat_column(tracking_field, "max")])
    values["growth_rate_sqkm_per_min"] = _growth_rate(cell_history, growth_window_scans)
    return CellSnapshot(
        uid=uid,
        lat=float(row["cell_centroid_mass_lat"]),
        lon=float(row["cell_centroid_mass_lon"]),
        area_sqkm=float(row["cell_area_sqkm"]),
        field_max=values["field_max"],
        age_seconds=float(row["age_seconds"]),
        growth_rate_sqkm_per_min=values["growth_rate_sqkm_per_min"],
        trajectory=_trajectory(row, scan_interval),
        values=values,
    )


def _track_summary(cell_history: pd.DataFrame, field_max_column: str) -> dict[str, float]:
    """The cell_tracks lifecycle columns, from this cell's history up to the snapshot."""
    first = from_scan_iso(cell_history["scan_time"].iloc[0])
    last = from_scan_iso(cell_history["scan_time"].iloc[-1])
    return {
        "n_scans": float(len(cell_history)),
        "duration_seconds": (last - first).total_seconds(),
        "max_area_sqkm": float(cell_history["cell_area_sqkm"].max()),
        "max_reflectivity": float(cell_history[field_max_column].max()),
    }


def _growth_rate(cell_history: pd.DataFrame, window: int) -> float:
    """OLS slope of cell area (km2) vs time (min) over the last `window` scans.

    A cell seen in fewer than two scans has no measurable growth: 0.0.
    """
    return _trend(cell_history, "cell_area_sqkm", window)


def _trend(cell_history: pd.DataFrame, column: str, window: int) -> float:
    """OLS slope of `column` vs time (min) over the last `window` scans of one cell.

    Fewer than two scans, or fewer than two finite values: 0.0 (no measurable
    trend) rather than NaN, so a trend can always be gated and weighted.
    """
    rows = cell_history.tail(window)
    if len(rows) < 2:
        return 0.0
    start = from_scan_iso(rows["scan_time"].iloc[0])
    minutes = np.array(
        [(from_scan_iso(t) - start).total_seconds() / 60.0 for t in rows["scan_time"]]
    )
    y = rows[column].to_numpy(dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < 2:
        return 0.0
    return float(np.polyfit(minutes[ok], y[ok], 1)[0])


_PROJECTION_LAT = re.compile(r"^cell_centroid_projection(\d+)_lat$")


def _projection_steps(columns: Iterable[str]) -> tuple[int, ...]:
    """Forward-projection step indices present in the table, ascending.

    The analysis module writes projection{k} = k scan intervals ahead
    (index 0 is the registration centroid, stored under a different name).
    """
    cols = set(columns)
    return tuple(
        sorted(
            int(m.group(1))
            for m in map(_PROJECTION_LAT.match, cols)
            if m and f"cell_centroid_projection{m.group(1)}_lon" in cols
        )
    )


def _trajectory(row: pd.Series, scan_interval: float | None) -> tuple[TrajectoryPoint, ...]:
    """Projected centroids in ascending step order, stopping at the first null.

    Lead time of step k is k scan intervals. At the first scan of a run
    no cadence exists yet, so lead times are underivable and the
    trajectory is empty — a defined condition, not an error.
    """
    if scan_interval is None:
        return ()
    points = []
    for k in _projection_steps(row.index):
        lat = row[f"cell_centroid_projection{k}_lat"]
        lon = row[f"cell_centroid_projection{k}_lon"]
        if pd.isna(lat) or pd.isna(lon):
            break
        points.append(
            TrajectoryPoint(lat=float(lat), lon=float(lon), lead_seconds=k * scan_interval)
        )
    return tuple(points)


def _scan_interval_seconds(scan_times: list[str]) -> float | None:
    """Seconds between the two most recent distinct scan times, if any."""
    if len(scan_times) < 2:
        return None
    return (from_scan_iso(scan_times[-1]) - from_scan_iso(scan_times[-2])).total_seconds()
