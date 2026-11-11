# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Turn a target's projected footprints into a radar command plan.

Plan times are the projection's own forward frames (the stored
``projection_minute`` grid, whatever its step), every ``interval_seconds`` from
the first. A plan stops at the first planned minute the target is not projected
to: gaps are never filled and footprints are never extrapolated.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from adapt.modules.radar_command.command import RadarCommand, TargetEntry
from adapt.modules.radar_command.config import PlanningConfig, RadarCommandConfig, config_hash
from adapt.modules.radar_command.extent import geographic_extent
from adapt.modules.radar_command.target import SelectionRef, TargetGeometry, TargetUnavailable
from adapt.utils.time import to_scan_iso


@dataclass(frozen=True)
class Plan:
    plan_id: str
    command: RadarCommand
    truncated: bool
    vertical_source: str


def plan_times(frames: tuple[datetime, ...], planning: PlanningConfig) -> tuple[datetime, ...]:
    """Every ``interval_seconds`` from the first projection frame to the horizon.

    Raises ValueError when a plan time within the projection falls between its
    frames: the interval does not fit the run's projection step.
    """
    interval = timedelta(seconds=planning.interval_seconds)
    count = planning.horizon_minutes * 60 // planning.interval_seconds
    times = tuple(frames[0] + k * interval for k in range(count))
    between = [t for t in times if t <= frames[-1] and t not in frames]
    if between:
        raise ValueError(
            f"radar_command.planning.interval_seconds={planning.interval_seconds} does not "
            f"fit the projection frames {to_scan_iso(frames[0])} .. {to_scan_iso(frames[-1])}: "
            f"plan time {to_scan_iso(between[0])} falls between frames"
        )
    return times


def plan_id(ref: SelectionRef, cfg_hash: str) -> str:
    """16-hex-char identity of a plan: same scan, cell and config give the same plan."""
    key = f"{ref.run_id}|{ref.scan_id}|{ref.cell_uid}|{cfg_hash}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def build_plan(geometry: TargetGeometry, ref: SelectionRef, cfg: RadarCommandConfig) -> Plan:
    """One target entry per plan time, boxing that minute's footprint, until the target ends."""
    by_time = {fp.time: fp for fp in geometry.footprints}
    margin_km = geometry.pixel_radius_km + cfg.extent.horizontal_buffer_km
    times = plan_times(tuple(by_time), cfg.planning)
    entries: list[TargetEntry] = []
    for time in times:
        footprint = by_time.get(time)
        if footprint is None or footprint.lats.size == 0:
            break
        extent = geographic_extent(footprint.lats, footprint.lons, margin_km, geometry.vertical)
        entries.append(TargetEntry(time, extent))
    if not entries:
        raise TargetUnavailable(
            f"Cell '{ref.cell_uid}' is not projected to the first plan time "
            f"{to_scan_iso(times[0])} after scan '{ref.scan_id}' of run '{ref.run_id}'"
        )
    pid = plan_id(ref, config_hash(cfg))
    command = RadarCommand(cfg.radar.scan_pattern, cfg.radar.user_id, pid, tuple(entries))
    return Plan(
        plan_id=pid,
        command=command,
        truncated=len(entries) < len(times),
        vertical_source=geometry.vertical.source,
    )
