# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Table rows as a DataFrame whose columns and dtypes are fixed in advance."""

import math
import types
import typing
from collections.abc import Sequence
from dataclasses import asdict, fields
from typing import Any

import pandas as pd

__all__ = [
    "INT",
    "REAL",
    "TEXT",
    "integer_columns",
    "record_columns",
    "record_frame",
    "record_integer_columns",
    "typed_frame",
]

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


# A record field's type -> its kind in the frame: (required, optional). An
# optional int or bool is REAL, since an INT frame column holds no missing
# value; the table still stores it as an integer (``record_integer_columns``).
_FIELD_KINDS: dict[type, tuple[str, str]] = {
    str: (TEXT, TEXT),
    float: (REAL, REAL),
    int: (INT, REAL),
    bool: (INT, REAL),
}


def _field_types(record_type: type) -> list[tuple[str, type, bool]]:
    """(name, base type, optional) for each field of a frozen dataclass record."""
    hints = typing.get_type_hints(record_type)
    out = []
    for f in fields(record_type):
        hint = hints[f.name]
        args = typing.get_args(hint)
        optional = typing.get_origin(hint) in (typing.Union, types.UnionType) and (
            type(None) in args
        )
        base = next((a for a in args if a is not type(None)), None) if optional else hint
        if (optional and len(args) != 2) or base not in _FIELD_KINDS:
            raise TypeError(
                f"{record_type.__name__}.{f.name}: field type {hint} has no table column type "
                "(str, float, int or bool, optional or not)"
            )
        out.append((f.name, base, optional))
    return out


def record_columns(record_type: type) -> Columns:
    """The columns of a frozen dataclass record, by field type."""
    return [
        (name, _FIELD_KINDS[base][1] if optional else _FIELD_KINDS[base][0])
        for name, base, optional in _field_types(record_type)
    ]


def record_integer_columns(record_type: type) -> tuple[str, ...]:
    """The record's int and bool fields, optional or not."""
    return tuple(name for name, base, _ in _field_types(record_type) if base in (int, bool))


def record_frame(records: Sequence[Any]) -> pd.DataFrame:
    """Records of one type as a frame with the declared dtypes.

    Raises ValueError for no record, records of two types, or a missing value
    (None, or NaN in a float) in a field that is not optional.
    """
    if not records:
        raise ValueError("record_frame: no record to make a frame of")
    record_type = type(records[0])
    others = sorted({type(r).__name__ for r in records} - {record_type.__name__})
    if others:
        raise ValueError(
            f"record_frame: records of more than one type ({record_type.__name__}, "
            f"{', '.join(others)})"
        )
    rows = [asdict(r) for r in records]
    for name, _, optional in _field_types(record_type):
        if optional:
            continue
        for row in rows:
            value = row[name]
            if value is None or (isinstance(value, float) and math.isnan(value)):
                raise ValueError(
                    f"{record_type.__name__}.{name}: missing value in a field that is not optional"
                )
    return typed_frame(rows, record_columns(record_type))
