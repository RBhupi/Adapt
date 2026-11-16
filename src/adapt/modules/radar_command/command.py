# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Radar command model and its JSON wire format.

The wire format lives only here, as ``AutoTargetCommand``: the radar API takes
one target box under the key ``AutoTarget``, so every planned minute is merged
into the single box that covers them all. ``to_json`` writes it and
``from_json`` reads it back, both through that one model, so no other text is
a command. The command carries no API key: the sender adds it as a request
header, so a stored command never holds a credential.

Units of the extents (metres, full width) are an assumption until the radar
side confirms them; ``_box`` is the one place to change.
"""

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adapt.modules.radar_command.extent import EARTH_RADIUS_KM, GeoExtent

_WIRE = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

_M_PER_DEG = EARTH_RADIUS_KM * 1000 * math.pi / 180


@dataclass(frozen=True)
class TargetEntry:
    """Where the target is expected at one whole UTC minute."""

    time: datetime
    extent: GeoExtent


@dataclass(frozen=True)
class RadarCommand:
    """One command: scan pattern, user and target ids, plus time-ordered target entries."""

    scan_pattern: str
    user_id: str
    target_id: str
    entries: tuple[TargetEntry, ...]


class AutoTarget(BaseModel):
    """The target box as the radar API takes it. Every field is required, no
    other is allowed, and a number is a finite JSON number (never a boolean
    or a string)."""

    model_config = _WIRE

    lat: float = Field(ge=-90.0, le=90.0, description="Box centre latitude (deg)")
    lon: float = Field(ge=-180.0, le=180.0, description="Box centre longitude (deg)")
    alt: float = Field(description="Box centre altitude (m above mean sea level)")
    lat_extent: float = Field(gt=0.0, description="Full north-south extent (m)")
    lon_extent: float = Field(gt=0.0, description="Full east-west extent (m)")
    alt_extent: float = Field(gt=0.0, description="Full vertical extent (m)")
    user_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    scan_pattern: str = Field(min_length=1)


class AutoTargetCommand(BaseModel):
    """The whole body of one command: ``{"AutoTarget": {...}}`` and nothing else."""

    model_config = _WIRE

    auto_target: AutoTarget = Field(alias="AutoTarget")


class CommandValidationError(ValueError):
    """A command that must not be sent to the radar."""


def validate(cmd: RadarCommand) -> None:
    """Raise CommandValidationError naming the first rule the command breaks."""
    if not cmd.entries:
        raise CommandValidationError("RadarCommand: needs at least one target entry, got none")
    identities = (
        ("scan_pattern", cmd.scan_pattern),
        ("user_id", cmd.user_id),
        ("target_id", cmd.target_id),
    )
    for name, value in identities:
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
    """Validate, then serialise as the wire body: the box covering every entry."""
    validate(cmd)
    try:
        wire = AutoTargetCommand.model_validate({"AutoTarget": _box(cmd)})
    except ValidationError as exc:
        raise CommandValidationError(f"RadarCommand: {_broken_rules(exc)}") from exc
    return json.dumps(wire.model_dump(by_alias=True), indent=2) + "\n"


def from_json(text: str) -> AutoTargetCommand:
    """The command ``text`` holds.

    Raises CommandValidationError unless ``text`` is exactly one wire body:
    malformed JSON, a non-finite number, a repeated, missing or extra field, and
    a value of the wrong type or outside its range are all refused.
    """
    try:
        body = json.loads(text, object_pairs_hook=_no_repeats)
    except (ValueError, RecursionError) as exc:
        raise CommandValidationError(f"Not a radar command: {exc}") from exc
    try:
        return AutoTargetCommand.model_validate(body)
    except ValidationError as exc:
        raise CommandValidationError(f"Not a radar command: {_broken_rules(exc)}") from exc


def _no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    names = [name for name, _ in pairs]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"field(s) given more than once: {', '.join(repeated)}")
    return dict(pairs)


def _broken_rules(exc: ValidationError) -> str:
    """Each broken rule as ``<field>: <rule>``, without the values."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
        for error in exc.errors()
    )


def _box(cmd: RadarCommand) -> dict[str, float | str]:
    """Box centre (deg, m AMSL) and full extents (m) over all entries."""
    boxes = [entry.extent for entry in cmd.entries]
    south = min(b.lat_center_deg - b.lat_width_deg / 2 for b in boxes)
    north = max(b.lat_center_deg + b.lat_width_deg / 2 for b in boxes)
    # Longitudes relative to the first box, so boxes straddling the antimeridian stay narrow.
    ref = boxes[0].lon_center_deg
    offsets = [math.remainder(b.lon_center_deg - ref, 360.0) for b in boxes]
    west = min(o - b.lon_width_deg / 2 for o, b in zip(offsets, boxes, strict=True))
    east = max(o + b.lon_width_deg / 2 for o, b in zip(offsets, boxes, strict=True))
    bottom = min(b.alt_bottom_m for b in boxes)
    top = max(b.alt_top_m for b in boxes)
    lat = (south + north) / 2
    lon = math.remainder(ref + (west + east) / 2, 360.0)
    return {
        "lat": lat,
        "lon": -180.0 if lon == 180.0 else lon,
        "alt": (bottom + top) / 2,
        "lat_extent": (north - south) * _M_PER_DEG,
        "lon_extent": (east - west) * _M_PER_DEG * math.cos(math.radians(lat)),
        "alt_extent": top - bottom,
        "user_id": cmd.user_id,
        "target_id": cmd.target_id,
        "scan_pattern": cmd.scan_pattern,
    }
