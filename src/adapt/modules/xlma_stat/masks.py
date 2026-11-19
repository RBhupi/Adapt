# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The minute-resolution cell masks one scan owns, from its analysis dataset.

A scan owns the whole minutes its ``registration_minutes`` frames cover: the
previous scan's cells advected toward this scan (previous-scan label space,
resolved by the ``registration_cell_uid`` LUT). Where the scan time itself
falls on the minute grid, the scan's real segmentation (fraction 0.0, the
scan's own ``cell_uid`` LUT) replaces the advected frame of that minute. All
geometry selection lives here, so the association never decides which mask
represents a cell at a given time.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import xarray as xr

__all__ = ["MinuteMask", "scan_cell_labels", "scan_minute_masks"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MinuteMask:
    """One minute's cell mask and its grid."""

    minute_time: pd.Timestamp
    cell_labels: np.ndarray  # 2D (ny, nx) int
    x: np.ndarray  # 1D metres
    y: np.ndarray  # 1D metres
    cell_uid_lut: np.ndarray  # 1D str, indexed by cell_label
    source_scan_time: pd.Timestamp  # scan whose labels the mask carries
    target_scan_time: pd.Timestamp  # scan the mask was advected toward
    interpolation_fraction: float  # 0.0 = real segmentation, (0, 1] = advected


def scan_cell_labels(analysis_ds: xr.Dataset) -> dict[str, int]:
    """The scan's own cells: uid -> cell label. Empty for a scan without tracked
    cells (it has no ``cell_uid`` lookup)."""
    if "cell_uid" not in analysis_ds:
        return {}
    lut = analysis_ds["cell_uid"].values.astype(str)
    return {str(uid): label for label, uid in enumerate(lut) if label > 0}


def scan_minute_masks(analysis_ds: xr.Dataset, scan_time: datetime) -> list[MinuteMask]:
    """The minute masks of the scan observed at ``scan_time``, ascending.

    Advected frames without ``registration_cell_uid`` (the previous scan was
    never tracked) are left out, with a log message. A scan without cells has
    no ``cell_uid`` lookup and so no real-segmentation mask.
    """
    x, y = analysis_ds["x"].values, analysis_ds["y"].values
    by_minute: dict[pd.Timestamp, MinuteMask] = {}

    if "registration_minutes" in analysis_ds:
        if "registration_cell_uid" in analysis_ds:
            source = pd.Timestamp(analysis_ds.attrs["registration_source_scan_time"])
            target = pd.Timestamp(analysis_ds.attrs["registration_target_scan_time"])
            lut = analysis_ds["registration_cell_uid"].values.astype(str)
            frames = analysis_ds["registration_minutes"].values
            fractions = analysis_ds["interpolation_fraction"].values
            for i, minute in enumerate(pd.to_datetime(analysis_ds["minute"].values)):
                by_minute[minute] = MinuteMask(
                    minute, frames[i], x, y, lut, source, target, float(fractions[i])
                )
        else:
            logger.info(
                "Leaving out %d advected minutes: no registration_cell_uid (the "
                "previous scan was never tracked)",
                int(analysis_ds["registration_minutes"].sizes["minute"]),
            )

    # Real segmentation wins at a scan time that lands on the minute grid.
    at = pd.Timestamp(scan_time).tz_convert(None) if scan_time.tzinfo else pd.Timestamp(scan_time)
    if at == at.floor("min") and "cell_uid" in analysis_ds:
        by_minute[at] = MinuteMask(
            at,
            analysis_ds["cell_labels"].values,
            x,
            y,
            analysis_ds["cell_uid"].values.astype(str),
            at,
            at,
            0.0,
        )
    return [by_minute[minute] for minute in sorted(by_minute)]
