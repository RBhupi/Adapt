# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options of the ``aws_nexrad`` source. Frozen. Only pydantic."""

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AwsNexradOptions"]


class AwsNexradOptions(BaseModel):
    """How the source polls the archive and which downloads it keeps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    latest_files: int = Field(
        default=5,
        ge=1,
        description="Realtime: newest scans taken from each poll's latest_minutes window",
    )
    latest_minutes: int = Field(
        default=60, ge=1, description="Realtime: how far back (minutes) each poll looks for scans"
    )
    poll_interval_sec: int = Field(default=300, ge=1, description="Polling interval in seconds")
    max_fetch_retries: int = Field(
        default=3, ge=1, description="AWS scan-fetch attempts before giving up for this poll"
    )
    min_file_size: int = Field(
        default=1024, ge=1, description="Minimum file size in bytes to consider valid"
    )
