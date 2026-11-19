# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the detection module.

``DetectionOptions`` is what a pipeline file sets under
``params: {detection: ...}``: the segmentation ``method``, the labelling options
every method shares, and one ``<method>_params`` block per method.
``DetectionConfig`` is exactly what RadarCellSegmenter consumes: the options,
plus what the node's ``configure`` adds for the run. Frozen. Only pydantic +
stdlib.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from adapt.modules.detection.method_params import (
    ConvStratRautParams,
    ConvStratYuterParams,
    FeatureDetectionParams,
    SteinerConvStratParams,
    ThresholdParams,
)


class DetectionOptions(BaseModel):
    """Cell segmentation options.

    The user selects a `method`; the parameters for that method come from the
    matching nested block below (Adapt-owned defaults). The shared labeling
    parameters apply to every method's convective mask.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # threshold is the default because it needs only the 2D CAPPI the pipeline
    # always produces, so an out-of-the-box run works with no further setup. The
    # pyart convective/stratiform classifiers need a 3D grid; select one via
    # `method` once that is available.
    method: Literal[
        "conv_strat_raut",
        "conv_strat_yuter",
        "feature_detection",
        "steiner_conv_strat",
        "threshold",
    ] = Field(
        "threshold",
        description="Segmentation method; its parameters are the matching <method>_params block",
    )
    # Shared labeling-backend parameters (applied to every method's mask)
    min_cellsize_gridpoint: int = Field(
        5,
        ge=1,
        description="Cells smaller than this many grid points are removed when filter_by_size",
    )
    max_cellsize_gridpoint: int | None = Field(
        None,
        ge=1,
        description="Cells larger than this many grid points are removed when filter_by_size; "
        "null means no upper limit",
    )
    closing_radius: int = Field(
        2,
        ge=0,
        description=(
            "Radius (grid points) of the disk that closes the convective mask (a dilation "
            "then an erosion); 0 disables it. Fills gaps and indentations narrower than "
            "about 2r and rounds the boundary without changing the extent of large features"
        ),
    )
    filter_by_size: bool = Field(
        True,
        description="Remove cells outside min_cellsize_gridpoint..max_cellsize_gridpoint",
    )
    h_maxima: float = Field(5.0, gt=0, description="h-maxima height for cell seeding (dBZ)")
    # Previous-frame seeding: the projection module's one-step-ahead labels
    # become extra watershed markers (union with h-maxima seeds, never a
    # replacement). On by default (one frame); off is byte-identical to no seeding.
    seed_carry_frames: int = Field(
        1,
        ge=0,
        description="How many consecutive frames a core may be carried without independent "
        "detection; 0 disables the carry. 1 carries only from the immediately preceding "
        "frame (a core not re-detected next frame is dropped); N allows N frames",
    )
    # Off by default: on KHTX 2021-05-04 the spared basins (1-4 px) sat wholly inside
    # the parent's projected hull (Opc 1.0) but covered ~4% of it, so the (v1)
    # tracker's projected-overlap gate rejected them anyway — 60 of 63 became
    # one-scan orphan tracks and 3 continued. The rescue moved from the size filter
    # to the tracking gate rather than succeeding.
    carried_exempt_size_filter: bool = Field(
        False,
        description="Spare carried-seed cells from the min_cellsize_gridpoint size filter; "
        "kept as an option for study arms",
    )
    seed_carry_min_separation: int = Field(
        5,
        ge=1,
        description="Minimum pixel distance from any h-maxima seed for a carried core "
        "to be admitted",
    )
    # Per-method scientific parameters (one block per method; defaults = algorithm defaults)
    threshold_params: ThresholdParams = Field(
        default_factory=ThresholdParams,
        description="Parameters of the threshold method",
    )
    conv_strat_raut_params: ConvStratRautParams = Field(
        default_factory=ConvStratRautParams,
        description="Parameters of the conv_strat_raut method",
    )
    conv_strat_yuter_params: ConvStratYuterParams = Field(
        default_factory=ConvStratYuterParams,
        description="Parameters of the conv_strat_yuter method",
    )
    feature_detection_params: FeatureDetectionParams = Field(
        default_factory=FeatureDetectionParams,
        description="Parameters of the feature_detection method",
    )
    steiner_conv_strat_params: SteinerConvStratParams = Field(
        default_factory=SteinerConvStratParams,
        description="Parameters of the steiner_conv_strat method",
    )


class DetectionConfig(DetectionOptions):
    """The options, and what the node's ``configure`` adds for the run."""

    # The selected method's block as a plain dict, in the block's field order:
    # the pyart methods splat it as keyword arguments, `threshold` reads
    # `method_params["threshold"]`, and it is recorded item by item in the output.
    method_params: dict[str, Any]
    reflectivity_var: str  # the field the run tracks
    z_level: float  # the run's analysis altitude (m)
