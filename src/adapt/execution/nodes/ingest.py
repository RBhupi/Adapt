# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

from datetime import UTC
from datetime import datetime as _dt
from pathlib import Path

import numpy as np
import xarray as _xr

from adapt.contracts import check_grid_ds_2d
from adapt.execution.module_registry import registry
from adapt.modules.base import BaseModule
from adapt.modules.ingest.module import RadarDataLoader


class LoadModule(BaseModule):
    """BaseModule wrapper for RadarDataLoader.

    Reads a NEXRAD Level-II file, regrids it to Cartesian coordinates,
    and extracts a 2D horizontal slice at the configured z-level.

    Context inputs
    --------------
    nexrad_file : str
        Path to the NEXRAD Level-II file.
    config : InternalConfig
        Runtime configuration (lazy-initialises the loader on first call).
    output_dirs : dict
        Output directory mapping (used for saving intermediate NetCDF).

    Context outputs
    ---------------
    grid_ds : xr.Dataset
        Full 3D Cartesian xarray Dataset.
    grid_ds_2d : xr.Dataset
        2D slice at configured z-level.
    scan_time : datetime
        Radar volume scan time parsed from the filename.
    """

    name = "ingest"
    pipeline_phase = 1
    inputs = ["nexrad_file", "ingest_config"]
    outputs = ["grid_ds", "grid_ds_2d", "scan_time"]
    output_contracts = {"grid_ds_2d": check_grid_ds_2d}

    def __init__(self) -> None:
        self._loader: RadarDataLoader | None = None

    def run(self, context: dict) -> dict:
        config = context["ingest_config"]
        filepath = context["nexrad_file"]
        output_dirs = context.get("output_dirs", {})

        if self._loader is None:
            self._loader = RadarDataLoader(config)

        radar = config.radar
        nc_filename = Path(filepath).stem
        scan_time = _dt.now(UTC)
        try:
            parts = nc_filename.split("_")
            dt_str = parts[0][-8:] + parts[1]
            scan_time = _dt.strptime(dt_str, "%Y%m%d%H%M%S")
        except Exception:
            pass

        date_str = scan_time.strftime("%Y%m%d")
        base = output_dirs.get("base")
        nc_path = base / radar / "gridnc" / date_str / nc_filename if base else None
        output_dir = str(nc_path.parent) if nc_path else None

        ds = self._loader.load_and_regrid(
            filepath,
            save_netcdf=config.save_netcdf,
            output_dir=output_dir,
        )

        if ds is None:
            raise RuntimeError(f"Ingest failed: load_and_regrid returned None for {filepath}")

        z_level = config.z_level
        z_name = config.z_coord
        time_name = config.time_coord
        z_idx = int(np.argmin(np.abs(ds[z_name].values - z_level)))

        ds_2d = _xr.Dataset()
        for var_name in ds.data_vars:
            var = ds[var_name]
            if time_name in var.dims and z_name in var.dims:
                ds_2d[var_name] = var.isel({time_name: 0, z_name: z_idx})
            else:
                ds_2d[var_name] = var
        for coord in ds.coords:
            if coord not in ds_2d.coords:
                ds_2d = ds_2d.assign_coords({coord: ds[coord]})
        ds_2d.attrs.update(ds.attrs)

        return {"grid_ds": ds, "grid_ds_2d": ds_2d, "scan_time": scan_time}


registry.register(LoadModule)
