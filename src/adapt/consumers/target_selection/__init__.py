# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Target Selection Engine — selects which tracked cell to observe.

A Ring 2 consumer: reads only through adapt.api.StoreClient, writes only its
decision files under the output directory the launcher gives it
(``<root>/consumers/<run_id>/target_selection/``) — never into the store's
databases — and never controls the radar.
"""

from adapt.consumers.target_selection.config import TSEConfig, config_hash
from adapt.consumers.target_selection.engine import CurrentTarget, TargetSelectionEngine
from adapt.consumers.target_selection.repository_source import build_snapshot
from adapt.consumers.target_selection.rules import is_candidate, total_score
from adapt.consumers.target_selection.selection import (
    Decision,
    RankedCandidate,
    ScanDecision,
    SelectionReason,
    StopReason,
    TargetSelection,
)
from adapt.consumers.target_selection.snapshot import (
    CellSnapshot,
    Snapshot,
    TrajectoryPoint,
)
from adapt.consumers.target_selection.writer import (
    SELECTION_FILE,
    append_decision,
    write_current_selection,
)

__all__ = [
    "SELECTION_FILE",
    "CellSnapshot",
    "CurrentTarget",
    "Decision",
    "RankedCandidate",
    "ScanDecision",
    "SelectionReason",
    "StopReason",
    "Snapshot",
    "TSEConfig",
    "TargetSelection",
    "TargetSelectionEngine",
    "TrajectoryPoint",
    "append_decision",
    "build_snapshot",
    "config_hash",
    "is_candidate",
    "total_score",
    "write_current_selection",
]
