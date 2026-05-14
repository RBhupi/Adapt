"""Time normalization helpers shared across ADAPT modules."""

import contextlib
from datetime import UTC, datetime

import numpy as np


def normalize_time_scalar(time_val):
    """Normalize xarray/cftime/numpy time representations to a scalar."""
    tv = time_val
    while isinstance(tv, np.ndarray) and tv.size == 1:
        tv = tv.reshape(-1)[0]
    if isinstance(tv, np.ndarray):
        tv = tv.reshape(-1)[0]

    if hasattr(tv, "item"):
        with contextlib.suppress(TypeError, ValueError):
            tv = tv.item()

    if getattr(type(tv), "__module__", "").startswith("cftime"):
        tv = datetime(
            int(tv.year),
            int(tv.month),
            int(tv.day),
            int(tv.hour),
            int(tv.minute),
            int(tv.second),
            int(getattr(tv, "microsecond", 0) or 0),
            tzinfo=UTC,
        )

    return tv
