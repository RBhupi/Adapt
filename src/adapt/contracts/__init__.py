# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Central contract definitions for the ADAPT pipeline.

All pipeline stage validators and the ContractViolation exception live here.
Import from this package — never from individual contract submodules.
"""

from adapt.contracts.analysis import assert_analysis_output, assert_cell_adjacency
from adapt.contracts.grid import assert_gridded
from adapt.contracts.pipeline import ContractViolation, require
from adapt.contracts.projection import assert_projected
from adapt.contracts.segmentation import assert_segmented
from adapt.contracts.tracking import assert_cell_events, assert_tracked_cells

__all__ = [
    "ContractViolation",
    "require",
    "assert_gridded",
    "assert_segmented",
    "assert_projected",
    "assert_analysis_output",
    "assert_cell_adjacency",
    "assert_tracked_cells",
    "assert_cell_events",
]
