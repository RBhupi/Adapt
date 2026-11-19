# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from adapt.contracts import (
    ProductTableWrite,
    SharedConfig,
    check_cell_adjacency,
    check_cell_stats,
)
from adapt.execution.module_registry import registry
from adapt.modules.analysis.config import ALWAYS_EXCLUDED, AnalysisConfig, AnalysisOptions
from adapt.modules.analysis.module import RadarCellAnalyzer
from adapt.modules.base import BaseModule


class AnalysisModule(BaseModule):
    """BaseModule wrapper for RadarCellAnalyzer.

    Extracts per-cell statistics (area, reflectivity, motion, centroids)
    from a segmented/projected 2D dataset. Pure compute — no I/O.
    Persistence is declared below and routed by the OutputRouter.

    Context inputs
    --------------
    projected_ds : xr.Dataset
        2D dataset with projections (output of ProjectionModule).
    analysis_config : AnalysisConfig
        The module's options and what ``configure`` adds for the run.
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
    summary = "2D per-cell statistics"
    options_class = AnalysisOptions
    inputs = ["projected_ds", "analysis_config", "scan_time"]
    outputs = ["cell_stats", "cell_adjacency"]
    output_contracts = {
        "cell_stats": check_cell_stats,
        "cell_adjacency": check_cell_adjacency,
    }
    persistence = (
        ProductTableWrite(
            key="cell_stats",
            table="cell_stats",
            primary_key=("run_id", "scan_id", "cell_label"),
            index_columns=("cell_label",),
        ),
        ProductTableWrite(
            key="cell_adjacency",
            table="cell_adjacency",
            primary_key=("run_id", "scan_id", "cell_label_a", "cell_label_b"),
        ),
    )

    @classmethod
    def configure(cls, options: AnalysisOptions, shared: SharedConfig) -> AnalysisConfig:
        """The options, the run's field and level, and every variable left out.

        The variables left out are the module's own, then the option's entries,
        each once in that order (the same list on every run).

        Raises
        ------
        ValueError
            The run's tracking field is not among ``radar_variables``: tracking
            reads its statistics, and would fail on the first scan with cells.
        """
        field = shared.tracking_field
        if field not in options.radar_variables:
            raise ValueError(
                f"params.global.tracking_field {field!r} is not in "
                f"params.analysis.radar_variables {list(options.radar_variables)} — analysis "
                "would never compute the per-cell statistics tracking needs. Add it to the list."
            )
        excluded = tuple(dict.fromkeys((*ALWAYS_EXCLUDED, *options.exclude_fields)))
        return AnalysisConfig(
            **{**options.model_dump(), "exclude_fields": excluded},
            reflectivity_var=field,
            z_level=shared.z_level,
        )

    def __init__(self) -> None:
        self._analyzer: RadarCellAnalyzer | None = None

    def run(self, context: dict) -> dict:
        config = context["analysis_config"]
        ds_2d = context["projected_ds"]

        if self._analyzer is None:
            self._analyzer = RadarCellAnalyzer(config)

        df_cells = self._analyzer.extract(ds_2d, z_level=config.z_level)
        df_adjacency = self._analyzer.extract_adjacency(ds_2d)

        return {"cell_stats": df_cells, "cell_adjacency": df_adjacency}


registry.register(AnalysisModule)
