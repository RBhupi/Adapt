# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Projection stage contract.

Enforces that after optical flow computation, motion vectors and optional
projection arrays are present and structurally valid.
"""

import xarray as xr

from adapt.contracts.pipeline import require


def assert_projected(ds: xr.Dataset, max_steps: int = 5) -> None:
    """Enforce projection stage contract.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset from projector.project()
    max_steps : int, optional
        Maximum number of projection steps (default 5). If dataset has
        'max_projection_steps' in attrs, that value is used instead.

    Raises
    ------
    ContractViolation
        If any invariant is violated
    """
    require("heading_x" in ds.data_vars, "Projection contract violated: missing 'heading_x' ")
    require("heading_y" in ds.data_vars, "Projection contract violated: missing 'heading_y' ")

    if "cell_projections" in ds.data_vars:
        projections = ds["cell_projections"]
        require(
            projections.ndim == 3,
            f"Projection contract violated: 'cell_projections' has {projections.ndim} dims, "
            "expected 3 (step, y, x)",
        )
        max_steps_actual = ds.attrs.get("max_projection_steps", max_steps)
        num_steps = projections.shape[0]
        expected_steps = max_steps_actual + 1
        require(
            num_steps == expected_steps,
            f"Projection contract violated: found {num_steps} steps, expected {expected_steps} "
            f"(1 registration + {max_steps_actual} projections from config)",
        )
