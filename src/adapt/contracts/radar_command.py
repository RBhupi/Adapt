# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The ``radar_commands`` contract: what was asked of the radar after one scan.

Exactly one row for the scan: the ``outcome`` (a command was published, sent,
or why there is none) and the ``config_hash`` of the radar command config.
"""

import pandas as pd

from adapt.contracts.pipeline import require

_REQUIRED_COLS = ("config_hash", "outcome", "cell_uid", "plan_id", "command_json")


def check_radar_commands(df: pd.DataFrame) -> None:
    """Bound contract for the ``radar_commands`` frame."""
    require(
        isinstance(df, pd.DataFrame),
        f"Radar commands contract violated: output is {type(df)}, expected DataFrame",
    )
    for col in _REQUIRED_COLS:
        require(col in df.columns, f"Radar commands contract violated: missing column '{col}'")
    require(len(df) == 1, f"Radar commands contract violated: {len(df)} rows, expected one")
    require(
        isinstance(df["outcome"].iloc[0], str) and df["outcome"].iloc[0] != "",
        "Radar commands contract violated: the row must say what happened (outcome)",
    )
