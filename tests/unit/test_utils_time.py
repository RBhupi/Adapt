# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Unit tests for adapt.utils.time.normalize_time_scalar.

All tests use synthetic numpy/Python scalars — no radar files, no IO.
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from adapt.utils.time import normalize_time_scalar

pytestmark = pytest.mark.unit


class TestNormalizeTimeScalar:
    def test_python_datetime_passthrough(self):
        dt = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = normalize_time_scalar(dt)
        assert result == dt

    def test_numpy_datetime64_unwrapped_to_python(self):
        val = np.datetime64("2025-06-15T08:00:00")
        result = normalize_time_scalar(val)
        # numpy datetime64.item() returns a Python datetime
        assert isinstance(result, datetime)

    def test_single_element_array_unwrapped(self):
        arr = np.array(["2025-03-01T06:00:00"], dtype="datetime64[s]")
        result = normalize_time_scalar(arr)
        assert not isinstance(result, np.ndarray)

    def test_size_1_array_with_ndim_gt_1(self):
        arr = np.array([["2025-01-01T00:00:00"]], dtype="datetime64[s]")
        result = normalize_time_scalar(arr)
        assert not isinstance(result, np.ndarray)

    def test_nat_returns_none(self):
        """NaT.item() returns None; normalize_time_scalar propagates that."""
        result = normalize_time_scalar(np.datetime64("NaT"))
        assert result is None

    def test_plain_integer_passthrough(self):
        result = normalize_time_scalar(42)
        assert result == 42

    def test_cftime_converted_to_datetime(self):
        """cftime objects are converted to Python datetime with UTC timezone."""
        pytest.importorskip("cftime")
        import cftime
        cf = cftime.DatetimeGregorian(2025, 6, 15, 10, 30, 0)
        result = normalize_time_scalar(cf)
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_numpy_scalar_without_item_method(self):
        """Objects without .item() are returned as-is."""
        class FakeScalar:
            pass
        obj = FakeScalar()
        result = normalize_time_scalar(obj)
        assert result is obj
