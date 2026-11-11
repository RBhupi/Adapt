# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Config schema for the xlma_stat module.

Holds exactly what the reader and the science class consume. Frozen. Built at
startup by the node's build_config from the resolved InternalConfig. Only pydantic
+ stdlib.
"""

from pydantic import BaseModel, ConfigDict, Field


class XlmaStatConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # No default folder: None leaves `adapt config --show xlma_stat` a slot to fill,
    # and the node's build_config refuses to configure a run without it.
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
