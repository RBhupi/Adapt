# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Frozen configuration for radar command generation.

The ``radar_command`` entry of a pipeline file's ``params:``, validated with
``RadarCommandConfig.model_validate``. Every command is recorded in the
``radar_commands`` table. Unknown keys raise and every number must be finite.
A missing key raises too, except these:

- ``planning`` (and each of its keys): a 12-minute plan, one entry a minute;
- ``extent.vertical_band``: none, so each target's vertical extent is its
  cell's measured base and top (the ``cell_volume_stats`` module must run);
- ``api``: none, so commands are only recorded (a dry run).

The ``api`` part holds no address, credential or machine path: it names the
environment variables that hold the API's base URL, its key and the CA
certificate file. The environment is their only source, so no value in the file
competes with it.
"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FROZEN = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class PlanningConfig(BaseModel):
    """Plan times land on projection frames: the interval is whole minutes and
    divides the horizon, giving ``horizon_minutes * 60 / interval_seconds`` entries.
    It must also be a multiple of the run's projection step, checked against the
    stored frames when a plan is built."""

    model_config = _FROZEN

    horizon_minutes: int = Field(
        default=12, ge=1, le=60, description="How far ahead (minutes) each plan reaches"
    )
    interval_seconds: int = Field(
        default=60, gt=0, description="Spacing (seconds, whole minutes) of a plan's target entries"
    )

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

    scan_pattern: str = Field(min_length=1, description="Scan pattern the radar is asked to run")
    user_id: str = Field(min_length=1, description="User id sent with every command")


class ApiConfig(BaseModel):
    """The radar API commands are published to (``POST {base URL}/publish/command``).

    Each ``*_env`` option names an environment variable; ``transport.endpoint``
    reads them when the run's config is built and again for each command."""

    model_config = _FROZEN

    base_url_env: str = Field(
        min_length=1,
        description="Environment variable that holds the API base URL (https://...); "
        "commands are POSTed to {base URL}/publish/command",
    )
    api_key_env: str = Field(
        min_length=1,
        description="Environment variable that holds the API key; the key is never in the file",
    )
    ca_file_env: str = Field(
        min_length=1,
        description="Environment variable that holds the path of the CA certificate file "
        "(PEM) the API's TLS certificate is verified against",
    )
    timeout_seconds: float = Field(gt=0, description="Timeout (seconds) of each publish request")


class AltitudeBand(BaseModel):
    """Metres above mean sea level."""

    model_config = _FROZEN

    altitude_min_m: float = Field(description="Bottom of the band (m above mean sea level)")
    altitude_max_m: float = Field(description="Top of the band (m above mean sea level)")

    @model_validator(mode="after")
    def _non_empty(self) -> "AltitudeBand":
        if self.altitude_min_m >= self.altitude_max_m:
            raise ValueError(
                f"altitude_min_m={self.altitude_min_m} must be below "
                f"altitude_max_m={self.altitude_max_m}"
            )
        return self


class ExtentConfig(BaseModel):
    """``horizontal_buffer_km: 0`` disables the buffer. With ``vertical_band``
    every target gets that fixed band; without, its cell's measured base and
    top (from the ``cell_volume_stats`` module)."""

    model_config = _FROZEN

    horizontal_buffer_km: float = Field(
        ge=0,
        description="Margin (km) added around each projected footprint's box, beyond half "
        "a grid pixel",
    )
    vertical_band: AltitudeBand | None = Field(
        default=None,
        description="Fixed altitude band for every target; unset, each cell's measured "
        "base and top are used (needs the cell_volume_stats module)",
    )


class RadarCommandConfig(BaseModel):
    model_config = _FROZEN

    planning: PlanningConfig = Field(
        default=PlanningConfig(),
        description="How far ahead a plan reaches and how its entries are spaced",
    )
    radar: RadarConfig = Field(description="What the radar is asked to do")
    extent: ExtentConfig = Field(description="Size of the target box around each footprint")
    api: ApiConfig | None = Field(
        default=None, description="Where commands are sent; absent, they are only recorded"
    )


def config_hash(cfg: RadarCommandConfig) -> str:
    """16-hex-char identity of the effective config: equal configs hash equal."""
    return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()[:16]
