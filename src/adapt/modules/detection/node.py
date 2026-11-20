# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

import logging

import numpy as np

from adapt.contracts import (
    CELL_LABELS_VAR,
    DecisionTableWrite,
    SharedConfig,
    check_grid_ds_2d,
    check_prior_scans,
    check_seed_carry,
    check_segmentation_decisions,
    check_segmented_ds,
    require,
)
from adapt.modules.base import BaseModule
from adapt.modules.detection.config import DetectionConfig, DetectionOptions
from adapt.modules.detection.module import CarriedLabels, RadarCellSegmenter

logger = logging.getLogger(__name__)


class DetectModule(BaseModule):
    """BaseModule wrapper for RadarCellSegmenter.

    Segments convective cells from a 2D reflectivity field using
    threshold and morphological filtering.

    Context inputs
    --------------
    grid_ds_2d : xr.Dataset
        2D Cartesian dataset (output of LoadModule).
    grid_ds : xr.Dataset
        Full 3D Cartesian dataset (also an ingest output). The pyart
        convective/stratiform methods classify it; ``threshold`` ignores it.
    prior_scans : list[dict]
        The previous completed scans' contexts, oldest first, provided by the
        processor (empty on the first scan or after a too-large time gap).
        With ``params.detection.seed_carry_frames`` above 0, the last one's
        ``projected_ds`` (the previous frame's labels advected one flow step
        forward) and ``seed_carry`` are handed to the segmenter as carried labels.
    detection_config : DetectionConfig
        The module's options and what ``configure`` adds for the run.

    Context outputs
    ---------------
    segmented_ds : xr.Dataset
        2D dataset with cell_labels variable added.
    num_cells : int
        Number of detected cells.
    seed_carry : tuple[int, ...]
        Carry age per cell label (index i -> label i + 1): 0 when seeded
        independently this scan, k when carried k consecutive scans. Empty
        with the flag off.
    """

    name = "detection"
    summary = "segment cells from the grid"
    required_history = 1
    options_class = DetectionOptions
    # Seed carry: the previous scan's advected labels (projection's), carry ages
    # and cell count. Read only while the carry is on (``prior_inputs_for``).
    prior_inputs = ("projected_ds", "seed_carry", "num_cells")
    inputs = ["grid_ds_2d", "grid_ds", "prior_scans", "detection_config"]
    # detection_decisions: every seed and carried claim this scan and what
    # became of it — analysis-only, written to decisions.db.
    outputs = ["segmented_ds", "num_cells", "seed_carry", "detection_decisions"]
    input_contracts = {"grid_ds_2d": check_grid_ds_2d, "prior_scans": check_prior_scans}
    output_contracts = {
        "segmented_ds": check_segmented_ds,
        "seed_carry": check_seed_carry,
        "detection_decisions": check_segmentation_decisions,
    }
    persistence = (DecisionTableWrite(key="detection_decisions"),)

    @classmethod
    def configure(cls, options: DetectionOptions, shared: SharedConfig) -> DetectionConfig:
        """The options, the selected method's parameters, and the run's field and level."""
        return DetectionConfig(
            **options.model_dump(),
            method_params=getattr(options, f"{options.method}_params").model_dump(),
            reflectivity_var=shared.tracking_field,
            z_level=shared.z_level,
        )

    @classmethod
    def prior_inputs_for(cls, config: DetectionConfig) -> tuple[str, ...]:
        """The seed-carry inputs, read only while the carry is on."""
        return cls.prior_inputs if config.seed_carry_frames > 0 else ()

    @classmethod
    def method_line(cls, config: DetectionConfig) -> str:
        return f"{config.method} · min_cellsize {config.min_cellsize_gridpoint}"

    def __init__(self) -> None:
        self._segmenter: RadarCellSegmenter | None = None

    def run(self, context: dict) -> dict:
        config = context["detection_config"]
        ds_2d = context["grid_ds_2d"]

        if self._segmenter is None:
            self._segmenter = RadarCellSegmenter(config)

        carried = self._carried(context["prior_scans"], config)
        segmented = self._segmenter.segment(ds_2d, context["grid_ds"], carried)
        labels = segmented[CELL_LABELS_VAR]
        num_cells = int(labels.max().item())
        seed_carry = (
            tuple(int(age) for age in np.atleast_1d(labels.attrs["carry_age"]))
            if config.seed_carry_frames > 0 and num_cells
            else ()
        )

        return {
            "segmented_ds": segmented,
            "num_cells": num_cells,
            "seed_carry": seed_carry,
            "detection_decisions": self._segmenter.decisions(),
        }

    @staticmethod
    def _carried(prior_scans: list[dict], config: DetectionConfig) -> CarriedLabels | None:
        """The previous scan's one-step-ahead projected labels with their carry ages.

        None with the flag off, without a prior scan, or when the prior has
        no motion field yet (projection first runs at the second scan and is
        skipped after a too-large gap): the carry is undefined without one.
        """
        if config.seed_carry_frames == 0 or not prior_scans:
            return None
        prior = prior_scans[-1]
        if "projected_ds" not in prior:
            logger.debug("seed_carry: prior scan has no projected_ds; nothing carried")
            return None

        ages = prior["seed_carry"]
        require(
            len(ages) == prior["num_cells"],
            f"seed_carry: prior scan carries {len(ages)} ages for {prior['num_cells']} cells",
        )
        projections = prior["projected_ds"]["cell_projections"]
        require(
            projections.sizes["frame_offset"] >= 2,
            "seed_carry needs params.projection.max_projection_steps >= 1 "
            "(no one-step-ahead labels)",
        )
        advected = np.asarray(projections.isel(frame_offset=1).values)
        require(
            int(advected.max()) <= len(ages),
            f"seed_carry: advected label {int(advected.max())} exceeds prior cell count",
        )
        return CarriedLabels(labels=advected, ages=tuple(ages))
