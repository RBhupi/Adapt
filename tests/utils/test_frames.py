# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Frozen dataclass records as typed table frames: a column's type comes from
its field, never from the values a call happens to carry."""

import math
from dataclasses import dataclass
from typing import Optional

import pytest

from adapt.utils.frames import (
    INT,
    REAL,
    TEXT,
    record_columns,
    record_frame,
    record_integer_columns,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Record:
    count: int
    flag: bool
    value: float
    name: str
    maybe_count: int | None
    maybe_flag: Optional[bool]  # noqa: UP045 - both spellings of "optional" are accepted
    maybe_value: float | None
    maybe_name: str | None


@dataclass(frozen=True)
class _Other:
    count: int


@dataclass(frozen=True)
class _Unsupported:
    when: bytes


def _record(**overrides) -> _Record:
    values = {
        "count": 3,
        "flag": True,
        "value": 1.5,
        "name": "a",
        "maybe_count": None,
        "maybe_flag": None,
        "maybe_value": None,
        "maybe_name": None,
    }
    return _Record(**{**values, **overrides})


def test_a_record_field_becomes_a_column_of_its_type():
    assert list(record_columns(_Record)) == [
        ("count", INT),
        ("flag", INT),
        ("value", REAL),
        ("name", TEXT),
        ("maybe_count", REAL),
        ("maybe_flag", REAL),
        ("maybe_value", REAL),
        ("maybe_name", TEXT),
    ]


def test_the_integer_columns_are_the_int_and_bool_fields_optional_or_not():
    assert record_integer_columns(_Record) == ("count", "flag", "maybe_count", "maybe_flag")


def test_a_column_missing_in_every_record_keeps_its_type():
    frame = record_frame([_record(), _record(count=4, flag=False)])

    assert frame["maybe_value"].dtype == float
    assert frame["maybe_count"].dtype == float
    assert frame["maybe_flag"].dtype == float
    assert frame["maybe_name"].dtype == object
    assert frame["maybe_name"].tolist() == [None, None]
    assert frame["count"].tolist() == [3, 4]
    assert frame["flag"].tolist() == [1, 0]


def test_the_frame_holds_the_record_values_in_order():
    frame = record_frame(
        [_record(maybe_count=7, maybe_flag=False, maybe_value=2.5, maybe_name="z")]
    )

    assert frame.to_dict("records") == [
        {
            "count": 3,
            "flag": 1,
            "value": 1.5,
            "name": "a",
            "maybe_count": 7.0,
            "maybe_flag": 0.0,
            "maybe_value": 2.5,
            "maybe_name": "z",
        }
    ]


@pytest.mark.parametrize(
    ("field", "missing"),
    [("count", None), ("name", None), ("value", None), ("value", math.nan)],
)
def test_a_missing_value_in_a_field_that_is_not_optional_is_refused(field, missing):
    with pytest.raises(ValueError, match=rf"_Record.*{field}"):
        record_frame([_record(), _record(**{field: missing})])


def test_a_field_of_another_type_is_refused():
    with pytest.raises(TypeError, match=r"_Unsupported.*when"):
        record_columns(_Unsupported)


def test_records_of_two_types_are_refused():
    with pytest.raises(ValueError, match=r"_Record.*_Other|_Other.*_Record"):
        record_frame([_record(), _Other(count=1)])


def test_no_record_is_refused():
    with pytest.raises(ValueError, match="no record"):
        record_frame([])
