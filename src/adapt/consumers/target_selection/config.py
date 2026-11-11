# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Frozen configuration for the target selection engine: one rule in five parts.

The ``target_selection`` section of a pipeline file's ``consumers:``, validated
with ``TSEConfig.model_validate``. A rule has five parts, each a section:

1. ``entry``    — gates a cell must pass to be a candidate;
2. ``score``    — the weighted sum that ranks the candidates (plus site bonuses);
3. ``hold``     — gates the current target must keep passing to stay the target;
4. ``switch``   — whether, and by what margin, a newcomer may displace it;
5. ``duration`` — how long a target is followed at most.

``snapshot`` says which derived per-cell values the gates and weights can use.
Gates and weights name columns of the per-cell snapshot: every numeric column of
cells_by_scan and the track summary, plus ``age_seconds``, ``field_max`` (the
run's tracking field), ``growth_rate_sqkm_per_min``, ``origin_is_split`` and the
``trend_<column>_per_min`` columns. Where decisions are written is not
configurable (see the runner). Unknown keys raise; every key is required except
``snapshot.trend_fields`` (no trend columns) and ``snapshot.track_summary``
(``at_scan``, the causal view live selection has).
"""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(frozen=True, extra="forbid")

GateOp = Literal["gt", "ge", "lt", "le"]

# Which track summary (n_scans, duration_seconds, max_area_sqkm, max_reflectivity)
# a replayed scan sees: as it stood at that scan (what live selection sees), or
# as it stands at the end of the run (a perfect-knowledge oracle, replay only).
TrackSummary = Literal["at_scan", "end_of_run"]


class QualityGate(BaseModel):
    """Pluggable quality gate: any numeric snapshot column compared to a value.

    ``op`` selects the comparison — gt (>), ge (>=), lt (<), le (<=) — so a gate
    can express both a floor and a ceiling. There is no default: the operator is
    always explicit.
    """

    model_config = _FROZEN

    field: str = Field(
        description="Numeric snapshot column the gate tests, such as n_scans or "
        "trend_area_40dbz_km2_per_min"
    )
    op: GateOp = Field(description="Comparison of the column with value: gt, ge, lt or le")
    value: float = Field(
        description="Value the column is compared with; a NaN column value fails the gate"
    )


class EntryConfig(BaseModel):
    """Part 1: a cell is a candidate only if every entry gate passes."""

    model_config = _FROZEN

    gates: tuple[QualityGate, ...] = Field(
        description="Entry gates: a cell is a candidate only when every gate passes "
        "(an age floor is a gate on age_seconds)"
    )


class SiteConfig(BaseModel):
    model_config = _FROZEN

    name: str = Field(description="Site name")
    lat: float = Field(description="Site latitude (degrees)")
    lon: float = Field(description="Site longitude (degrees)")
    radius_km: float = Field(
        description="A projected cell position within this distance (km) earns the bonus"
    )
    bonus: float = Field(
        description="Points added once when any checked projected position is within radius_km"
    )


class SitePreferenceConfig(BaseModel):
    model_config = _FROZEN

    projection_steps: int = Field(
        ge=1, description="How many of a cell's projected positions are checked against the sites"
    )
    sites: tuple[SiteConfig, ...] = Field(
        description="Sites that add a bonus to cells projected to pass over them"
    )


class ScoreConfig(BaseModel):
    """Part 2: a candidate's score is the weighted sum of its values, plus site bonuses.

    Weights carry units — points per unit of the named column (dBZ, km2,
    km2/min, ...). A negative weight is a penalty. Scores depend on the cell
    alone plus fixed site geometry, so they stay stable as other cells come and go.
    """

    model_config = _FROZEN

    weights: dict[str, float] = Field(
        description="Points per unit of each named snapshot column; a negative weight is a penalty"
    )
    site_preference: SitePreferenceConfig = Field(
        description="Bonus for cells heading over instrumented sites"
    )


class HoldConfig(BaseModel):
    """Part 3: the current target stays the target while every hold gate passes.

    Hold gates are usually looser than entry gates: a cell selected for being
    young and growing should not be dropped the moment it stops growing. Empty
    gates hold the target until the tracker ends the cell or the duration cap
    is reached.
    """

    model_config = _FROZEN

    gates: tuple[QualityGate, ...] = Field(
        description="Gates the current target must keep passing; empty: held until the "
        "tracker ends it or the duration cap"
    )


class SwitchConfig(BaseModel):
    """Part 4: whether a better-scoring newcomer may displace the current target."""

    model_config = _FROZEN

    allow: bool = Field(
        description="Let a better-scoring newcomer take over; false keeps the target until it stops"
    )
    margin: float = Field(
        description="A newcomer takes over only when it outscores the target by more than this"
    )


class DurationConfig(BaseModel):
    """Part 5: how long a target is followed at most."""

    model_config = _FROZEN

    max_minutes: float | None = Field(
        gt=0,
        description="A target is dropped once it has been followed this long; null = no cap",
    )


class SnapshotConfig(BaseModel):
    """``trend_fields`` names cells_by_scan columns whose per-minute OLS slope
    over the last ``growth_window_scans`` scans is added to every cell's values
    as ``trend_<column>_per_min`` (0.0 for a cell seen once). ``track_summary``
    picks the causal (``at_scan``) or oracle (``end_of_run``) track summary."""

    model_config = _FROZEN

    growth_window_scans: int = Field(
        ge=2, description="Scans over which growth rates and trends are fitted"
    )
    trend_fields: tuple[str, ...] = Field(
        default=(),
        description="cells_by_scan columns whose per-minute slope is added as "
        "trend_<column>_per_min",
    )
    track_summary: TrackSummary = Field(
        default="at_scan",
        description="Track summary a scan sees: at_scan (as live selection sees it) or "
        "end_of_run (an oracle, for replay studies)",
    )


class TSEConfig(BaseModel):
    model_config = _FROZEN

    entry: EntryConfig = Field(description="1. Which cells may be selected")
    score: ScoreConfig = Field(description="2. How candidates are ranked")
    hold: HoldConfig = Field(description="3. When the current target is kept")
    switch: SwitchConfig = Field(description="4. Whether a newcomer may displace it")
    duration: DurationConfig = Field(description="5. How long a target is followed at most")
    snapshot: SnapshotConfig = Field(
        description="Derived per-cell values the gates and weights can use"
    )


def config_hash(cfg: TSEConfig) -> str:
    """16-hex-char identity of the effective rules: equal configs hash equal."""
    return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()[:16]
