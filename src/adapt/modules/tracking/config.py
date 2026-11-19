# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Options and config of the tracking module.

``TrackingOptions`` is what a pipeline file sets under
``params: {tracking: ...}``: the rule parameters of tracker v2 and the width of
the cell uids. ``TrackingConfig`` is exactly what CellTracker consumes: the
options, plus what the node's ``configure`` adds for the run. Frozen. Only
pydantic + stdlib.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrackingOptions(BaseModel):
    """Cell tracking options."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    cell_threshold: float = Field(
        30.0,
        description="Intensity at the cell edge (field units). The excess e = polarity·(f − "
        "cell_threshold)₊ weights the cell centre (by e²) and the identity score",
    )
    polarity: Literal[1, -1] = Field(
        1,
        description="+1 when cells are above the threshold (reflectivity), −1 when below "
        "(brightness temperature)",
    )
    growth_max_km: float = Field(
        2.0,
        ge=0.0,
        description="Growth r_max (km) of the smallest footprints and cells: "
        "r = r_max·(1 − √A/ℓ0)₊ evens out the one-grid-cell registration error across cell sizes",
    )
    growth_size_km: float = Field(
        12.0, gt=0.0, description="Size ℓ0 (km, √area) at and above which cells are not grown"
    )
    max_overlap_mismatch: float = Field(
        0.6,
        gt=0.0,
        le=1.0,
        description="Curved overlap gate u_max: a pair is a candidate when u = 1 − √(O^c·O^h) "
        "on the grown shapes is at most this",
    )
    max_link_cost: float = Field(
        1.8,
        gt=0.0,
        description="Cost ceiling c_max: the cost of leaving a cell unlinked; a link is accepted "
        "only if its cost c = u + w_d·d + h is at most this",
    )
    residual_weight: float = Field(
        0.25,
        ge=0.0,
        description="w_d on the shape-aware centre residual d; 0.25 makes w_d·d the displacement "
        "over the diameter for a round cell",
    )
    heading_weight: float = Field(
        1.0,
        ge=0.0,
        description="w_h on the heading term w_h·(1 − cos Δθ)/2, applied when both steps exceed "
        "two grid lengths",
    )
    max_speed_ms: float = Field(40.0, gt=0.0, description="Hard speed cap v_max (m/s) of any link")
    max_acceleration_ms2: float = Field(
        0.04,
        ge=0.0,
        description="a_max (m/s²) in the additive speed-change gate "
        "v ≤ v_prev + a_max·Δt + 2√2·σ_x/Δt (σ_x = one grid length)",
    )
    split_overlap_threshold: float = Field(
        0.65,
        gt=0.0,
        le=1.0,
        description="τ_s: an orphan is a split child when a continuing parent's grown footprint "
        "covers at least this share of it (O^c)",
    )
    merge_overlap_threshold: float = Field(
        0.7,
        gt=0.0,
        le=1.0,
        description="τ_m: an unlinked cell merged into a continuing cell covering at least this "
        "share of its grown footprint (O^h)",
    )
    latent_scans: int = Field(
        2,
        ge=0,
        description="N: a track that ends or merges away is carried by the flow for up to N scans "
        "and may be resumed; 0 or 1 ends it at once",
    )
    identity_intensity_weight: float = Field(
        1.0,
        ge=0.0,
        description="γ in the identity score S = log A + γ·log ē (1: intensity mass, 0: area)",
    )
    identity_score_margin: float = Field(
        0.33,
        ge=0.0,
        description="δ_S: scores closer than this are within noise and the identity follows the "
        "candidate nearer the predicted position",
    )
    core_field_threshold: float = Field(
        40.0, description="Field threshold for the diagnostic core-area output (not a decision)"
    )
    uid_width: int = Field(10, ge=1, description="Characters in each base-36 cell_uid")


class TrackingConfig(TrackingOptions):
    """The options, and what the node's ``configure`` adds for the run."""

    field_var: str  # the field the run tracks
    max_tracking_gap_minutes: float  # the run's largest scan gap tracked across
