# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Output data model of the target selection engine."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from adapt.modules.target_selection.snapshot import TrajectoryPoint


class SelectionReason(Enum):
    NEW_TARGET = "NEW_TARGET"
    CONTINUATION = "CONTINUATION"
    SWITCH = "SWITCH"


class StopReason(Enum):
    TARGET_LOST = "TARGET_LOST"
    QUALITY_DROPPED = "QUALITY_DROPPED"
    MAX_DURATION = "MAX_DURATION"


@dataclass(frozen=True)
class TargetSelection:
    """The engine's decision for one snapshot. Radar-agnostic.

    predicted_hulls is always None in v0.1 — hull polygons are not yet
    queryable from the repository; the field is the extension point.
    """

    cell_uid: str
    reason: SelectionReason
    score: float
    selection_time: datetime
    trajectory: tuple[TrajectoryPoint, ...]
    observation_window: tuple[datetime, datetime | None]  # end None: no duration cap
    predicted_hulls: tuple | None = None


@dataclass(frozen=True)
class RankedCandidate:
    cell_uid: str
    score: float


@dataclass(frozen=True)
class Decision:
    """The engine's outcome at one scan — made at every scan, target or not.

    ``previous_cell_uid`` is the target held going into the scan (None when
    idle); ``stop_reason`` says why it ended at this scan (None when it did not
    end). ``candidates`` are the cells eligible at this scan, best first (ties
    to the smallest uid), including a held target that only passes hold gates.
    """

    scan_time: datetime
    selection: TargetSelection | None
    stop_reason: StopReason | None
    previous_cell_uid: str | None
    candidates: tuple[RankedCandidate, ...]
