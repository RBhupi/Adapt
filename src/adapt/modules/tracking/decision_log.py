# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Per-scan log of the tracker's decisions.

``ScanLog`` collects, step by step, the records of one ``CellTracker.track``
call and freezes them into ``TrackingDecisions`` — the analysis-only record
persisted to ``decisions.db``. Mutable while the scan is tracked, immutable
afterwards.
"""

from collections import Counter
from dataclasses import dataclass, field

from adapt.contracts import (
    DecisionTableWrite,
    TrackingCell,
    TrackingDecisions,
    TrackingIdentity,
    TrackingLatent,
    TrackingLineage,
    TrackingPair,
    TrackingScan,
)
from adapt.utils.frames import record_integer_columns

__all__ = ["DECISION_TABLES", "ScanLog"]

_KEY = "tracking_decisions"

# The tables of decisions.db that hold each part of a ``TrackingDecisions``.
DECISION_TABLES = (
    DecisionTableWrite(
        key=_KEY,
        records="scan",
        table="tracking_scans",
        primary_key=("run_id", "scan_id"),
        integer_columns=record_integer_columns(TrackingScan),
    ),
    DecisionTableWrite(
        key=_KEY,
        records="cells",
        table="tracking_cells",
        primary_key=("run_id", "scan_id", "cell_label"),
        index_columns=("cell_uid",),
        integer_columns=record_integer_columns(TrackingCell),
    ),
    DecisionTableWrite(
        key=_KEY,
        records="pairs",
        table="tracking_pairs",
        primary_key=("run_id", "scan_id", "prev_cell_uid", "curr_cell_label"),
        index_columns=("prev_cell_uid",),
        integer_columns=record_integer_columns(TrackingPair),
    ),
    DecisionTableWrite(
        key=_KEY,
        records="lineage",
        table="tracking_lineage",
        primary_key=("run_id", "scan_id", "seq"),
        integer_columns=("seq", *record_integer_columns(TrackingLineage)),
        sequenced=True,
    ),
    DecisionTableWrite(
        key=_KEY,
        records="identity",
        table="tracking_identity",
        primary_key=(
            "run_id",
            "scan_id",
            "kind",
            "identity_uid",
            "candidate_side",
            "candidate_label",
        ),
        integer_columns=record_integer_columns(TrackingIdentity),
    ),
    DecisionTableWrite(
        key=_KEY,
        records="latent",
        table="tracking_latent",
        primary_key=("run_id", "scan_id", "cell_uid"),
        integer_columns=record_integer_columns(TrackingLatent),
    ),
)


@dataclass
class ScanLog:
    """Builder for one scan's ``TrackingDecisions``.

    ``counts`` holds the scan totals the records alone do not give
    (previous and latent tracks, components, crossing exclusions, merges,
    identity transfers, latent creations, terminations).
    """

    dt_s: float | None
    reset_code: str | None
    counts: Counter = field(default_factory=Counter)
    pairs: dict[tuple[str, int], dict] = field(default_factory=dict)
    cells: dict[int, TrackingCell] = field(default_factory=dict)
    lineage: list[TrackingLineage] = field(default_factory=list)
    identity: list[TrackingIdentity] = field(default_factory=list)
    latent: list[TrackingLatent] = field(default_factory=list)

    def pair(self, uid: str, label: int, **values) -> None:
        self.pairs[(uid, label)] = {
            "component_id": None,
            "cost_rank": None,
            "margin": None,
            "crossing_with_uid": None,
            **values,
        }

    def update_pair(self, uid: str, label: int, **values) -> None:
        self.pairs[(uid, label)].update(values)

    def freeze(self) -> TrackingDecisions:
        self._rank_costs()
        pairs = tuple(
            TrackingPair(prev_cell_uid=uid, curr_cell_label=label, **row)
            for (uid, label), row in sorted(self.pairs.items())
        )
        gates = Counter(p.gate for p in pairs)
        fates = Counter(c.fate for c in self.cells.values())
        scan = TrackingScan(
            dt_s=self.dt_s,
            reset_code=self.reset_code,
            n_prev=self.counts["prev"],
            n_latent=self.counts["latent"],
            n_curr=len(self.cells),
            n_pairs=len(pairs),
            n_pass_overlap=len(pairs) - gates["OVERLAP"],
            n_pass_speed=gates["PASS"] + gates["COST"],
            n_pass_cost=gates["PASS"],
            n_components=self.counts["components"],
            n_continue=fates["CONTINUE"],
            n_crossing_excluded=self.counts["crossing_excluded"],
            n_split=fates["SPLIT_CHILD"],
            n_merge=self.counts["merge"],
            n_resumed=fates["RESUMED"],
            n_identity_transfer=self.counts["identity_transfer"],
            n_initiation=fates["INITIATION"],
            n_latent_created=self.counts["latent_created"],
            n_termination=self.counts["termination"],
        )
        return TrackingDecisions(
            scan,
            tuple(self.cells[k] for k in sorted(self.cells)),
            pairs,
            tuple(self.lineage),
            tuple(self.identity),
            tuple(self.latent),
        )

    def _rank_costs(self) -> None:
        """Rank each footprint's admissible pairs, cheapest first (1-based)."""
        by_prev: dict[str, list[tuple[float, int]]] = {}
        for (uid, label), row in self.pairs.items():
            if row["gate"] == "PASS":
                by_prev.setdefault(uid, []).append((row["cost"], label))
        for uid, costs in by_prev.items():
            for rank, (_, label) in enumerate(sorted(costs), start=1):
                self.pairs[(uid, label)]["cost_rank"] = rank
