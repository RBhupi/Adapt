# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import check_cell_adjacency, check_cell_stats
from adapt.execution.module_registry import registry
from adapt.modules.analysis.module import RadarCellAnalyzer
from adapt.modules.base import BaseModule


class AnalysisModule(BaseModule):
    """BaseModule wrapper for RadarCellAnalyzer.

    Extracts per-cell statistics (area, reflectivity, motion, centroids)
    from a segmented/projected 2D dataset. Pure compute — no I/O.
    Persistence is the processor's responsibility.

    Context inputs
    --------------
    projected_ds : xr.Dataset
        2D dataset with projections (output of ProjectionModule).
    config : InternalConfig
        Runtime configuration.
    scan_time : datetime
        Radar scan timestamp (from LoadModule).

    Context outputs
    ---------------
    cell_stats : pd.DataFrame
        Per-cell statistics DataFrame.
    cell_adjacency : pd.DataFrame
        Touching-cell pairs DataFrame.
    """

    name = "analysis"
    inputs = ["projected_ds", "analysis_config", "scan_time"]
    outputs = ["cell_stats", "cell_adjacency"]
    output_contracts = {
        "cell_stats": check_cell_stats,
        "cell_adjacency": check_cell_adjacency,
    }

    def __init__(self) -> None:
        self._analyzer = None

    def run(self, context: dict) -> dict:
        config = context["analysis_config"]
        ds_2d = context["projected_ds"]

        if self._analyzer is None:
            self._analyzer = RadarCellAnalyzer(config)

        df_cells = self._analyzer.extract(ds_2d, z_level=config.z_level)
        df_adjacency = self._analyzer.extract_adjacency(ds_2d)

        return {"cell_stats": df_cells, "cell_adjacency": df_adjacency}


registry.register(AnalysisModule)
