# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Output contract for the cell_volume_stats module.

Validates the per-cell 3D-statistics DataFrame the module returns. An empty
frame is valid: a scan without cells has no rows.
"""

import pandas as pd

from adapt.contracts.pipeline import require

_REQUIRED_COLS = (
    "cell_label",
    "cell_area_km2",
    "cell_volume_km3",
    "dbz_max",
    "dbz_mean",
)


def assert_cell_volume_stats(df) -> None:
    require(isinstance(df, pd.DataFrame), "cell_volume_stats output must be a DataFrame")
    if df.empty:
        return  # a scan without cells has no rows
    for col in _REQUIRED_COLS:
        require(col in df.columns, f"cell_volume_stats missing column '{col}'")


def check_cell_volume_stats(df) -> None:
    assert_cell_volume_stats(df)
