# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import check_grid_ds_2d, check_segmented_ds
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.detection.module import RadarCellSegmenter


class DetectModule(BaseModule):
    """BaseModule wrapper for RadarCellSegmenter.

    Segments convective cells from a 2D reflectivity field using
    threshold and morphological filtering.

    Context inputs
    --------------
    grid_ds_2d : xr.Dataset
        2D Cartesian dataset (output of LoadModule).
    config : InternalConfig
        Runtime configuration.

    Context outputs
    ---------------
    segmented_ds : xr.Dataset
        2D dataset with cell_labels variable added.
    num_cells : int
        Number of detected cells.
    """

    name = "detection"
    pipeline_phase = 1
    inputs = ["grid_ds_2d", "detection_config"]
    outputs = ["segmented_ds", "num_cells"]
    input_contracts = {"grid_ds_2d": check_grid_ds_2d}
    output_contracts = {"segmented_ds": check_segmented_ds}

    def __init__(self) -> None:
        self._segmenter: RadarCellSegmenter | None = None

    def run(self, context: dict) -> dict:
        config = context["detection_config"]
        ds_2d = context["grid_ds_2d"]

        if self._segmenter is None:
            self._segmenter = RadarCellSegmenter(config)

        segmented = self._segmenter.segment(ds_2d)
        num_cells = int(segmented[config.labels_var].max().item())

        return {"segmented_ds": segmented, "num_cells": num_cells}


registry.register(DetectModule)
