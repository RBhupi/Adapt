# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Scoring a target-selection rule in hindsight, against the record and an oracle.

The completed tracks define the *ideal targets*: cells worth a scan campaign,
known only once their life is over. A rule is replayed causally over the run
and every ideal target is given one fate:

- ``captured_peak``: held through its reflectivity maximum;
- ``captured_nopeak``: held, but selected after the peak or released before it;
- ``lost_busy``: eligible, but the radar held another target every time;
- ``lost_outranked``: eligible when a new target was chosen, and outscored;
- ``lost_never``: never passed the entry gates while alive.

The first loss is the one-radar capacity limit; the other two are failures of
the rule. The oracle — a scheduler that knows every track's future — gives the
capacity ceiling. ``variant_rule`` builds the rule variants a study compares.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from adapt.modules.target_selection.config import TSEConfig

__all__ = [
    "FATES",
    "IdealConfig",
    "ScanOutcome",
    "StudyConfig",
    "Variant",
    "fates",
    "ideal_targets",
    "oracle",
    "summarize",
    "variant_rule",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

FATES = ("captured_peak", "captured_nopeak", "lost_busy", "lost_outranked", "lost_never")
_STARTS = ("NEW_TARGET", "SWITCH")


class IdealConfig(BaseModel):
    """What makes a cell worth a scan campaign, judged from its completed track."""

    model_config = _FROZEN

    min_scans: int = Field(default=6, ge=2, description="Lived at least this many scans")
    min_field_max: float = Field(
        default=45.0, description="Reached at least this value of the run's tracking field (dBZ)"
    )
    min_peak_observation: int = Field(
        default=3,
        ge=1,
        description="Reached its maximum at this observation or later, so a growth phase "
        "existed to catch",
    )
    exclude_split: bool = Field(default=True, description="Cells born by splitting are not targets")


class Variant(BaseModel):
    """A named rule variant: the parts it gives replace those of the file's rule."""

    model_config = _FROZEN

    name: str = Field(description="How the variant is labelled in the tables")
    entry: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    hold: dict[str, Any] | None = None
    switch: dict[str, Any] | None = None
    duration: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None


class StudyConfig(BaseModel):
    """The ``target_study`` section: the ideal-target criteria and the variants to compare."""

    model_config = _FROZEN

    ideal: IdealConfig = Field(default=IdealConfig(), description="What an ideal target is")
    variants: tuple[Variant, ...] = Field(
        default=(), description="Rule variants replayed next to the file's rule and the oracle"
    )


def variant_rule(base: Mapping[str, Any], variant: Variant) -> TSEConfig:
    """The file's rule with each part ``variant`` gives replaced option by option."""
    rule = {part: dict(options) for part, options in base.items()}
    for part in ("entry", "score", "hold", "switch", "duration", "snapshot"):
        given = getattr(variant, part)
        if given is not None:
            rule[part] = {**rule.get(part, {}), **given}
    return TSEConfig.model_validate(rule)


@dataclass(frozen=True)
class ScanOutcome:
    """What a rule did at one scan: the target it held (and why), and every eligible cell."""

    scan_time: str
    cell_uid: str | None
    reason: str | None  # NEW_TARGET | SWITCH | CONTINUATION | None
    candidates: tuple[str, ...]


def ideal_targets(
    cells: pd.DataFrame, tracks: pd.DataFrame, field_max_column: str, cfg: IdealConfig
) -> pd.DataFrame:
    """The ideal targets of a run: ``cell_uid`` and ``peak_scan_time``, from the record."""
    ordered = cells.sort_values(["cell_uid", "scan_time"])
    by_cell = ordered.groupby("cell_uid", sort=True)
    summary = pd.DataFrame(
        {
            "n_scans": by_cell.size(),
            "field_max": by_cell[field_max_column].max(),
            "peak_observation": by_cell[field_max_column].apply(
                lambda s: int(s.values.argmax()) + 1
            ),
            "peak_scan_time": by_cell.apply(
                lambda g: g["scan_time"].iloc[int(g[field_max_column].values.argmax())]
            ),
        }
    )
    origin = tracks.set_index("cell_uid")["origin_type"]
    keep = (
        (summary["n_scans"] >= cfg.min_scans)
        & (summary["field_max"] >= cfg.min_field_max)
        & (summary["peak_observation"] >= cfg.min_peak_observation)
    )
    if cfg.exclude_split:
        keep &= origin.reindex(summary.index) != "SPLIT"
    ideal = summary[keep].reset_index()
    return ideal[["cell_uid", "peak_scan_time"]]


def fates(
    outcomes: Sequence[ScanOutcome], ideal: pd.DataFrame, life: Mapping[str, Sequence[str]]
) -> pd.DataFrame:
    """One row per ideal target: ``cell_uid``, ``fate``, ``coverage`` (share of its life held)."""
    held: dict[str, list[str]] = {}
    chosen_at: dict[str, str] = {}
    for o in outcomes:
        if o.cell_uid is not None:
            held.setdefault(o.cell_uid, []).append(o.scan_time)
            if o.reason in _STARTS:
                chosen_at[o.scan_time] = o.cell_uid
    eligible_at: dict[str, list[str]] = {}
    for o in outcomes:
        for uid in o.candidates:
            eligible_at.setdefault(uid, []).append(o.scan_time)
    rows = []
    for uid, peak in zip(ideal["cell_uid"], ideal["peak_scan_time"], strict=True):
        if uid in held:
            fate = "captured_peak" if peak in held[uid] else "captured_nopeak"
            coverage = len(held[uid]) / len(life[uid])
        else:
            coverage = 0.0
            when = eligible_at.get(uid, [])
            if not when:
                fate = "lost_never"
            elif any(chosen_at.get(t, uid) != uid for t in when):
                fate = "lost_outranked"
            else:
                fate = "lost_busy"
        rows.append({"cell_uid": uid, "fate": fate, "coverage": coverage})
    return pd.DataFrame(rows, columns=["cell_uid", "fate", "coverage"])


def summarize(
    target_fates: pd.DataFrame, outcomes: Sequence[ScanOutcome], ideal_uids: set[str]
) -> dict[str, Any]:
    """Counts per fate, the targeting episodes, those on non-ideal cells, and the coverage."""
    starts = [o.cell_uid for o in outcomes if o.reason in _STARTS]
    captured = target_fates[target_fates["fate"].str.startswith("captured")]
    return {
        **{fate: int((target_fates["fate"] == fate).sum()) for fate in FATES},
        "n_ideal": int(len(target_fates)),
        "episodes": len(starts),
        "false_alarms": sum(uid not in ideal_uids for uid in starts),
        "coverage_median": float(captured["coverage"].median()) if len(captured) else None,
    }


def oracle(ideal: pd.DataFrame, life: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """One radar that knows the future: whenever free, take the ideal target at its 2nd
    observation with the longest remaining life and hold it to its end."""
    second_observation: dict[str, list[str]] = {}
    for uid in ideal["cell_uid"]:
        second_observation.setdefault(life[uid][1], []).append(uid)
    busy_until: str | None = None
    captured: list[str] = []
    for scan_time in sorted(second_observation):
        if busy_until is not None and scan_time <= busy_until:
            continue
        uid = max(second_observation[scan_time], key=lambda u: (len(life[u]), u))
        captured.append(uid)
        busy_until = life[uid][-1]
    coverage = [(len(life[uid]) - 1) / len(life[uid]) for uid in captured]
    return {
        "captured_peak": len(captured),
        "captured_nopeak": 0,
        "lost_busy": int(len(ideal)) - len(captured),
        "lost_outranked": 0,
        "lost_never": 0,
        "n_ideal": int(len(ideal)),
        "episodes": len(captured),
        "false_alarms": 0,
        "coverage_median": median(coverage) if coverage else None,
    }
