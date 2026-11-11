# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Prior-scans contract.

``prior_scans`` is the one history key the processor provides to per-scan
modules: the previous scans, oldest first, each a dict of that scan's time and
identity and the outputs modules declared they read back (``prior_inputs``). It
never holds the current scan, and is empty on the first scan or when the time
gap to the last scan is too large.
"""

from adapt.contracts.pipeline import require


def check_prior_scans(prior: list) -> None:
    """Validate the prior_scans context key.

    Raises
    ------
    ContractViolation
        If ``prior`` is not a list, or an entry is not a dict with its ``scan_time``.
    """
    require(isinstance(prior, list), "prior_scans must be a list")
    for i, ctx in enumerate(prior):
        require(isinstance(ctx, dict), f"prior_scans[{i}] must be a dict")
        require("scan_time" in ctx, f"prior_scans[{i}] missing 'scan_time'")
