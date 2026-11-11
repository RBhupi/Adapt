# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Science rules as pure functions.

Scores are raw weighted sums — a cell's score depends only on the cell
itself plus fixed site geometry, so it stays stable as other cells
appear and disappear (the stability the switch margin relies on).
"""

import math
import operator
from collections.abc import Callable, Mapping, Sequence

from adapt.modules.target_selection._geo import haversine_km
from adapt.modules.target_selection.config import (
    DurationConfig,
    EntryConfig,
    HoldConfig,
    QualityGate,
    SitePreferenceConfig,
    TSEConfig,
)
from adapt.modules.target_selection.selection import SelectionReason, StopReason
from adapt.modules.target_selection.snapshot import CellSnapshot, TrajectoryPoint

# Gate operator → comparison. Keyed by the validated config Literal, so an
# unknown operator can never reach here (config validation rejects it first).
_GATE_OPS: dict[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
}


def passes_gates(cell: CellSnapshot, gates: Sequence[QualityGate]) -> bool:
    """True when every gate passes. A NaN gate value (SQL NULL) is a defined
    data condition: fail. A missing gate column is a configuration error: raise."""
    for gate in gates:
        if gate.field not in cell.values:
            raise ValueError(
                f"Gate field {gate.field!r} not found for cell {cell.uid!r}; "
                f"available columns: {sorted(cell.values)}"
            )
        value = cell.values[gate.field]
        if math.isnan(value) or not _GATE_OPS[gate.op](value, gate.value):
            return False
    return True


def is_candidate(cell: CellSnapshot, cfg: EntryConfig) -> bool:
    """Entry rule (part 1): every entry gate must pass.

    Being in the snapshot at all is the "active" clause — snapshots hold
    only latest-scan cells.
    """
    return passes_gates(cell, cfg.gates)


def holds(cell: CellSnapshot, cfg: HoldConfig) -> bool:
    """Hold rule (part 3): the current target stays while every hold gate passes."""
    return passes_gates(cell, cfg.gates)


def priority_score(cell: CellSnapshot, weights: Mapping[str, float]) -> float:
    """Score rule (part 2): the weighted sum of the named snapshot values.

    NaN components must never rank: 0 * NaN is still NaN, and a NaN score
    makes ``select_best`` pick an arbitrary cell silently.
    """
    missing = [name for name in weights if name not in cell.values]
    if missing:
        raise ValueError(
            f"priority_score: weights on {', '.join(missing)} but cell {cell.uid!r} has no "
            f"such value; available columns: {sorted(cell.values)}"
        )
    bad = [name for name in weights if math.isnan(cell.values[name])]
    if bad:
        raise ValueError(
            f"priority_score: cell {cell.uid!r} has NaN {', '.join(bad)} — "
            "refusing to rank on undefined values (check that the run "
            "produced these statistics)"
        )
    return sum(w * cell.values[name] for name, w in weights.items())


def cap_seconds(cfg: DurationConfig) -> float | None:
    """Duration rule (part 5) in seconds, or None for no cap."""
    return None if cfg.max_minutes is None else cfg.max_minutes * 60.0


def site_bonus(trajectory: Sequence[TrajectoryPoint], cfg: SitePreferenceConfig) -> float:
    """Site preference rule: each site's bonus is added once if any of the
    first projection_steps trajectory points falls within its radius."""
    points = trajectory[: cfg.projection_steps]
    bonus = 0.0
    for site in cfg.sites:
        if any(haversine_km(p.lat, p.lon, site.lat, site.lon) <= site.radius_km for p in points):
            bonus += site.bonus
    return bonus


def total_score(cell: CellSnapshot, cfg: TSEConfig) -> float:
    """Full score used for selection: priority plus site bonus."""
    return priority_score(cell, cfg.score.weights) + site_bonus(
        cell.trajectory, cfg.score.site_preference
    )


def select_best(scored: Mapping[str, float]) -> str | None:
    """Selection rule: highest score wins; ties break to smallest uid."""
    if not scored:
        return None
    return min(scored, key=lambda uid: (-scored[uid], uid))


def continue_or_switch(
    current_uid: str, scored: Mapping[str, float], margin: float
) -> tuple[str, SelectionReason]:
    """Switch rule (part 4): keep the current target unless a challenger
    exceeds its score by strictly more than the margin."""
    challengers = {uid: s for uid, s in scored.items() if uid != current_uid}
    best = select_best(challengers)
    if best is not None and challengers[best] > scored[current_uid] + margin:
        return best, SelectionReason.SWITCH
    return current_uid, SelectionReason.CONTINUATION


def should_stop(
    cell: CellSnapshot | None, cfg: TSEConfig, observed_seconds: float
) -> StopReason | None:
    """Stop rule, checked in order: target gone from the snapshot, a hold gate
    no longer passed, the duration cap reached (when there is one)."""
    if cell is None:
        return StopReason.TARGET_LOST
    if not holds(cell, cfg.hold):
        return StopReason.QUALITY_DROPPED
    cap = cap_seconds(cfg.duration)
    if cap is not None and observed_seconds >= cap:
        return StopReason.MAX_DURATION
    return None
