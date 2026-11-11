# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The ``selection`` contract: which cell the radar should watch after one scan.

Any module that decides the target hands on this frame: exactly one row for
the scan, naming the target's ``cell_uid`` (missing when no cell qualifies) and
the ``config_hash`` of the rules that decided. Anything else in the row is the
deciding module's own record.
"""

import pandas as pd

from adapt.contracts.pipeline import require

_REQUIRED_COLS = ("config_hash", "cell_uid")


def check_selection(df: pd.DataFrame) -> None:
    """Bound contract for the ``selection`` frame."""
    require(
        isinstance(df, pd.DataFrame),
        f"Selection contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_COLS:
        require(col in df.columns, f"Selection contract violated: missing column '{col}'")
    require(len(df) == 1, f"Selection contract violated: {len(df)} rows, expected exactly one")
    require(
        isinstance(df["config_hash"].iloc[0], str) and df["config_hash"].iloc[0] != "",
        "Selection contract violated: config_hash must name the rules that decided",
    )
