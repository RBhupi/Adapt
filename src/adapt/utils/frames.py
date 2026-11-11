# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Table rows as a DataFrame whose columns and dtypes are fixed in advance."""

from collections.abc import Sequence

import pandas as pd

__all__ = ["INT", "REAL", "TEXT", "integer_columns", "typed_frame"]

TEXT, REAL, INT = "text", "real", "int"

Columns = Sequence[tuple[str, str]]  # (name, TEXT | REAL | INT), in table order


def typed_frame(rows: list[dict], columns: Columns) -> pd.DataFrame:
    """``rows`` as a frame with exactly ``columns``, each of its declared kind.

    The stored schema then never depends on which values a scan happens to
    carry: an all-missing REAL column is still float, a TEXT one still object.
    INT columns may not hold a missing value; use REAL for a count that can be
    absent.
    """
    df = pd.DataFrame(rows, columns=[name for name, _ in columns])
    for name, kind in columns:
        if kind == REAL:
            df[name] = df[name].astype(float)
        elif kind == INT:
            df[name] = df[name].astype(int)
        else:
            df[name] = df[name].astype(object).where(df[name].notna(), None)
    return df


def integer_columns(columns: Columns) -> tuple[str, ...]:
    """Names of the INT columns."""
    return tuple(name for name, kind in columns if kind == INT)
