# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options of the xlma_stat module, which are also its config.

What a pipeline file sets under ``params: {xlma_stat: ...}``, and exactly what
the reader and the science class consume. Frozen. Only pydantic + stdlib.
"""

from pydantic import BaseModel, ConfigDict, Field


class XlmaStatConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # No default folder: None leaves `adapt config --params xlma_stat` a slot to fill,
    # and the node's configure refuses a run without it.
    input_dir: str | None = Field(
        None,
        description="Directory of xLMA flash-sorted NetCDF files (*.nc); required. "
        "Raw LYLOUT ASCII must be converted with xLMA/pyxlma first, and non-NetCDF "
        "files in the directory are ignored",
    )
    search_radius_m: float = Field(
        500.0,
        ge=0.0,
        description="A flash initiating outside every cell goes to the nearest cell "
        "within this distance (m); beyond it the flash is unattributed",
    )
