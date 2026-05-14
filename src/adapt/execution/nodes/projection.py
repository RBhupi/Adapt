# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import check_projected_ds, check_segmented_ds
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.projection.module import RadarCellProjector


class ProjectionModule(BaseModule):
    """BaseModule wrapper for RadarCellProjector.

    Computes optical flow between consecutive radar frames and projects
    cell positions forward in time. Stateless: receives the frame pair
    via the context key ``dataset_history`` (injected by the processor).

    Context inputs
    --------------
    segmented_ds : xr.Dataset
        2D segmented dataset for the current frame (output of DetectModule).
    dataset_history : list of (str, xr.Dataset)
        Rolling history of (filepath, segmented_ds) tuples supplied by the
        processor. Must contain exactly 2 entries before this module is called.
    config : InternalConfig
        Runtime configuration.

    Context outputs
    ---------------
    projected_ds : xr.Dataset
        2D dataset with heading_x, heading_y, and cell_projections added.
    """

    name = "projection"
    inputs = ["segmented_ds", "dataset_history", "projection_config"]
    outputs = ["projected_ds"]
    input_contracts = {"segmented_ds": check_segmented_ds}
    output_contracts = {"projected_ds": check_projected_ds}

    def __init__(self) -> None:
        self._projector = None

    def run(self, context: dict) -> dict:
        config = context["projection_config"]
        dataset_history = context["dataset_history"]  # list of (filepath, ds_2d)

        if self._projector is None:
            self._projector = RadarCellProjector(config)

        if len(dataset_history) < 2:
            raise ValueError(
                f"ProjectionModule requires 2 frames in dataset_history, "
                f"got {len(dataset_history)}. Processor must pair frames before calling."
            )

        ds_list = [ds for _, ds in dataset_history]
        projected = self._projector.project(ds_list)
        return {"projected_ds": projected}


registry.register(ProjectionModule)
