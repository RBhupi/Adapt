# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Frozen YAML configuration for radar command generation.

Read from the ``radar_command`` section of the shared consumer YAML; sibling
sections belong to other components. Unknown keys raise and every number must
be finite. A missing key raises too, except these:

- ``planning`` (and each of its keys): a 12-minute plan, one entry a minute;
- ``extent.vertical_band``: none, so a run without cell_volume_stats has no
  vertical extent;
- ``output.current_file`` / ``output.history_directory``: ``current_targets.json``
  and ``history`` inside ``output.directory``;
- ``api``: none, so commands are only written to files (a dry run).

The API key never appears here: ``api.api_key_env`` names the environment
variable that holds it.
"""

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adapt.utils.files import read_yaml_section

_FROZEN = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class PlanningConfig(BaseModel):
    """Plan times land on projection frames: the interval is whole minutes and
    divides the horizon, giving ``horizon_minutes * 60 / interval_seconds`` entries.
    It must also be a multiple of the run's projection step, checked against the
    stored frames when a plan is built."""

    model_config = _FROZEN

    horizon_minutes: int = Field(default=12, ge=1, le=60)
    interval_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _on_projection_minutes(self) -> "PlanningConfig":
        if self.interval_seconds % 60 != 0:
            raise ValueError(
                f"interval_seconds={self.interval_seconds} is not a whole number of minutes"
            )
        if (self.horizon_minutes * 60) % self.interval_seconds != 0:
            raise ValueError(
                f"interval_seconds={self.interval_seconds} does not divide "
                f"horizon_minutes={self.horizon_minutes}"
            )
        return self


class RadarConfig(BaseModel):
    model_config = _FROZEN

    scan_pattern: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class ApiConfig(BaseModel):
    """Where published commands are POSTed (``{base_url}/publish/command``)."""

    model_config = _FROZEN

    base_url: str = Field(pattern=r"^https?://\S+$")
    api_key_env: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)


class AltitudeBand(BaseModel):
    """Metres above mean sea level."""

    model_config = _FROZEN

    altitude_min_m: float
    altitude_max_m: float

    @model_validator(mode="after")
    def _non_empty(self) -> "AltitudeBand":
        if self.altitude_min_m >= self.altitude_max_m:
            raise ValueError(
                f"altitude_min_m={self.altitude_min_m} must be below "
                f"altitude_max_m={self.altitude_max_m}"
            )
        return self


class ExtentConfig(BaseModel):
    """``horizontal_buffer_km: 0`` disables the buffer. ``vertical_band`` is used
    only when the selected run wrote no cell_volume_stats rows."""

    model_config = _FROZEN

    horizontal_buffer_km: float = Field(ge=0)
    vertical_band: AltitudeBand | None = None


class OutputConfig(BaseModel):
    model_config = _FROZEN

    directory: str = Field(min_length=1)
    current_file: str = "current_targets.json"
    history_directory: str = "history"

    @field_validator("current_file", "history_directory")
    @classmethod
    def _plain_name(cls, name: str) -> str:
        if name in ("", ".", "..") or "/" in name or "\\" in name:
            raise ValueError(f"{name!r} must be a plain name inside output.directory")
        return name


class RadarCommandConfig(BaseModel):
    model_config = _FROZEN

    planning: PlanningConfig = PlanningConfig()
    radar: RadarConfig
    extent: ExtentConfig
    output: OutputConfig
    api: ApiConfig | None = None


def load_config(path: str | Path) -> RadarCommandConfig:
    """Load and validate the ``radar_command`` section of a YAML config file."""
    return RadarCommandConfig.model_validate(read_yaml_section(path, "radar_command"))


def config_hash(cfg: RadarCommandConfig) -> str:
    """16-hex-char identity of the effective config: equal configs hash equal."""
    return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()[:16]
