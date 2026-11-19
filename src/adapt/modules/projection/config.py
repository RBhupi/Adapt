# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the projection module.

``ProjectionOptions`` is what a pipeline file sets under
``params: {projection: ...}``; its ``flow_params`` part holds the optical-flow
parameters. ``ProjectionConfig`` is exactly what RadarCellProjector consumes:
the options, plus what the node's ``configure`` adds for the run. Frozen. Only
pydantic + stdlib.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Each default is given by keyword (``default=``): the form a type checker reads
# as a default, so ``default_factory=FlowParams`` in ProjectionOptions type-checks.
# Every parameter has one, so a pipeline file may give one parameter alone.
class FlowParams(BaseModel):
    """OpenCV optical flow parameters.

    The field names and their order are the keyword arguments of
    ``cv2.calcOpticalFlowFarneback``: the projector hands it ``model_dump()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    pyr_scale: float = Field(
        default=0.5, gt=0, le=1.0, description="Image scale between pyramid levels; 0.5 halves it"
    )
    levels: int = Field(default=3, ge=1, description="Pyramid levels, the full-size image included")
    winsize: int = Field(default=10, ge=1, description="Averaging window size (pixels)")
    iterations: int = Field(default=3, ge=1, description="Iterations at each pyramid level")
    poly_n: int = Field(
        default=7,
        ge=5,
        description="Pixel neighbourhood size of the per-pixel polynomial expansion",
    )
    poly_sigma: float = Field(
        default=1.5,
        gt=0,
        description="Gaussian standard deviation smoothing the polynomial expansion",
    )
    flags: int = Field(default=0, description="OpenCV calcOpticalFlowFarneback flags; 0 sets none")


class ProjectionOptions(BaseModel):
    """Cell projection options."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    method: Literal["adapt_default"] = Field(
        "adapt_default",
        description="Projection method; only adapt_default (Farneback optical flow) exists",
    )
    max_projection_steps: int = Field(
        3,
        ge=1,
        le=10,
        description="Scan-step projections computed beyond the registration frame",
    )
    nan_fill_value: float = Field(
        0.0, description="Value that replaces NaN in the tracking field before optical flow"
    )
    flow_params: FlowParams = Field(
        default_factory=FlowParams,
        description="OpenCV Farneback optical-flow parameters",
    )
    min_motion_threshold: float = Field(
        1.0,
        ge=0,
        description="A median flow below this (pixels/frame) logs a warning that the field "
        "may be static or noise",
    )
    max_flow_magnitude: float = Field(
        20.0,
        gt=0,
        description="Clip flow vectors exceeding this magnitude (pixels/frame)",
    )
    registration_step_minutes: int = Field(
        1,
        ge=1,
        description="Step of the minute-resolution registration masks between scans",
    )
    projection_horizon_minutes: int = Field(
        15,
        ge=1,
        le=60,
        description="Forward horizon (minutes) for the minute-resolution projection product",
    )


class ProjectionConfig(ProjectionOptions):
    """The options, and what the node's ``configure`` adds for the run."""

    max_time_interval_minutes: float  # the run's largest scan gap projected across
    reflectivity_var: str  # the field the run tracks
