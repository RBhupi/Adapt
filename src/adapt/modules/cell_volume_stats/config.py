# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the cell_volume_stats module.

``CellVolumeStatsOptions`` is what a pipeline file sets under
``params: {cell_volume_stats: ...}``. ``CellVolumeStatsConfig`` is exactly what
CellVolumeStatsAlgorithm consumes: the options, plus what the node's
``configure`` takes from the run. Frozen. Only pydantic + stdlib.
"""

from pydantic import BaseModel, ConfigDict, Field


class CellVolumeStatsOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # The polarimetric variable names are the module's own (not a value the
    # run's modules share), so they default here.
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


class CellVolumeStatsConfig(CellVolumeStatsOptions):
    """The options, and the field the statistics are computed on."""

    reflectivity_var: str  # the field the run tracks
