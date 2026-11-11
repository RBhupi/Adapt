# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Prior-scans contract.

``prior_scans`` is the one history key the processor provides to per-scan
modules: the previous completed scans' context dicts, oldest first. It never
holds the current scan, and is empty on the first scan or when the time gap to
the last scan is too large.
"""

from adapt.contracts.pipeline import require


def check_prior_scans(prior: list) -> None:
    """Validate the prior_scans context key.

    Each entry must be a dict containing at minimum ``segmented_ds``
    (output of the detection module) and ``scan_time``.

    Raises
    ------
    ContractViolation
        If ``prior`` is not a list, or any entry is malformed.
    """
    require(isinstance(prior, list), "prior_scans must be a list")
    for i, ctx in enumerate(prior):
        require(isinstance(ctx, dict), f"prior_scans[{i}] must be a dict")
        require("segmented_ds" in ctx, f"prior_scans[{i}] missing 'segmented_ds'")
        require("scan_time" in ctx, f"prior_scans[{i}] missing 'scan_time'")
