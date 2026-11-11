# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tracking stage contracts.

Enforces structural requirements on the tracking outputs: the scan-local
``tracked_cells`` and the three stored tables (``cell_identity``,
``cell_events``, ``cell_tracks``). Any tracker that meets them can replace the
built-in one.
"""

from __future__ import annotations

import pandas as pd

from adapt.contracts.pipeline import require

_REQUIRED_TRACKED_COLS = [
    "time",
    "cell_label",
    "cell_uid",
    "area",
    "centroid_x",
    "centroid_y",
    "mean_reflectivity",
    "max_reflectivity",
    "core_area",
]

_REQUIRED_IDENTITY_COLS = ["cell_label", "cell_uid", "age_seconds"]

_REQUIRED_TRACKS_COLS = [
    "cell_uid",
    "first_seen_time",
    "last_seen_time",
    "n_scans",
    "origin_type",
    "termination_type",
]

_REQUIRED_EVENTS_COLS = [
    "event_seq",
    "source_scan_id",
    "target_scan_id",
    "event_type",
    "source_cell_uid",
    "target_cell_uid",
    "source_cell_label",
    "target_cell_label",
    "cost",
    "is_dominant",
    "event_group_id",
]

# LATENT: a track that vanished is kept for possible resumption; RESUMED: it was.
_VALID_EVENT_TYPES = {
    "CONTINUE",
    "SPLIT",
    "MERGE",
    "INITIATION",
    "TERMINATION",
    "LATENT",
    "RESUMED",
}


def assert_tracked_cells(df: pd.DataFrame) -> None:
    """Enforce tracked cells contract.

    Raises
    ------
    ContractViolation
        If structural requirements are violated
    """
    require(
        isinstance(df, pd.DataFrame),
        f"Tracked cells contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_TRACKED_COLS:
        require(
            col in df.columns,
            f"Tracked cells contract violated: missing required column '{col}'",
        )
    if len(df) == 0:
        return
    require(
        (df["cell_label"] > 0).all(),
        "Tracked cells contract violated: cell_label must be > 0 for all rows",
    )
    require(
        "cell_uid" in df.columns and df["cell_uid"].notna().all(),
        "Tracked cells contract violated: cell_uid must be non-null for all rows",
    )


def assert_cell_events(df: pd.DataFrame) -> None:
    """Enforce cell events contract.

    Raises
    ------
    ContractViolation
        If structural requirements are violated
    """
    require(
        isinstance(df, pd.DataFrame),
        f"Cell events contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_EVENTS_COLS:
        require(
            col in df.columns,
            f"Cell events contract violated: missing required column '{col}'",
        )
    if len(df) == 0:
        return
    require(
        df["event_type"].isin(_VALID_EVENT_TYPES).all(),
        f"Cell events contract violated: invalid event_type present "
        f"(valid={sorted(_VALID_EVENT_TYPES)})",
    )


def check_tracked_cells(df: pd.DataFrame) -> None:
    """Bound contract for tracked cells DataFrame (skips validation on empty frame)."""
    if not df.empty:
        assert_tracked_cells(df)


def check_cell_events(df: pd.DataFrame) -> None:
    """Bound contract for cell events DataFrame (skips validation on empty frame)."""
    if not df.empty:
        assert_cell_events(df)


def check_cell_identity(df: pd.DataFrame) -> None:
    """Bound contract for one scan's cell identity: label -> uid, each used once."""
    require(
        isinstance(df, pd.DataFrame),
        f"Cell identity contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_IDENTITY_COLS:
        require(
            col in df.columns, f"Cell identity contract violated: missing required column '{col}'"
        )
    if df.empty:
        return
    require(
        (df["cell_label"] > 0).all(),
        "Cell identity contract violated: cell_label must be > 0 for all rows",
    )
    require(
        df["cell_uid"].notna().all(),
        "Cell identity contract violated: cell_uid must be non-null for all rows",
    )
    require(
        not df["cell_label"].duplicated().any() and not df["cell_uid"].duplicated().any(),
        "Cell identity contract violated: a cell_label or cell_uid repeats within the scan",
    )


def check_cell_tracks(df: pd.DataFrame) -> None:
    """Bound contract for the track lifecycle rows a scan touched."""
    require(
        isinstance(df, pd.DataFrame),
        f"Cell tracks contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_TRACKS_COLS:
        require(
            col in df.columns, f"Cell tracks contract violated: missing required column '{col}'"
        )
    require(
        not df["cell_uid"].duplicated().any(),
        "Cell tracks contract violated: a cell_uid repeats within the scan",
    )
