# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Target selection: which tracked cell the radar should watch.

Pure rules over a frozen Snapshot of one scan. The pipeline's
``target_selection`` module runs them every scan; store clients (the dashboard,
replay studies) run the same rules on stored runs.
"""

from adapt.modules.target_selection.config import TrackSummary, TSEConfig, config_hash
from adapt.modules.target_selection.engine import CurrentTarget, TargetSelectionEngine
from adapt.modules.target_selection.frames import snapshot_at
from adapt.modules.target_selection.rules import is_candidate, total_score
from adapt.modules.target_selection.selection import (
    Decision,
    RankedCandidate,
    SelectionReason,
    StopReason,
    TargetSelection,
)
from adapt.modules.target_selection.snapshot import (
    CellSnapshot,
    Snapshot,
    TrajectoryPoint,
)

__all__ = [
    "CellSnapshot",
    "CurrentTarget",
    "Decision",
    "RankedCandidate",
    "SelectionReason",
    "Snapshot",
    "StopReason",
    "TSEConfig",
    "TargetSelection",
    "TargetSelectionEngine",
    "TrackSummary",
    "TrajectoryPoint",
    "config_hash",
    "is_candidate",
    "snapshot_at",
    "total_score",
]
