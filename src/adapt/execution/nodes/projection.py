# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import check_prior_scans, check_projected_ds, check_segmented_ds
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.projection.config import ProjectionConfig
from adapt.modules.projection.module import RadarCellProjector


class ProjectionModule(BaseModule):
    """BaseModule wrapper for RadarCellProjector.

    Computes optical flow between consecutive radar frames and projects
    cell positions forward in time. The frames are the prior scans' labels
    (``prior_scans``) followed by the current scan's (``segmented_ds``).

    Context inputs
    --------------
    segmented_ds : xr.Dataset
        2D segmented dataset for the current frame (output of DetectModule).
    prior_scans : list[dict]
        Previous completed scans' context dicts, oldest first, provided by the
        processor. Each dict must contain ``segmented_ds`` and ``scan_time``.
        At least one is needed (required_history=2).
    projection_config : ProjectionModuleConfig
        Runtime configuration.

    Context outputs
    ---------------
    projected_ds : xr.Dataset
        2D dataset with heading_x, heading_y, and cell_projections added.
    """

    name = "projection"
    summary = "optical-flow projection between scans"
    param_sections = ("projector",)
    required_history = 2
    pipeline_phase = 0
    inputs = ["segmented_ds", "prior_scans", "projection_config"]
    outputs = ["projected_ds"]
    input_contracts = {"segmented_ds": check_segmented_ds, "prior_scans": check_prior_scans}
    output_contracts = {"projected_ds": check_projected_ds}
    config_class = ProjectionConfig

    @classmethod
    def build_config(cls, cfg) -> ProjectionConfig:
        return ProjectionConfig(
            method=cfg.projector.method,
            nan_fill_value=cfg.projector.nan_fill_value,
            max_time_interval_minutes=cfg.global_.max_scan_gap_minutes,
            max_projection_steps=cfg.projector.max_projection_steps,
            pyr_scale=cfg.projector.flow_params.pyr_scale,
            levels=cfg.projector.flow_params.levels,
            winsize=cfg.projector.flow_params.winsize,
            iterations=cfg.projector.flow_params.iterations,
            poly_n=cfg.projector.flow_params.poly_n,
            poly_sigma=cfg.projector.flow_params.poly_sigma,
            flags=cfg.projector.flow_params.flags,
            min_motion_threshold=cfg.projector.min_motion_threshold,
            max_flow_magnitude=cfg.projector.max_flow_magnitude,
            registration_step_minutes=cfg.projector.registration_step_minutes,
            projection_horizon_minutes=cfg.projector.projection_horizon_minutes,
            reflectivity_var=cfg.global_.tracking_field,
        )

    def __init__(self) -> None:
        self._projector: RadarCellProjector | None = None

    def run(self, context: dict) -> dict:
        config = context["projection_config"]

        if self._projector is None:
            self._projector = RadarCellProjector(config)

        ds_list = [prior["segmented_ds"] for prior in context["prior_scans"]]
        ds_list.append(context["segmented_ds"])
        projected = self._projector.project(ds_list)
        return {"projected_ds": projected}


registry.register(ProjectionModule)
