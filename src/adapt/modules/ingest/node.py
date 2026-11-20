# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

import numpy as np
import xarray as _xr

from adapt.contracts import TIME_COORD, Z_COORD, NetcdfArtifact, SharedConfig, check_grid_ds_2d
from adapt.modules.base import BaseModule
from adapt.modules.ingest.config import IngestConfig, IngestOptions
from adapt.modules.ingest.module import RadarDataLoader


class LoadModule(BaseModule):
    """BaseModule wrapper for RadarDataLoader.

    Reads a NEXRAD Level-II file, regrids it to Cartesian coordinates,
    and extracts a 2D horizontal slice at the configured z-level. The node
    builds no paths: the 3D grid is returned in-memory and persisted by the
    router as a ``gridded3d`` object.

    Context inputs
    --------------
    nexrad_file : str
        Path to the NEXRAD Level-II file.
    scan_time : datetime
        Scan time owned by the source boundary (acquisition parse or replay
        source); seeded into the context by the processor. Never re-derived
        here — a missing value raises instead of substituting a clock.
    ingest_config : IngestConfig
        The module's options and what ``configure`` adds for the run.

    Context outputs
    ---------------
    grid_ds : xr.Dataset
        Full 3D Cartesian xarray Dataset.
    grid_ds_2d : xr.Dataset
        2D slice at configured z-level.
    """

    name = "ingest"
    summary = "read + regrid radar volumes"
    options_class = IngestOptions
    required_history = 1
    inputs = ["nexrad_file", "ingest_config", "scan_time"]
    outputs = ["grid_ds", "grid_ds_2d"]
    output_contracts = {"grid_ds_2d": check_grid_ds_2d}
    persistence = (
        NetcdfArtifact(
            key="grid_ds",
            product_type="gridded3d",
            description="Regridded 3D Cartesian radar volume",
        ),
    )

    @classmethod
    def configure(cls, options: IngestOptions, shared: SharedConfig) -> IngestConfig:
        """The options, and the run's analysis level.

        Raises
        ------
        ValueError
            ``fields`` selects variables and leaves out the run's tracking
            field: the modules after ingest would never see the field they run on.
        """
        field = shared.tracking_field
        if options.fields and field not in options.fields:
            raise ValueError(
                f"params.global.tracking_field {field!r} is excluded by "
                f"params.ingest.fields {list(options.fields)} — the tracked field would "
                "never survive ingest. Add it to params.ingest.fields or clear the list."
            )
        return IngestConfig(**options.model_dump(), z_level=shared.z_level)

    @classmethod
    def method_line(cls, config: IngestConfig) -> str:
        return f"regrid {config.weighting_function} · roi {config.roi_func}"

    def __init__(self) -> None:
        self._loader: RadarDataLoader | None = None

    def run(self, context: dict) -> dict:
        config = context["ingest_config"]
        filepath = context["nexrad_file"]

        if self._loader is None:
            self._loader = RadarDataLoader(config)

        if context.get("scan_time") is None:
            raise ValueError(
                f"Ingest requires a scan_time for {filepath!r} and the source "
                "supplied none — refusing to substitute a clock or re-parse the "
                "filename (wall-clock substitution is forbidden)"
            )

        ds = self._loader.load_and_regrid(filepath)
        if ds is None:
            raise RuntimeError(f"Ingest failed: load_and_regrid returned None for {filepath}")

        z_idx = int(np.argmin(np.abs(ds[Z_COORD].values - config.z_level)))

        ds_2d = _xr.Dataset()
        for var_name in ds.data_vars:
            var = ds[var_name]
            if TIME_COORD in var.dims and Z_COORD in var.dims:
                ds_2d[var_name] = var.isel({TIME_COORD: 0, Z_COORD: z_idx})
            else:
                ds_2d[var_name] = var
        for coord in ds.coords:
            if coord not in ds_2d.coords:
                ds_2d = ds_2d.assign_coords({coord: ds[coord]})
        ds_2d.attrs.update(ds.attrs)

        return {"grid_ds": ds, "grid_ds_2d": ds_2d}
