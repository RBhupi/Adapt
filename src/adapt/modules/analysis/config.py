# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the analysis module.

``AnalysisOptions`` is what a pipeline file sets under
``params: {analysis: ...}``. ``AnalysisConfig`` is exactly what
RadarCellAnalyzer consumes: the options, plus what the node's ``configure``
adds for the run. Frozen. Only pydantic + stdlib.
"""

from pydantic import BaseModel, ConfigDict, Field

# The variables analysis never computes statistics for, whatever the options
# say: the pipeline's own label, projection and bookkeeping variables.
ALWAYS_EXCLUDED = (
    "ROI",
    "labels",
    "cell_labels",
    "cell_projections",
    "clutter_filter_power_removed",
)


class AnalysisOptions(BaseModel):
    """Cell analysis options."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    radar_variables: tuple[str, ...] = Field(
        (
            "reflectivity",
            "velocity",
            "differential_phase",
            "differential_reflectivity",
            "spectrum_width",
            "cross_correlation_ratio",
        ),
        description="Variables that get per-cell statistics, when present in the scan",
    )
    exclude_fields: tuple[str, ...] = Field(
        (),
        description="Variables never analysed, even when in radar_variables; entries you give "
        f"are added to the ones analysis always leaves out ({', '.join(ALWAYS_EXCLUDED)})",
    )
    adjacency_min_touching_boundary_pixels: int = Field(
        1,
        ge=1,
        description=(
            "Min number of touching boundary pixels to count two labels "
            "as adjacent in the same scan"
        ),
    )


class AnalysisConfig(AnalysisOptions):
    """The options, and what the node's ``configure`` adds for the run.

    ``exclude_fields`` holds every variable left out: ``ALWAYS_EXCLUDED``, then
    the option's entries.
    """

    reflectivity_var: str  # the field the run tracks
    z_level: float  # the run's analysis altitude (m)
