# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from adapt.configuration.schemas.materialization import materialize_module_configs
from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig
from adapt.modules.tracking.module import RadarCellTracker


@pytest.fixture
def config():
    d = tempfile.mkdtemp()
    try:
        import shutil

        param = ParamConfig()
        param.tracker.split_overlap_threshold = 0.4
        user = UserConfig(base_dir=str(Path(d)), radar="TEST_RADAR")
        internal = resolve_config(param, user, None)
        return materialize_module_configs(internal)["tracking_config"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def tracker(config):
    return RadarCellTracker(config)


def _synthetic_ds(time, labels, refl=None, proj_labels=None):
    H, W = labels.shape
    if refl is None:
        refl = np.zeros((H, W), dtype=np.float32)
        refl[labels > 0] = 40.0
    if proj_labels is None:
        proj_labels = labels

    projections = np.stack([proj_labels.astype(np.int32)], axis=0)
    ds = xr.Dataset(
        {
            "cell_labels": (["y", "x"], labels.astype(np.int32)),
            "reflectivity": (["y", "x"], refl.astype(np.float32)),
            "cell_projections": (["frame_offset", "y", "x"], projections),
            "heading_x": (["y", "x"], np.zeros((H, W), dtype=np.float32)),
            "heading_y": (["y", "x"], np.zeros((H, W), dtype=np.float32)),
        },
        coords={
            "y": np.arange(H) * 1000.0,
            "x": np.arange(W) * 1000.0,
            "frame_offset": [0],
        },
    )
    return ds.assign_coords(time=time)


def _cell_stats(time, rows):
    data = []
    for r in rows:
        data.append(
            {
                "time": time,
                "time_volume_start": time,
                "cell_label": r["id"],
                "cell_area_sqkm": r["area"],
                "area_40dbz_km2": r.get("area40", r["area"]),
                "cell_centroid_geom_x": r["cx"],
                "cell_centroid_geom_y": r["cy"],
                "cell_centroid_mass_lat": r.get("lat", 35.0),
                "cell_centroid_mass_lon": r.get("lon", -97.0),
                "radar_reflectivity_mean": r["mean_refl"],
                "radar_reflectivity_max": r["max_refl"],
                "radar_differential_reflectivity_max": r.get("max_zdr", 1.0),
            }
        )
    return pd.DataFrame(data)


def test_birth_and_continue_events(tracker):
    t1 = np.datetime64("2024-01-01T12:00:00")
    t2 = np.datetime64("2024-01-01T12:05:00")

    labels1 = np.zeros((6, 6), dtype=np.int32)
    labels1[2:4, 2:4] = 1
    ds1 = _synthetic_ds(t1, labels1)
    stats1 = _cell_stats(
        t1,
        [
            {
                "id": 1,
                "area": 4.0,
                "cx": 2.5,
                "cy": 2.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            }
        ],
    )

    tracked1, events1 = tracker.track(ds1, stats1)
    assert len(tracked1) == 1
    assert "cell_uid" in tracked1.columns
    assert pd.notna(tracked1.iloc[0]["cell_uid"])
    uid1 = str(tracked1.iloc[0]["cell_uid"])
    assert len(events1[events1["event_type"] == "INITIATION"]) == 1

    labels2 = labels1.copy()
    ds2 = _synthetic_ds(t2, labels2, proj_labels=labels1)
    stats2 = _cell_stats(
        t2,
        [
            {
                "id": 1,
                "area": 4.0,
                "cx": 2.5,
                "cy": 2.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            }
        ],
    )

    tracked2, events2 = tracker.track(ds2, stats2)
    assert len(tracked2) == 1
    assert str(tracked2.iloc[0]["cell_uid"]) == uid1
    assert "source_cell_uid" in events2.columns
    assert "target_cell_uid" in events2.columns
    assert len(events2[events2["event_type"] == "CONTINUE"]) == 1
    assert len(events2[events2["event_type"] == "INITIATION"]) == 0


def test_split_event(tracker):
    t1 = np.datetime64("2024-01-01T12:00:00")
    t2 = np.datetime64("2024-01-01T12:05:00")

    labels1 = np.zeros((8, 8), dtype=np.int32)
    labels1[3:5, 2:6] = 1
    ds1 = _synthetic_ds(t1, labels1)
    stats1 = _cell_stats(
        t1,
        [
            {
                "id": 1,
                "area": 8.0,
                "cx": 3.5,
                "cy": 3.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            }
        ],
    )
    tracker.track(ds1, stats1)

    labels2 = np.zeros((8, 8), dtype=np.int32)
    labels2[3:5, 2:4] = 1
    labels2[3:5, 4:6] = 2
    proj = labels1.copy()
    ds2 = _synthetic_ds(t2, labels2, proj_labels=proj)
    stats2 = _cell_stats(
        t2,
        [
            {
                "id": 1,
                "area": 4.0,
                "cx": 2.5,
                "cy": 3.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            },
            {
                "id": 2,
                "area": 4.0,
                "cx": 4.5,
                "cy": 3.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            },
        ],
    )

    tracked2, events2 = tracker.track(ds2, stats2)
    assert len(tracked2) == 2
    assert len(events2[events2["event_type"] == "SPLIT"]) == 1
    assert tracked2["cell_uid"].nunique() == 2


def test_merge_event_emits_death(tracker):
    t1 = np.datetime64("2024-01-01T12:00:00")
    t2 = np.datetime64("2024-01-01T12:05:00")

    labels1 = np.zeros((10, 10), dtype=np.int32)
    labels1[4:6, 2:4] = 1
    labels1[4:6, 6:8] = 2
    ds1 = _synthetic_ds(t1, labels1)
    stats1 = _cell_stats(
        t1,
        [
            {
                "id": 1,
                "area": 4.0,
                "cx": 2.5,
                "cy": 4.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            },
            {
                "id": 2,
                "area": 4.0,
                "cx": 6.5,
                "cy": 4.5,
                "mean_refl": 40.0,
                "max_refl": 45.0,
            },
        ],
    )
    tracker.track(ds1, stats1)

    labels2 = np.zeros((10, 10), dtype=np.int32)
    labels2[4:6, 3:7] = 1
    proj = np.zeros((10, 10), dtype=np.int32)
    proj[4:6, 2:4] = 1
    proj[4:6, 6:8] = 2
    ds2 = _synthetic_ds(t2, labels2, proj_labels=proj)
    stats2 = _cell_stats(
        t2,
        [
            {
                "id": 1,
                "area": 8.0,
                "cx": 4.5,
                "cy": 4.5,
                "mean_refl": 45.0,
                "max_refl": 50.0,
            }
        ],
    )

    tracked2, events2 = tracker.track(ds2, stats2)
    assert len(tracked2) == 1
    assert len(events2[events2["event_type"] == "MERGE"]) == 1
    deaths = events2[events2["event_type"] == "TERMINATION"]
    assert len(deaths) >= 1
    assert deaths["source_cell_uid"].notna().any()


# ---------------------------------------------------------------------------
# dt-scaling and gap-survival tests
# ---------------------------------------------------------------------------


def _make_config(
    max_gap_minutes=10.0,
    expected_speed_ms=30.0,
    split_overlap=0.4,
    match_cost_threshold=0.15,
):
    import shutil

    d = tempfile.mkdtemp()
    try:
        param = ParamConfig()
        param.tracker.split_overlap_threshold = split_overlap
        param.tracker.max_gap_minutes = max_gap_minutes
        param.tracker.expected_speed_ms = expected_speed_ms
        param.tracker.match_cost_threshold = match_cost_threshold
        user = UserConfig(base_dir=str(Path(d)), radar="TEST_RADAR")
        internal = resolve_config(param, user, None)
        return materialize_module_configs(internal)["tracking_config"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _ds_no_projections(time, labels, refl=None):
    """Dataset with no cell_projections variable — simulates a missing scan."""
    H, W = labels.shape
    if refl is None:
        refl = np.zeros((H, W), dtype=np.float32)
        refl[labels > 0] = 40.0
    ds = xr.Dataset(
        {
            "cell_labels": (["y", "x"], labels.astype(np.int32)),
            "reflectivity": (["y", "x"], refl.astype(np.float32)),
            "heading_x": (["y", "x"], np.zeros((H, W), dtype=np.float32)),
            "heading_y": (["y", "x"], np.zeros((H, W), dtype=np.float32)),
        },
        coords={"y": np.arange(H) * 1000.0, "x": np.arange(W) * 1000.0},
    )
    return ds.assign_coords(time=time)


def test_dpos_scales_with_dt():
    """Shorter dt → higher D_pos → higher match cost for same displacement."""
    # Cell moves 2000 m (2 pixels at 1000 m/pixel).
    # expected_speed_ms=30 → max_displacement = 30 * dt_s.
    # dt=300 s: D_pos = 2000/9000 ≈ 0.222   (higher cost)
    # dt=600 s: D_pos = 2000/18000 ≈ 0.111  (lower cost)
    # match_cost_threshold=0 disables pre-clamping so raw cost is preserved in events
    cfg = _make_config(expected_speed_ms=30.0, match_cost_threshold=0.0)

    t0 = np.datetime64("2024-01-01T12:00:00")
    labels0 = np.zeros((8, 8), dtype=np.int32)
    labels0[2:4, 2:4] = 1
    stats0 = _cell_stats(
        t0,
        [{"id": 1, "area": 4.0, "cx": 2500.0, "cy": 2500.0, "mean_refl": 40.0, "max_refl": 45.0}],
    )

    # T1 cell moved 2 pixels right; proj_labels at new position → full IoU overlap
    labels1 = np.zeros((8, 8), dtype=np.int32)
    labels1[2:4, 4:6] = 1
    proj1 = labels1.copy()  # projection predicts exact new position
    stats1 = _cell_stats(
        None,  # time set per tracker below
        [{"id": 1, "area": 4.0, "cx": 4500.0, "cy": 2500.0, "mean_refl": 40.0, "max_refl": 45.0}],
    )

    def _run_tracker(t1):
        tracker = RadarCellTracker(cfg)
        tracker.track(_synthetic_ds(t0, labels0, proj_labels=labels0), stats0)
        s1 = stats1.copy()
        s1["time"] = t1
        s1["time_volume_start"] = t1
        _, events = tracker.track(_synthetic_ds(t1, labels1, proj_labels=proj1), s1)
        cont = events[events["event_type"] == "CONTINUE"]
        assert len(cont) == 1, f"Expected CONTINUE at {t1}"
        return float(cont.iloc[0]["cost"])

    t1_short = np.datetime64("2024-01-01T12:05:00")  # dt = 300 s
    t1_long = np.datetime64("2024-01-01T12:10:00")  # dt = 600 s

    cost_short = _run_tracker(t1_short)
    cost_long = _run_tracker(t1_long)

    assert cost_short > cost_long, (
        f"Expected cost_short ({cost_short:.4f}) > cost_long ({cost_long:.4f})"
    )


def test_track_survives_one_missing_scan():
    """A track whose scan is missing stays dormant; no TERMINATION emitted within gap."""
    cfg = _make_config(max_gap_minutes=15.0)
    tracker = RadarCellTracker(cfg)

    t0 = np.datetime64("2024-01-01T12:00:00")
    t1 = np.datetime64("2024-01-01T12:05:00")  # 5 min later — no projections
    t2 = np.datetime64("2024-01-01T12:10:00")  # 10 min from T0 — within 15-min gap

    labels = np.zeros((8, 8), dtype=np.int32)
    labels[2:4, 2:4] = 1
    stats = _cell_stats(
        t0,
        [{"id": 1, "area": 4.0, "cx": 2500.0, "cy": 2500.0, "mean_refl": 40.0, "max_refl": 45.0}],
    )

    _, events0 = tracker.track(_synthetic_ds(t0, labels, proj_labels=labels), stats)
    uid_t0 = str(events0[events0["event_type"] == "INITIATION"].iloc[0]["target_cell_uid"])

    # T1: no projections — should NOT terminate T0's track
    stats1 = stats.copy()
    stats1["time"] = t1
    stats1["time_volume_start"] = t1
    _, events1 = tracker.track(_ds_no_projections(t1, labels), stats1)

    terminations_t1 = events1[events1["event_type"] == "TERMINATION"]
    assert uid_t0 not in terminations_t1["source_cell_uid"].values, (
        "Track was terminated at T1 despite being within the gap window"
    )

    # T2: valid projections — T0's track is still dormant (10 min < 15 min gap)
    # T1 created a new track with label=1; use that as proj context
    labels2 = labels.copy()
    stats2 = stats.copy()
    stats2["time"] = t2
    stats2["time_volume_start"] = t2
    _, events2 = tracker.track(_synthetic_ds(t2, labels2, proj_labels=labels), stats2)

    terminations_t2 = events2[events2["event_type"] == "TERMINATION"]
    assert uid_t0 not in terminations_t2["source_cell_uid"].values, (
        "Track was terminated at T2 despite gap (10 min) < max_gap (15 min)"
    )


def test_track_terminated_after_gap_exceeded():
    """A dormant track is terminated when the gap exceeds max_gap_minutes."""
    cfg = _make_config(max_gap_minutes=5.0)  # 5-minute gap limit
    tracker = RadarCellTracker(cfg)

    t0 = np.datetime64("2024-01-01T12:00:00")
    t1 = np.datetime64("2024-01-01T12:03:00")  # 3 min later — no projections (gap starts)
    t2 = np.datetime64("2024-01-01T12:10:00")  # 10 min from T0 — exceeds 5-min gap

    labels = np.zeros((8, 8), dtype=np.int32)
    labels[2:4, 2:4] = 1
    stats = _cell_stats(
        t0,
        [{"id": 1, "area": 4.0, "cx": 2500.0, "cy": 2500.0, "mean_refl": 40.0, "max_refl": 45.0}],
    )

    _, events0 = tracker.track(_synthetic_ds(t0, labels, proj_labels=labels), stats)
    uid_t0 = str(events0[events0["event_type"] == "INITIATION"].iloc[0]["target_cell_uid"])

    # T1: no projections — moves T0's track to dormant (last_seen = epoch(T0))
    stats1 = stats.copy()
    stats1["time"] = t1
    stats1["time_volume_start"] = t1
    tracker.track(_ds_no_projections(t1, labels), stats1)

    # T2: valid projections — gap from T0 to T2 is 10 min > 5-min limit → TERMINATION
    labels2 = labels.copy()
    stats2 = stats.copy()
    stats2["time"] = t2
    stats2["time_volume_start"] = t2
    _, events2 = tracker.track(_synthetic_ds(t2, labels2, proj_labels=labels), stats2)

    terminations = events2[events2["event_type"] == "TERMINATION"]
    assert uid_t0 in terminations["source_cell_uid"].values, (
        "Expected TERMINATION for dormant track after gap exceeded max_gap_minutes"
    )
