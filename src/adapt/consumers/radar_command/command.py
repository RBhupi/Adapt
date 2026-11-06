# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command model and its JSON wire format.

The wire format lives only here. The command carries no ApiToken: the future
transport injects it when sending, so command files never hold a credential.
"""

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from adapt.consumers.radar_command.extent import GeoExtent
from adapt.utils.time import to_scan_iso


@dataclass(frozen=True)
class TargetEntry:
    """Where the target is expected at one whole UTC minute."""

    time: datetime
    extent: GeoExtent


@dataclass(frozen=True)
class RadarCommand:
    """One command: radar command/pattern/user plus time-ordered target entries."""

    command: str
    pattern: str
    user: str
    entries: tuple[TargetEntry, ...]


class CommandValidationError(ValueError):
    """A RadarCommand that must not be sent to the radar."""


def validate(cmd: RadarCommand) -> None:
    """Raise CommandValidationError naming the first rule the command breaks."""
    if not cmd.entries:
        raise CommandValidationError("RadarCommand: needs at least one target entry, got none")
    for name, value in (("command", cmd.command), ("pattern", cmd.pattern), ("user", cmd.user)):
        if not value:
            raise CommandValidationError(f"RadarCommand: {name} must be a non-empty string")
    for i, entry in enumerate(cmd.entries):
        _check_time(i, entry.time)
        _check_extent(i, entry.extent)
    for i, (prev, entry) in enumerate(pairwise(cmd.entries), start=1):
        if entry.time <= prev.time:
            raise CommandValidationError(
                f"RadarCommand entry {i}: time {entry.time} is not after {prev.time}; "
                "entry times must be strictly increasing"
            )


def _check_time(i: int, time: datetime) -> None:
    if time.utcoffset() is None:
        raise CommandValidationError(f"RadarCommand entry {i}: time {time} has no timezone")
    if time.utcoffset() != timedelta(0):
        raise CommandValidationError(f"RadarCommand entry {i}: time {time} is not UTC")
    if time.second or time.microsecond:
        raise CommandValidationError(f"RadarCommand entry {i}: time {time} is not a whole minute")


def _check_extent(i: int, extent: GeoExtent) -> None:
    for name, value in asdict(extent).items():
        if not math.isfinite(value):
            raise CommandValidationError(f"RadarCommand entry {i}: {name}={value} is not finite")
    for name, width in (
        ("lat_width_deg", extent.lat_width_deg),
        ("lon_width_deg", extent.lon_width_deg),
    ):
        if width <= 0:
            raise CommandValidationError(f"RadarCommand entry {i}: {name}={width} must be > 0")
    if not -90.0 <= extent.lat_center_deg <= 90.0:
        raise CommandValidationError(
            f"RadarCommand entry {i}: lat_center_deg={extent.lat_center_deg} outside [-90, 90]"
        )
    half = extent.lat_width_deg / 2
    if extent.lat_center_deg - half < -90.0 or extent.lat_center_deg + half > 90.0:
        raise CommandValidationError(
            f"RadarCommand entry {i}: box {extent.lat_center_deg} +- {half} deg latitude "
            "crosses a pole"
        )
    if not -180.0 <= extent.lon_center_deg < 180.0:
        raise CommandValidationError(
            f"RadarCommand entry {i}: lon_center_deg={extent.lon_center_deg} outside [-180, 180)"
        )
    if extent.alt_top_m <= extent.alt_bottom_m:
        raise CommandValidationError(
            f"RadarCommand entry {i}: alt_top_m={extent.alt_top_m} must be above "
            f"alt_bottom_m={extent.alt_bottom_m}"
        )


def to_json(cmd: RadarCommand) -> str:
    """Validate, then serialise: box centre + full widths (deg), altitude centre + depth (m)."""
    validate(cmd)
    target_data = {to_scan_iso(entry.time): _target(entry.extent) for entry in cmd.entries}
    wire = {
        "Command": cmd.command,
        "Pattern": cmd.pattern,
        "User": cmd.user,
        "TargetData": target_data,
    }
    return json.dumps(wire, indent=2) + "\n"


def _target(extent: GeoExtent) -> dict[str, float]:
    return {
        "lat": extent.lat_center_deg,
        "lon": extent.lon_center_deg,
        "alt": (extent.alt_bottom_m + extent.alt_top_m) / 2,
        "dLat": extent.lat_width_deg,
        "dLon": extent.lon_width_deg,
        "dAlt": extent.alt_top_m - extent.alt_bottom_m,
    }
