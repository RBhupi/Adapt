# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Per-frame log of the tracker's matching decisions.

``FrameLog`` accumulates, stage by stage, what happened to every candidate
pair of one scan pair and freezes it into ``TrackingDecisions`` — the
analysis-only record persisted to ``decisions.db``. It is a builder: mutable
for the duration of one ``CellTracker.track`` call, immutable afterwards.
"""

from dataclasses import dataclass, field

from adapt.contracts import (
    TrackingCandidate,
    TrackingDecisions,
    TrackingFrame,
    TrackingSplitMergeTest,
    TrackingUnmatched,
)

__all__ = ["FrameLog", "LogCell", "STAGE_ASSIGNMENT", "STAGE_KINEMATIC", "STAGE_OVERLAP"]

STAGE_OVERLAP = "OVERLAP"
STAGE_KINEMATIC = "KINEMATIC"
STAGE_ASSIGNMENT = "ASSIGNMENT"
_STAGE_ORDER = {STAGE_OVERLAP: 0, STAGE_KINEMATIC: 1, STAGE_ASSIGNMENT: 2}


@dataclass(frozen=True)
class LogCell:
    """Identity of one previous cell as the log sees it."""

    uid: str
    label: int
    steps_before: int = 0


@dataclass
class FrameLog:
    """Builder for one frame pair's ``TrackingDecisions``."""

    dt_s: float | None
    prev: list[LogCell]
    curr_labels: list[int]
    reset_code: str | None = None
    _pairs: dict[tuple[int, int], dict] = field(default_factory=dict)
    _tests: list[TrackingSplitMergeTest] = field(default_factory=list)
    _matched_prev: dict[int, str] = field(default_factory=dict)  # prev idx -> method
    _split_children: set[int] = field(default_factory=set)
    _merge_sources: set[str] = field(default_factory=set)  # prev cell uids
    _born_uids: dict[int, str] = field(default_factory=dict)

    # ── stage recorders ────────────────────────────────────────────────────

    def candidate(self, i: int, j: int, **geometry) -> None:
        """Stage 2/3: a generated pair with its overlap-gate result."""
        self._pairs[(i, j)] = {
            **geometry,
            "speed_ms": None,
            "previous_speed_ms": None,
            "accel_cap_ms": None,
            "kinematic_code": None,
            "displacement_m": None,
            "length_scale_m": None,
            "heading_change_deg": None,
            "cost": None,
            "component_n_prev": None,
            "component_n_curr": None,
            "cost_rank": None,
            "match_method": None,
            "last_stage": STAGE_OVERLAP,
            "outcome": "CONTINUE" if geometry["overlap_passed"] else "REJECTED_OVERLAP",
        }

    def kinematic(self, i: int, j: int, **motion) -> None:
        """Stage 4: the kinematic gate's numbers; a code means rejection."""
        row = self._pairs[(i, j)]
        row.update(motion, last_stage=STAGE_KINEMATIC)
        if motion["kinematic_code"] is not None:
            row["outcome"] = "REJECTED_KINEMATIC"

    def costed(self, i: int, j: int, **cost) -> None:
        """Stage 5: the pair survived every gate and carries a cost."""
        self._pairs[(i, j)].update(cost, last_stage=STAGE_ASSIGNMENT, outcome="LOST_ASSIGNMENT")

    def matched(self, i: int, j: int, method: str) -> None:
        """Stage 6: the pair won its component."""
        self._pairs[(i, j)].update(match_method=method, outcome="CONTINUE")
        self._matched_prev[i] = method

    def component(self, prevs: list[int], currs: list[int]) -> None:
        """Stage 6: the component (prev x curr) every costed pair was resolved in."""
        for i, j in self._pairs:
            if i in prevs and j in currs and self._pairs[(i, j)]["cost"] is not None:
                self._pairs[(i, j)].update(component_n_prev=len(prevs), component_n_curr=len(currs))

    def split_merge_test(self, test: TrackingSplitMergeTest) -> None:
        self._tests.append(test)

    def split_child(self, j: int) -> None:
        self._split_children.add(j)

    def merge_source(self, prev_uid: str) -> None:
        self._merge_sources.add(prev_uid)

    def born(self, j: int, uid: str) -> None:
        self._born_uids[j] = uid

    # ── freeze ─────────────────────────────────────────────────────────────

    def freeze(self) -> TrackingDecisions:
        self._rank_costs()
        candidates = tuple(
            TrackingCandidate(
                prev_cell_uid=self.prev[i].uid,
                prev_cell_label=self.prev[i].label,
                curr_cell_label=self.curr_labels[j],
                track_steps_before=self.prev[i].steps_before,
                **row,
            )
            for (i, j), row in sorted(self._pairs.items())
        )
        unmatched = tuple(self._unmatched_prev()) + tuple(self._unmatched_curr())
        rows = list(self._pairs.values())
        methods = list(self._matched_prev.values())
        frame = TrackingFrame(
            dt_s=self.dt_s,
            reset_code=self.reset_code,
            n_prev=len(self.prev),
            n_curr=len(self.curr_labels),
            n_pairs=len(rows),
            n_pass_overlap=sum(r["overlap_passed"] for r in rows),
            n_pass_kinematic=sum(r["cost"] is not None for r in rows),
            n_propagated=methods.count("PROPAGATED"),
            n_hungarian=methods.count("HUNGARIAN"),
            n_split=len(self._split_children),
            n_merge=len(self._merge_sources),
            n_initiation=len(self._born_uids) - len(self._split_children),
            n_termination=len(self.prev) - len(self._matched_prev),
        )
        return TrackingDecisions(frame, candidates, unmatched, tuple(self._tests))

    def _rank_costs(self) -> None:
        """Rank each previous cell's costed pairs, cheapest first (1-based)."""
        by_prev: dict[int, list[tuple[float, int]]] = {}
        for (i, j), row in self._pairs.items():
            if row["cost"] is not None:
                by_prev.setdefault(i, []).append((row["cost"], j))
        for i, costs in by_prev.items():
            for rank, (_, j) in enumerate(sorted(costs), start=1):
                self._pairs[(i, j)]["cost_rank"] = rank

    def _best(self, rows: list[tuple[int, dict]]) -> tuple[int | None, str | None]:
        if not rows:
            return None, None
        label, row = max(rows, key=lambda item: _STAGE_ORDER[item[1]["last_stage"]])
        return label, row["last_stage"]

    def _reason(self, rows: list[tuple[int, dict]]) -> str:
        if self.reset_code is not None:
            return self.reset_code if self.reset_code == "FIRST_SCAN" else "RESET"
        if not rows:
            return "NO_CANDIDATE"
        stages = {row["last_stage"] for _, row in rows}
        if STAGE_ASSIGNMENT in stages:
            return "LOST_ASSIGNMENT"
        if STAGE_KINEMATIC in stages:
            return "ALL_REJECTED_KINEMATIC"
        return "ALL_REJECTED_OVERLAP"

    def _unmatched_prev(self):
        for i, cell in enumerate(self.prev):
            if i in self._matched_prev:
                continue
            rows = [(self.curr_labels[j], r) for (p, j), r in self._pairs.items() if p == i]
            best_label, best_stage = self._best(rows)
            reason = "MERGE_SOURCE" if cell.uid in self._merge_sources else self._reason(rows)
            yield TrackingUnmatched(
                "prev", cell.uid, cell.label, len(rows), best_label, best_stage, reason
            )

    def _unmatched_curr(self):
        for j, uid in sorted(self._born_uids.items()):
            rows = [(self.prev[i].label, r) for (i, c), r in self._pairs.items() if c == j]
            best_label, best_stage = self._best(rows)
            reason = "SPLIT_CHILD" if j in self._split_children else self._reason(rows)
            yield TrackingUnmatched(
                "curr", uid, self.curr_labels[j], len(rows), best_label, best_stage, reason
            )
