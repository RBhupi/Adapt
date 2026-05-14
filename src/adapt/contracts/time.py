# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Time coordinate contract.

Enforces that datasets crossing module boundaries carry a normalized time
coordinate — numpy datetime64, not cftime — so no module needs its own
conversion logic. Modules that read raw radar data (e.g. ingest) are
responsible for calling adapt.utils.time.normalize_time_scalar before
returning a dataset to the context.
"""

import numpy as np
import xarray as xr

from adapt.contracts.pipeline import require


def assert_time_normalized(ds: xr.Dataset) -> None:
    """Enforce that the dataset time coordinate is numpy-compatible, not cftime.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset crossing a module boundary.

    Raises
    ------
    ContractViolation
        If the dataset has no time coordinate, or if the time coordinate
        uses cftime objects instead of numpy datetime64.
    """
    require(
        "time" in ds.coords or hasattr(ds, "attrs") and "time" in ds.attrs,
        "Time contract violated: dataset has no 'time' coordinate or attribute. "
        "Every dataset crossing a module boundary must carry a time stamp.",
    )

    if "time" in ds.coords:
        raw = ds.coords["time"].values
        tv = raw.flat[0] if isinstance(raw, np.ndarray) and raw.ndim > 0 else raw
        # unwrap numpy scalar wrapper if needed
        if hasattr(tv, "item"):
            try:
                tv = tv.item()
            except Exception:
                pass
        module = getattr(type(tv), "__module__", "")
        require(
            not module.startswith("cftime"),
            f"Time contract violated: time coordinate is cftime ({type(tv).__name__}). "
            "Normalize with adapt.utils.time.normalize_time_scalar before returning "
            "the dataset from the ingest or any adapter layer.",
        )


def check_time_normalized(ds: xr.Dataset) -> None:
    """Bound contract for time normalization — use in module input_contracts."""
    assert_time_normalized(ds)
