# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the prior_scans contract.

``prior_scans`` is the list of previous completed scan contexts, oldest first,
that the processor provides to every per-scan module; it never holds the
current scan and is empty on the first scan or after a too-large time gap.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

pytestmark = pytest.mark.unit

from adapt.contracts import ContractViolation, check_prior_scans  # noqa: E402


def _valid_scan_ctx() -> dict:
    """Minimal valid scan context dict as stored in history."""
    ds = xr.Dataset(
        {"cell_labels": (("y", "x"), np.zeros((4, 4), dtype=np.int32))},
        coords={"x": range(4), "y": range(4)},
    )
    return {"segmented_ds": ds, "scan_time": datetime(2024, 1, 1, tzinfo=UTC)}


class TestPriorScansContract:
    """check_prior_scans validates the list of previous scan contexts."""

    def test_passes_with_no_prior_scan(self):
        """The first scan (or one after a too-large gap) has no usable prior."""
        check_prior_scans([])

    def test_passes_with_single_valid_entry(self):
        check_prior_scans([_valid_scan_ctx()])

    def test_passes_with_two_valid_entries(self):
        check_prior_scans([_valid_scan_ctx(), _valid_scan_ctx()])

    def test_fails_when_not_a_list(self):
        with pytest.raises(ContractViolation, match="list"):
            check_prior_scans("not a list")

    def test_fails_when_entry_is_not_dict(self):
        with pytest.raises(ContractViolation, match=r"prior_scans\[0\].*dict"):
            check_prior_scans(["not a dict"])

    def test_an_entry_holds_only_what_modules_read_back(self):
        """Which outputs an entry carries is the readers' business, not the contract's."""
        ctx = _valid_scan_ctx()
        del ctx["segmented_ds"]
        check_prior_scans([ctx])

    def test_fails_when_scan_time_missing(self):
        ctx = _valid_scan_ctx()
        del ctx["scan_time"]
        with pytest.raises(ContractViolation, match="scan_time"):
            check_prior_scans([ctx])

    def test_fails_on_second_entry_if_invalid(self):
        """Contract validates every entry, not just the first."""
        bad_ctx = _valid_scan_ctx()
        del bad_ctx["scan_time"]
        with pytest.raises(ContractViolation, match=r"prior_scans\[1\]"):
            check_prior_scans([_valid_scan_ctx(), bad_ctx])
