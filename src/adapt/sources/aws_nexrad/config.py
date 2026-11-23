# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options of the ``aws_nexrad`` source. Frozen. Only pydantic."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["AwsNexradOptions"]


class AwsNexradOptions(BaseModel):
    """When the source looks in the archive and which downloads it keeps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    latest_files: int = Field(
        default=5,
        ge=1,
        description="Realtime start: newest scans taken from the latest_minutes window",
    )
    latest_minutes: int = Field(
        default=60, ge=1, description="Realtime start: how far back (minutes) to look for scans"
    )
    first_check_sec: int = Field(
        default=200,
        ge=1,
        description="Realtime: seconds after a volume is published before looking for the next",
    )
    expected_arrival_sec: int = Field(
        default=300,
        ge=1,
        description=(
            "Realtime: seconds after a volume from which the next is expected (looks every 10 s)"
        ),
    )
    late_after_sec: int = Field(
        default=450,
        ge=1,
        description=(
            "Realtime: seconds after a volume from which the next is late (looks every 30 s)"
        ),
    )
    max_skipped_scans: int = Field(
        default=3,
        ge=1,
        description="Realtime: volumes skipped while processing is behind before the run stops",
    )
    max_fetch_retries: int = Field(
        default=3, ge=1, description="AWS scan-fetch attempts before giving up for this listing"
    )
    min_file_size: int = Field(
        default=1024, ge=1, description="Minimum file size in bytes to consider valid"
    )

    @model_validator(mode="after")
    def _thresholds_increase(self) -> Self:
        if not self.first_check_sec < self.expected_arrival_sec < self.late_after_sec:
            raise ValueError(
                "need first_check_sec < expected_arrival_sec < late_after_sec, got "
                f"{self.first_check_sec}, {self.expected_arrival_sec}, {self.late_after_sec}"
            )
        return self
