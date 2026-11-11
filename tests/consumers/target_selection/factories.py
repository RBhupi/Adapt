# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Synthetic factories shared by the target-selection tests."""

from datetime import datetime

from adapt.consumers.target_selection.config import TSEConfig
from adapt.consumers.target_selection.selection import Decision, ScanDecision, TargetSelection
from adapt.consumers.target_selection.snapshot import CellSnapshot, TrajectoryPoint


def make_cell(
    uid: str = "a",
    *,
    lat: float = 35.0,
    lon: float = -97.0,
    area: float = 100.0,
    refl: float = 50.0,
    age: float = 700.0,
    growth: float = 0.0,
    trajectory: tuple[TrajectoryPoint, ...] = (),
    values: dict[str, float] | None = None,
) -> CellSnapshot:
    return CellSnapshot(
        uid=uid,
        lat=lat,
        lon=lon,
        area_sqkm=area,
        field_max=refl,
        age_seconds=age,
        growth_rate_sqkm_per_min=growth,
        trajectory=trajectory,
        values={
            "n_scans": 5.0,
            "age_seconds": age,
            "cell_area_sqkm": area,
            "field_max": refl,
            "growth_rate_sqkm_per_min": growth,
            **(values or {}),
        },
    )


def make_config(
    *,
    gates: tuple[dict, ...] = ({"field": "n_scans", "op": "ge", "value": 3.0},),
    min_age_seconds: float = 600.0,
    hold_gates: tuple[dict, ...] | None = None,
    w_reflectivity: float = 1.0,
    w_area: float = 0.05,
    w_growth: float = 2.0,
    projection_steps: int = 5,
    sites: tuple[dict, ...] = (),
    allow_switch: bool = True,
    switch_margin: float = 5.0,
    max_observation_seconds: float | None = 1800.0,
    growth_window_scans: int = 4,
) -> TSEConfig:
    """A five-part rule: ``gates`` plus an age floor as the entry gates, the same
    gates holding the target unless ``hold_gates`` says otherwise."""
    entry = [*gates]
    if min_age_seconds > 0:
        entry.append({"field": "age_seconds", "op": "ge", "value": min_age_seconds})
    return TSEConfig.model_validate(
        {
            "entry": {"gates": entry},
            "score": {
                "weights": {
                    "field_max": w_reflectivity,
                    "cell_area_sqkm": w_area,
                    "growth_rate_sqkm_per_min": w_growth,
                },
                "site_preference": {"projection_steps": projection_steps, "sites": list(sites)},
            },
            "hold": {"gates": entry if hold_gates is None else list(hold_gates)},
            "switch": {"allow": allow_switch, "margin": switch_margin},
            "duration": {
                "max_minutes": None
                if max_observation_seconds is None
                else max_observation_seconds / 60.0
            },
            "snapshot": {"growth_window_scans": growth_window_scans},
        }
    )


def make_scan_decision(
    collection: str,
    run_id: str,
    scan_id: str,
    scan_time: datetime,
    selection: TargetSelection | None,
) -> ScanDecision:
    """A decision with no stop, no previous target and no ranked field — enough
    to write a current-selection file naming ``selection``'s cell (or none)."""
    return ScanDecision(
        collection,
        run_id,
        scan_id,
        "0123456789abcdef",
        Decision(scan_time, selection, None, None, ()),
    )
