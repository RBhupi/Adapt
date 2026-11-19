# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""cell_volume_stats — per-cell 3D statistics from the gridded volume.

One row per segmented cell, keyed (run_id, scan_id, cell_label) in the
cell_volume_stats table: it needs the cells' labels, not their track identity,
so it does not wait for tracking. That key is the key of the ``cells_by_scan``
view, so every tracked cell's row there carries these columns beside its uid;
no column name here may repeat one of another table with the key. The 3D grid
is ingest's in-memory ``grid_ds``.

Output columns (authoritative — documented here, schema inferred from the frame):
  run_id, scan_time, cell_label                               : index
  cell_area_km2, cell_volume_km3, cell_top_m/base_m/depth_m   : geometry
  dbz_{max,mean,std,min}, z_{mean,max,std}                    : reflectivity
  vol_{20,30,40,50,60}dbz_km3                                 : volume-by-threshold
  cell_eth{10,20,30,40,50}_*                                  : echo-top features
  dbz_{max,com}_height_m                                      : height stats
  {zdr,kdp,rhohv}_{max,mean,std,min}                          : polarimetric (if present)
  multilayer_fraction, mean_nlayers, max_nlayers              : storm structure
"""

import numpy as np
import pandas as pd

from adapt.contracts import (
    CELL_LABELS_VAR,
    ProductTableWrite,
    SharedConfig,
    check_cell_volume_stats,
)
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.cell_volume_stats.config import CellVolumeStatsConfig, CellVolumeStatsOptions
from adapt.modules.cell_volume_stats.module import CellVolumeStatsAlgorithm


class CellVolumeStatsModule(BaseModule):
    name = "cell_volume_stats"
    summary = "3D volume stats per cell (base, top, echo-top heights)"
    required_history = 1
    options_class = CellVolumeStatsOptions
    inputs = [
        "cell_volume_stats_config",
        "grid_ds",
        "segmented_ds",
    ]
    outputs = ["cell_volume_stats_rows"]
    output_contracts = {"cell_volume_stats_rows": check_cell_volume_stats}
    persistence = (
        ProductTableWrite(
            key="cell_volume_stats_rows",
            table="cell_volume_stats",
            primary_key=("run_id", "scan_id", "cell_label"),
            index_columns=("scan_time",),
        ),
    )

    @classmethod
    def configure(
        cls, options: CellVolumeStatsOptions, shared: SharedConfig
    ) -> CellVolumeStatsConfig:
        """The options, computed on the field the run tracks."""
        return CellVolumeStatsConfig(**options.model_dump(), reflectivity_var=shared.tracking_field)

    def run(self, context: dict) -> dict:
        config = context["cell_volume_stats_config"]
        cell_labels_2d = context["segmented_ds"][CELL_LABELS_VAR].values
        labels = np.unique(cell_labels_2d[cell_labels_2d > 0])
        algo = CellVolumeStatsAlgorithm(config)
        rows = [
            algo.compute_cell(context["grid_ds"], cell_labels_2d, int(label)) for label in labels
        ]
        return {"cell_volume_stats_rows": pd.DataFrame(rows)}


registry.register(CellVolumeStatsModule)
