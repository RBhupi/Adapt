# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The engine: a thin shell around the pure rules.

Its state is exactly one field — the currently observed target — which it is
given when built and offers afterwards (``current_target``), so an engine per
scan decides as one engine over every scan would. All time comes from snapshots
(never the wall clock), so identical snapshot sequences produce identical
decisions.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from adapt.modules.target_selection.config import TSEConfig
from adapt.modules.target_selection.rules import (
    cap_seconds,
    continue_or_switch,
    is_candidate,
    select_best,
    should_stop,
    total_score,
)
from adapt.modules.target_selection.selection import (
    Decision,
    RankedCandidate,
    SelectionReason,
    StopReason,
    TargetSelection,
)
from adapt.modules.target_selection.snapshot import CellSnapshot, Snapshot


@dataclass(frozen=True)
class CurrentTarget:
    """The engine's only state: which cell is being observed since when."""

    cell_uid: str
    started_at: datetime


class TargetSelectionEngine:
    def __init__(self, config: TSEConfig, current: CurrentTarget | None = None) -> None:
        self._config = config
        self._current = current

    @property
    def current_target(self) -> CurrentTarget | None:
        return self._current

    def decide(self, snapshot: Snapshot) -> Decision:
        """Apply the science rules to one snapshot, update state, and say what happened."""
        previous = self._current
        candidates = {c.uid: c for c in snapshot.cells if is_candidate(c, self._config.entry)}
        scored = {uid: total_score(c, self._config) for uid, c in candidates.items()}
        stop: StopReason | None = None

        if previous is None:
            selection = self._start_new(snapshot, candidates, scored, exclude=())
        else:
            current_cell = _find(snapshot.cells, previous.cell_uid)
            observed = (snapshot.scan_time - previous.started_at).total_seconds()
            stop = should_stop(current_cell, self._config, observed)
            if stop is not None:
                # A max-duration target would immediately win again on score;
                # exclude it from re-selection within this call only.
                exclude = (previous.cell_uid,) if stop is StopReason.MAX_DURATION else ()
                self._current = None
                selection = self._start_new(snapshot, candidates, scored, exclude=exclude)
            else:
                # A held target may have left the entry set (hold gates are looser
                # than entry gates); it is still scored so challengers are judged
                # against it.
                assert current_cell is not None
                if previous.cell_uid not in scored:
                    candidates = {**candidates, previous.cell_uid: current_cell}
                    scored = {**scored, previous.cell_uid: total_score(current_cell, self._config)}
                selection = self._continue(snapshot, previous, candidates, scored)

        return Decision(
            scan_time=snapshot.scan_time,
            selection=selection,
            stop_reason=stop,
            previous_cell_uid=None if previous is None else previous.cell_uid,
            candidates=_ranked(scored),
        )

    def _continue(
        self,
        snapshot: Snapshot,
        current: CurrentTarget,
        candidates: dict[str, CellSnapshot],
        scored: dict[str, float],
    ) -> TargetSelection:
        if self._config.switch.allow:
            uid, reason = continue_or_switch(current.cell_uid, scored, self._config.switch.margin)
        else:
            uid, reason = current.cell_uid, SelectionReason.CONTINUATION
        if reason is SelectionReason.SWITCH:
            current = CurrentTarget(uid, snapshot.scan_time)
            self._current = current
        return self._selection(
            snapshot, candidates[uid], scored[uid], reason, started_at=current.started_at
        )

    def _start_new(
        self,
        snapshot: Snapshot,
        candidates: dict[str, CellSnapshot],
        scored: dict[str, float],
        *,
        exclude: tuple[str, ...],
    ) -> TargetSelection | None:
        eligible = {uid: s for uid, s in scored.items() if uid not in exclude}
        uid = select_best(eligible)
        if uid is None:
            return None
        self._current = CurrentTarget(uid, snapshot.scan_time)
        return self._selection(
            snapshot,
            candidates[uid],
            eligible[uid],
            SelectionReason.NEW_TARGET,
            started_at=self._current.started_at,
        )

    def _selection(
        self,
        snapshot: Snapshot,
        cell: CellSnapshot,
        score: float,
        reason: SelectionReason,
        *,
        started_at: datetime,
    ) -> TargetSelection:
        cap = cap_seconds(self._config.duration)
        window_end = None if cap is None else started_at + timedelta(seconds=cap)
        return TargetSelection(
            cell_uid=cell.uid,
            reason=reason,
            score=score,
            selection_time=snapshot.scan_time,
            trajectory=cell.trajectory,
            observation_window=(snapshot.scan_time, window_end),
        )


def _ranked(scored: dict[str, float]) -> tuple[RankedCandidate, ...]:
    """Candidates best first; ties break to the smallest uid, as in select_best."""
    order = sorted(scored, key=lambda uid: (-scored[uid], uid))
    return tuple(RankedCandidate(uid, scored[uid]) for uid in order)


def _find(cells: Iterable[CellSnapshot], uid: str) -> CellSnapshot | None:
    return next((c for c in cells if c.uid == uid), None)
