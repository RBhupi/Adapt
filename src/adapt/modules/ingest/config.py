# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the ingest module.

``IngestOptions`` is what a pipeline file sets under ``params: {ingest: ...}``:
how a radar file is read, which of its variables are kept under which names,
and the Cartesian grid each volume is regridded onto. ``IngestConfig`` is
exactly what the ingest node and RadarDataLoader consume: the options, plus
what the node's ``configure`` adds for the run. Frozen. Only pydantic + stdlib.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IngestOptions(BaseModel):
    """Radar file reading and regridding options."""

    # Variable names are taken without the spaces around them.
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    file_format: Literal["nexrad_archive"] = Field(
        "nexrad_archive",
        description="Radar file format to read; only nexrad_archive (NEXRAD Level-II) is supported",
    )
    field_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Source-to-canonical variable renames applied once at ingest, such as "
            "{corrected_reflectivity: reflectivity}; empty means no renames"
        ),
    )
    fields: tuple[str, ...] = Field(
        (),
        description=(
            "Canonical variable names to keep after renaming; empty keeps every "
            "field the source file provides"
        ),
    )
    grid_shape: tuple[int, int, int] = Field(
        (41, 301, 301),
        description="Grid points (nz, ny, nx) of the Cartesian grid each volume is regridded onto",
    )
    grid_limits: tuple[tuple[float, float], tuple[float, float], tuple[float, float]] = Field(
        (
            (0.0, 20000.0),
            (-150000.0, 150000.0),
            (-150000.0, 150000.0),
        ),
        description="Grid extent ((z_min, z_max), (y_min, y_max), (x_min, x_max)) in metres "
        "from the radar",
    )
    roi_func: Literal["dist_beam", "dist"] = Field(
        "dist_beam",
        description="Py-ART radius-of-influence function: dist_beam grows the radius with range "
        "and beam size, dist with range only",
    )
    min_radius: float = Field(
        1750.0, gt=0, description="Smallest radius of influence (m) when regridding"
    )
    weighting_function: Literal["cressman", "barnes", "nearest"] = Field(
        "cressman",
        description="Weighting of the gates within the radius of influence when regridding",
    )


class IngestConfig(IngestOptions):
    """The options, and what the node's ``configure`` adds for the run."""

    z_level: float  # the run's analysis altitude (m): where the 2D slice is taken
