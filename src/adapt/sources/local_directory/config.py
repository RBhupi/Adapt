# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options of the ``local_directory`` source. Frozen. Only pydantic."""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["LocalDirectoryOptions"]


class LocalDirectoryOptions(BaseModel):
    """Which files of the folder the source acquires."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_file_size: int = Field(
        default=1024, ge=1, description="Minimum file size in bytes to consider valid"
    )
