# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Config schema for the cell_volume_stats module.

Holds exactly what CellVolumeStatsAlgorithm consumes. Frozen. Built at startup by
the node's build_config from the resolved InternalConfig. Only pydantic + stdlib.
"""

from pydantic import BaseModel, ConfigDict, Field


class CellVolumeStatsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Variable names. reflectivity/labels are injected from global; the polarimetric
    # names are module-owned (not a global knob), so they default here.
    reflectivity_var: str = "reflectivity"
    zdr_var: str = Field(
        "differential_reflectivity",
        description="Differential reflectivity variable for the zdr_* statistics; "
        "skipped when the grid lacks it",
    )
    kdp_var: str = Field(
        "specific_differential_phase",
        description="Specific differential phase variable for the kdp_* statistics; "
        "skipped when the grid lacks it",
    )
    rhohv_var: str = Field(
        "cross_correlation_ratio",
        description="Correlation coefficient variable for the rhohv_* statistics; "
        "skipped when the grid lacks it",
    )
    labels_var: str = "cell_labels"
    z_coord: str = "z"
    y_coord: str = "y"
    x_coord: str = "x"
    time_coord: str = "time"

    thresholds: tuple[float, ...] = Field(
        (10.0, 20.0, 30.0, 40.0, 50.0),
        description="Reflectivity thresholds (dBZ), each giving one set of "
        "cell_eth<threshold>_* echo-top, base, depth and layering columns",
    )
    gap_tolerance_m: float = Field(
        500.0,
        ge=0.0,
        description="Vertical gaps (m) no larger than this are bridged when finding "
        "echo layers in a column",
    )
    structure_threshold: float = Field(
        30.0,
        ge=0.0,
        description="Reflectivity threshold (dBZ) of the storm-structure (layering) "
        "summary columns multilayer_fraction, mean_nlayers and max_nlayers",
    )
