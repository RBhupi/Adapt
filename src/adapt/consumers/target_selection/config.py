# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Frozen YAML configuration for the target selection engine.

Every threshold and weight lives here, under the file's top-level
``target_selection:`` section (sibling sections belong to other consumers and
are ignored). Unknown keys raise, and so does any missing key except these,
each of which has a documented meaning when absent:

- ``output`` (and its ``jsonl_path``): no JSONL log;
- ``candidate.hold_gates``: the entry gates also decide whether a target is kept;
- ``priority.weights.extra``: no score beyond field, area and growth rate;
- ``selection.allow_switch``: true, a better challenger may take over;
- ``snapshot.trend_fields``: no trend columns.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from adapt.utils.files import read_yaml_section

_FROZEN = ConfigDict(frozen=True, extra="forbid")

GateOp = Literal["gt", "ge", "lt", "le"]


class QualityGate(BaseModel):
    """Pluggable quality gate: any numeric snapshot column compared to a value.

    ``op`` selects the comparison — gt (>), ge (>=), lt (<), le (<=) — so a gate
    can express both a floor and a ceiling. There is no default: the operator is
    always explicit.
    """

    model_config = _FROZEN

    field: str
    op: GateOp
    value: float


class CandidateConfig(BaseModel):
    """A cell is a candidate only if every gate passes and it is old enough.

    ``hold_gates``, when given, are the gates a *current* target must keep
    passing to remain the target; the entry ``gates`` are then checked only at
    selection. Without them the entry gates double as hold gates, so a cell
    selected for being young and growing is dropped the moment it stops
    growing.
    """

    model_config = _FROZEN

    gates: tuple[QualityGate, ...]
    min_age_seconds: float
    hold_gates: tuple[QualityGate, ...] | None = None


class PriorityWeights(BaseModel):
    """Weights carry units: points per dBZ, per km2, per km2/min.

    ``extra`` weights any other numeric snapshot column by name (points per
    unit of that column), including the derived ``trend_<column>_per_min``
    columns and ``origin_is_split``. A negative weight is a penalty.
    """

    model_config = _FROZEN

    field: float
    area: float
    growth_rate: float
    extra: dict[str, float] = {}


class PriorityConfig(BaseModel):
    model_config = _FROZEN

    weights: PriorityWeights


class SiteConfig(BaseModel):
    model_config = _FROZEN

    name: str
    lat: float
    lon: float
    radius_km: float
    bonus: float


class SitePreferenceConfig(BaseModel):
    model_config = _FROZEN

    projection_steps: int = Field(ge=1)
    sites: tuple[SiteConfig, ...]


class SelectionConfig(BaseModel):
    """``allow_switch: false`` keeps the current target until it stops (hold
    gates, loss, or the duration cap) regardless of challengers' scores; the
    switch margin is then irrelevant."""

    model_config = _FROZEN

    switch_margin: float
    max_observation_seconds: float
    allow_switch: bool = True


class SnapshotConfig(BaseModel):
    """``trend_fields`` names cells_by_scan columns whose per-minute OLS slope
    over the last ``growth_window_scans`` scans is added to every cell's values
    as ``trend_<column>_per_min`` (0.0 for a cell seen once)."""

    model_config = _FROZEN

    growth_window_scans: int = Field(ge=2)
    trend_fields: tuple[str, ...] = ()


class OutputConfig(BaseModel):
    model_config = _FROZEN

    jsonl_path: str | None = None


class TSEConfig(BaseModel):
    model_config = _FROZEN

    candidate: CandidateConfig
    priority: PriorityConfig
    site_preference: SitePreferenceConfig
    selection: SelectionConfig
    snapshot: SnapshotConfig
    output: OutputConfig = OutputConfig()


def load_config(path: str | Path) -> TSEConfig:
    """Load and validate the ``target_selection`` section. Raises on any invalid content."""
    return TSEConfig.model_validate(read_yaml_section(path, "target_selection"))
