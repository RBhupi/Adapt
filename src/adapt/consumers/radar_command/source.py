# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The selected cell's projected footprints and vertical extent, read via adapt.api.

The only radar command module that touches the store.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from adapt.api import StoreClient
from adapt.consumers.radar_command.config import AltitudeBand
from adapt.consumers.radar_command.extent import VerticalBounds
from adapt.consumers.radar_command.selection import SelectionRef


class TargetUnavailable(Exception):
    """The selected target cannot be located in the store."""


@dataclass(frozen=True)
class Footprint:
    """The cell's projected pixels at one UTC minute; empty arrays when it is absent."""

    time: datetime
    lats: np.ndarray
    lons: np.ndarray


@dataclass(frozen=True)
class TargetGeometry:
    """One footprint per projection minute (ascending) plus the cell's vertical extent."""

    footprints: tuple[Footprint, ...]
    pixel_radius_km: float
    vertical: VerticalBounds


_REQUIRED = ("cell_uid", "projection_minutes", "lat", "lon", "origin_altitude")


def load_target(
    client: StoreClient, ref: SelectionRef, vertical_band: AltitudeBand | None
) -> TargetGeometry:
    """Locate ``ref.cell_uid`` in the projection frames of scan ``ref.scan_id``."""
    with client.open_scan_raster(ref.collection, ref.run_id, ref.scan_id) as raster:
        ds = raster.dataset
        missing = [name for name in _REQUIRED if name not in ds.variables]
        if missing:
            raise TargetUnavailable(
                f"Scan '{ref.scan_id}' of run '{ref.run_id}' lacks {missing} in its "
                "segmentation2d product; it cannot locate a projected target"
            )
        lut = ds["cell_uid"].values
        label = _label(lut, ref)
        lats, lons = ds["lat"].values, ds["lon"].values
        frames = ds["projection_minutes"]
        footprints = tuple(
            Footprint(
                time=_utc(minute),
                lats=lats[frame == label],
                lons=lons[frame == label],
            )
            for minute, frame in zip(frames["projection_minute"].values, frames.values, strict=True)
        )
        x, y = ds["x"].values, ds["y"].values
        pixel_radius_km = math.hypot(x[1] - x[0], y[1] - y[0]) / 2 / 1000
        origin_altitude_m = float(ds["origin_altitude"].item())
    vertical = _vertical(client, ref, origin_altitude_m, vertical_band)
    return TargetGeometry(footprints, pixel_radius_km, vertical)


def _label(lut: np.ndarray, ref: SelectionRef) -> int:
    """The label whose cell_uid lookup entry is the selected uid (exactly one)."""
    matches = np.flatnonzero(lut == ref.cell_uid)
    if len(matches) != 1:
        raise TargetUnavailable(
            f"Cell '{ref.cell_uid}' has {len(matches)} cell_uid entries in scan "
            f"'{ref.scan_id}' of run '{ref.run_id}', expected exactly one"
        )
    return int(matches[0])


def _vertical(
    client: StoreClient, ref: SelectionRef, origin_altitude_m: float, band: AltitudeBand | None
) -> VerticalBounds:
    """Cell base/top (metres above the grid origin) lifted to AMSL; the configured
    band when this run wrote no cell_volume_stats rows.

    The source is decided per run: other runs of the collection may have
    written the table. A run that wrote rows must have this scan's row; one
    not written yet (phase 3 still running) is unavailable for now.

    cell_base_m/cell_top_m are the centres of the lowest and highest levels
    with echo, so the half level beyond each is not covered: neither the row
    nor the 2D raster carries the level spacing needed to pad it.
    """
    run_rows = _run_volume_stats(client, ref)
    if run_rows.empty:
        if band is None:
            raise TargetUnavailable(
                f"Run '{ref.run_id}' of collection '{ref.collection}' wrote no "
                "cell_volume_stats rows and radar_command.extent.vertical_band is not "
                f"configured; no vertical extent for cell '{ref.cell_uid}' at scan "
                f"'{ref.scan_id}'"
            )
        return VerticalBounds(band.altitude_min_m, band.altitude_max_m, source="config")
    rows = run_rows[(run_rows["scan_id"] == ref.scan_id) & (run_rows["cell_uid"] == ref.cell_uid)]
    # SQL NULL (a cell with no echo aloft) reads back as None; as float it is NaN.
    base_top = rows[["cell_base_m", "cell_top_m"]].to_numpy(dtype=float)
    if len(base_top) != 1 or not np.isfinite(base_top).all():
        raise TargetUnavailable(
            f"Cell '{ref.cell_uid}' at scan '{ref.scan_id}' of run '{ref.run_id}' needs exactly "
            f"one cell_volume_stats row with finite cell_base_m/cell_top_m, got {base_top.tolist()}"
        )
    base_m, top_m = base_top[0]
    return VerticalBounds(
        bottom_m=origin_altitude_m + float(base_m),
        top_m=origin_altitude_m + float(top_m),
        source="cell_volume_stats",
    )


def _run_volume_stats(client: StoreClient, ref: SelectionRef) -> pd.DataFrame:
    """Every cell_volume_stats row of ``ref.run_id``; none when no run declared the table."""
    if "cell_volume_stats" not in set(client.tables(ref.collection)["table_name"]):
        return pd.DataFrame()
    return client.table("cell_volume_stats", ref.collection, run_id=ref.run_id)


def _utc(minute: np.datetime64) -> datetime:
    return minute.astype("datetime64[us]").astype(datetime).replace(tzinfo=UTC)
