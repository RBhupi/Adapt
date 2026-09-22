# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Decision records: what the segmenter and the tracker considered, and why.

These are analysis-only records written to a separate database
(``decisions.db``). The pipeline never reads them and the public API does not
expose them; they exist so a run can be explained after the fact and gate
thresholds re-tuned offline from stored numbers. Identity (run, scan, time)
is stamped by persistence, never carried here.
"""

from dataclasses import dataclass

from adapt.contracts.pipeline import require

# ── Tracking ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TrackingFrame:
    """One scan pair: how the frame was classified and what each stage yielded."""

    dt_s: float | None
    reset_code: str | None  # FIRST_SCAN | NON_MONOTONIC_TIME | TRACK_GAP_EXCEEDED | NO_PROJECTIONS
    n_prev: int
    n_curr: int
    n_pairs: int
    n_pass_overlap: int
    n_pass_kinematic: int
    n_propagated: int
    n_hungarian: int
    n_split: int
    n_merge: int
    n_initiation: int
    n_termination: int


@dataclass(frozen=True)
class TrackingCandidate:
    """One (previous cell, current cell) pair that candidate generation produced.

    ``last_stage`` is the furthest stage the pair reached (OVERLAP, KINEMATIC,
    ASSIGNMENT); ``outcome`` is CONTINUE, REJECTED_OVERLAP, REJECTED_KINEMATIC
    or LOST_ASSIGNMENT. Fields of stages the pair never reached are None.
    """

    prev_cell_uid: str
    prev_cell_label: int
    curr_cell_label: int
    track_steps_before: int
    hull_area_px: int
    hull_centroid_x: float
    hull_centroid_y: float
    curr_area_px: int
    curr_centroid_x: float
    curr_centroid_y: float
    intersection_px: int
    opc: float
    ocp: float
    overlap_passed: bool
    speed_ms: float | None
    previous_speed_ms: float | None
    accel_cap_ms: float | None
    kinematic_code: str | None
    displacement_m: float | None
    length_scale_m: float | None
    heading_change_deg: float | None
    cost: float | None
    component_n_prev: int | None
    component_n_curr: int | None
    cost_rank: int | None
    match_method: str | None
    last_stage: str
    outcome: str


@dataclass(frozen=True)
class TrackingUnmatched:
    """A previous cell that did not continue, or a current cell that was born."""

    side: str  # prev | curr
    cell_uid: str
    cell_label: int
    n_candidates: int
    best_candidate_label: int | None
    best_stage: str | None
    reason: str  # NO_CANDIDATE | ALL_REJECTED_OVERLAP | ALL_REJECTED_KINEMATIC |
    # LOST_ASSIGNMENT | SPLIT_CHILD | MERGE_SOURCE | RESET | FIRST_SCAN
    # How close the best candidate came, so a near-miss is distinguishable from
    # no candidate at all without re-joining tracking_candidates.
    best_opc: float | None = None
    best_ocp: float | None = None
    best_cost: float | None = None


@dataclass(frozen=True)
class TrackingSplitMergeTest:
    """Every hull overlap tested — pass or fail.

    MERGE compares ``hull_fraction`` against the merge threshold, SPLIT compares
    ``cell_fraction`` against the split one; ``overlap_fraction`` always holds
    whichever was used for the decision.
    """

    kind: str  # SPLIT | MERGE
    continuing_cell_uid: str
    tested_cell_label: int
    overlap_fraction: float  # the fraction actually compared against `threshold`
    threshold: float
    passed: bool
    # Both normalisations plus the raw areas, so the choice of denominator stays
    # auditable: MERGE decides on `hull_fraction`, SPLIT on `cell_fraction`.
    intersection_px: int | None = None
    hull_px: int | None = None
    tested_px: int | None = None
    hull_fraction: float | None = None
    cell_fraction: float | None = None


@dataclass(frozen=True)
class TrackingDecisions:
    frame: TrackingFrame
    candidates: tuple[TrackingCandidate, ...]
    unmatched: tuple[TrackingUnmatched, ...]
    split_merge_tests: tuple[TrackingSplitMergeTest, ...]


# ── Segmentation ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SegmentationFrame:
    """One scan: mask, seeds, carried claims and what survived the size filter."""

    mask_px: int
    closed_mask_px: int
    n_components: int
    n_independent_seeds: int
    n_carried_claims: int
    n_carried_admitted: int
    n_basins: int
    n_dropped_small: int
    n_dropped_large: int
    n_cells: int
    # Carried-seed basins the minimum-size filter spared (0 when the exemption
    # is off), so the rescue's contribution can be separated from the filter's.
    n_size_exempt: int = 0


@dataclass(frozen=True)
class SegmentationSeed:
    """One watershed marker — independent or carried, admitted or not.

    ``decision`` is SEEDED for markers used, else why a carried claim was not:
    SURVIVOR (its footprint already holds a seed), EXPIRED (past the carry
    bound), OUTSIDE_MASK, NO_DEFICIT, TOO_CLOSE_TO_SEED, TOO_CLOSE_TO_MARKER.
    ``final_label`` is 0 when the size filter dropped the basin.
    """

    marker_id: int | None
    origin: str  # HMAXIMA | CARRIED
    row: int
    col: int
    peak_value: float | None
    plateau_px: int | None
    component_id: int | None
    prior_label: int | None
    prior_age: int | None
    footprint_px: int | None
    clearance_px: float | None
    footprint_holds_seed: bool | None
    component_claims: int | None
    component_seeds: int | None
    component_deficit: int | None
    decision: str
    basin_px: int | None
    final_label: int | None


@dataclass(frozen=True)
class SegmentationComponent:
    """One 8-connected component of the closed convective mask."""

    component_id: int
    area_px: int
    field_max: float
    field_min: float
    n_seeds: int
    n_claims: int
    deficit: int
    n_admitted: int
    n_final_labels: int


@dataclass(frozen=True)
class SegmentationDecisions:
    frame: SegmentationFrame
    seeds: tuple[SegmentationSeed, ...]
    components: tuple[SegmentationComponent, ...]


# ── Bound checks ─────────────────────────────────────────────────────────────


def check_tracking_decisions(decisions: TrackingDecisions) -> None:
    require(
        isinstance(decisions, TrackingDecisions), "tracking_decisions must be TrackingDecisions"
    )
    frame = decisions.frame
    require(
        frame.n_termination <= frame.n_prev,
        "tracking_decisions: more terminations than previous cells",
    )
    require(
        len(decisions.candidates) == frame.n_pairs,
        f"tracking_decisions: {len(decisions.candidates)} candidate rows for "
        f"{frame.n_pairs} generated pairs",
    )


def check_segmentation_decisions(decisions: SegmentationDecisions) -> None:
    require(
        isinstance(decisions, SegmentationDecisions),
        "detection_decisions must be SegmentationDecisions",
    )
    frame = decisions.frame
    require(
        len(decisions.components) == frame.n_components,
        f"detection_decisions: {len(decisions.components)} component rows for "
        f"{frame.n_components} components",
    )
    require(
        frame.n_cells <= frame.n_basins,
        "detection_decisions: more cells than basins",
    )
