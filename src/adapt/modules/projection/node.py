# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import (
    SharedConfig,
    check_prior_scans,
    check_projected_ds,
    check_segmented_ds,
)
from adapt.modules.base import BaseModule
from adapt.modules.projection.config import ProjectionConfig, ProjectionOptions
from adapt.modules.projection.module import RadarCellProjector


class ProjectionModule(BaseModule):
    """BaseModule wrapper for RadarCellProjector.

    Computes optical flow between consecutive radar frames and projects
    cell positions forward in time. The frames are the latest prior scan's
    labels (the last of ``prior_scans``) followed by the current scan's
    (``segmented_ds``); earlier prior scans are not read.

    Context inputs
    --------------
    segmented_ds : xr.Dataset
        2D segmented dataset for the current frame (output of DetectModule).
    prior_scans : list[dict]
        Previous completed scans' context dicts, oldest first, provided by the
        processor. Each dict must contain ``segmented_ds`` and ``scan_time``.
        At least one is needed (required_history=2).
    projection_config : ProjectionConfig
        The module's options and what ``configure`` adds for the run.

    Context outputs
    ---------------
    projected_ds : xr.Dataset
        2D dataset with heading_x, heading_y, and cell_projections added.
    """

    name = "projection"
    summary = "optical-flow projection between scans"
    required_history = 2
    options_class = ProjectionOptions
    prior_inputs = ("segmented_ds",)
    inputs = ["segmented_ds", "prior_scans", "projection_config"]
    outputs = ["projected_ds"]
    input_contracts = {"segmented_ds": check_segmented_ds, "prior_scans": check_prior_scans}
    output_contracts = {"projected_ds": check_projected_ds}

    @classmethod
    def configure(cls, options: ProjectionOptions, shared: SharedConfig) -> ProjectionConfig:
        """The options, and the run's field and largest scan gap."""
        return ProjectionConfig(
            **options.model_dump(),
            max_time_interval_minutes=shared.max_scan_gap_minutes,
            reflectivity_var=shared.tracking_field,
        )

    @classmethod
    def method_line(cls, config: ProjectionConfig) -> str:
        return (
            f"{config.method} · horizon {config.projection_horizon_minutes}min "
            f"· steps {config.max_projection_steps}"
        )

    def __init__(self) -> None:
        self._projector: RadarCellProjector | None = None

    def run(self, context: dict) -> dict:
        config = context["projection_config"]

        if self._projector is None:
            self._projector = RadarCellProjector(config)

        # The projector takes two frames: the latest earlier scan and this one.
        ds_list = [prior["segmented_ds"] for prior in context["prior_scans"][-1:]]
        ds_list.append(context["segmented_ds"])
        projected = self._projector.project(ds_list)
        return {"projected_ds": projected}
