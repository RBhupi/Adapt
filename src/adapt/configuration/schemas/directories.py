# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Directory setup and path generation utilities for Adapt pipeline.

This module provides functions for creating the standard Adapt directory structure
and generating paths for various artifact types.

Author: Bhupendra Raut
"""

from datetime import datetime
from pathlib import Path


def setup_output_directories(base_dir: str) -> dict[str, Path]:
    """Setup output directory structure.

    Creates the standard Adapt directory layout under base_dir.
    Radar-specific subdirectories are created dynamically as needed.

    Structure:
        base_dir/
        ├── catalog/          # SQLite catalog database
        ├── logs/             # Pipeline logs
        └── {RADAR_ID}/       # Created per radar (dynamic)
            ├── nexrad/       # Raw NEXRAD Level-II files
            ├── gridnc/       # Gridded NetCDF files
            ├── analysis/     # Cell analysis results (Parquet/DB)
            └── plots/        # Visualization outputs

    Parameters
    ----------
    base_dir : str
        Base directory for all outputs

    Returns
    -------
    Dict[str, Path]
        Dictionary with keys: base, catalog, logs
        Radar-specific paths created dynamically by other functions

    Examples
    --------
    >>> dirs = setup_output_directories("/tmp/adapt_output")
    >>> print(dirs["base"])
    /tmp/adapt_output
    >>> print(dirs["logs"])
    /tmp/adapt_output/logs
    """
    base_path = Path(base_dir)

    # Create base and root-level directories
    dirs = {
        "base": base_path,
        "catalog": base_path / "catalog",
        "logs": base_path / "logs",
    }

    # Create directories
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    return dirs


def get_plot_path(
    output_dirs: dict[str, Path],
    radar: str,
    plot_type: str = None,
    scan_time: datetime = None,
    filename: str = None,
) -> Path:
    """Get path for plot/visualization file.

    Pattern: base_dir/RADAR_ID/plots/YYYYMMDD/filename

    Parameters
    ----------
    output_dirs : Dict[str, Path]
        Output directories from setup_output_directories()
    radar : str
        Radar identifier (e.g., "KDIX")
    plot_type : str, optional
        Type of plot (e.g., "reflectivity", "cells")
    scan_time : datetime, optional
        Scan timestamp for date-based organization
    filename : str, optional
        Plot filename (e.g., "reflectivity_KDIX_123045.png")

    Returns
    -------
    Path
        Full path to plot file
    """
    if scan_time:
        date_str = scan_time.strftime("%Y%m%d")
        time_str = scan_time.strftime("%H%M%S")
        base_dir = output_dirs["base"] / radar / "plots" / date_str
    else:
        base_dir = output_dirs["base"] / radar / "plots"
    base_dir.mkdir(parents=True, exist_ok=True)

    if filename:
        return base_dir / filename
    elif plot_type and scan_time:
        filename = f"{radar}_{plot_type}_{time_str}.png"
        return base_dir / filename
    else:
        return base_dir


__all__ = [
    "setup_output_directories",
    "get_plot_path",
]
