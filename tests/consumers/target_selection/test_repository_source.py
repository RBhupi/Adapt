# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""build_snapshot against a synthetic on-disk store.

The tracking rows are written as the pipeline writes them (frozen first-frame
schema — the projection columns freeze with the first stats frame), and read
back through the real StoreClient. No hand-copied DDL.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest

from adapt.api.store_client import StoreClient
from adapt.consumers.target_selection.repository_source import build_snapshot
from adapt.persistence.errors import StoreError
from adapt.persistence.store import Store, init_store
from adapt.persistence.store_registry import RunStart, StoreRegistry
from tests.helpers.tracking_store import TrackedScans

pytestmark = pytest.mark.unit

RUN_ID = "2024JUN01-1200-KDIX"
COLLECTION = "KDIX"
T0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 1, 14, 0, tzinfo=UTC)  # 120 min after T0

_STATS_COLUMNS = [
    "cell_label",
    "cell_area_sqkm",
    "cell_centroid_mass_lat",
    "cell_centroid_mass_lon",
    "radar_reflectivity_max",
    "cell_centroid_projection1_lat",
    "cell_centroid_projection1_lon",
    "cell_centroid_projection2_lat",
    "cell_centroid_projection2_lon",
]


def _stats(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=_STATS_COLUMNS)


def _tracked(pairs) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cell_label": [label for label, _ in pairs],
            "cell_uid": [uid for _, uid in pairs],
            "area": [1.0] * len(pairs),
            "max_reflectivity": [40.0] * len(pairs),
        }
    )


def _build_store(tmp_path):
    root = init_store(tmp_path / "store")
    store = Store.open(root)
    collection = store.collection(COLLECTION)
    registry = StoreRegistry.get_instance(root)
    registry.register_collection(COLLECTION, source_kind="nexrad")
    registry.begin_run(
        RunStart(
            run_id=RUN_ID,
            collection_id=COLLECTION,
            config_hash="h",
            config_json='{"global_": {"tracking_field": "reflectivity"}}',
            pipeline_version="0",
            environment_json="{}",
        )
    )
    scans = TrackedScans(collection, RUN_ID)
    # T0: alpha (no projections), beta (area 100), delta (projections, only scan)
    scans.write(
        "sid-t0",
        T0,
        stats=_stats(
            [
                (1, 30.0, 34.8, -97.3, 42.0, None, None, None, None),
                (2, 100.0, 35.0, -97.0, 48.1, None, None, None, None),
                (4, 60.0, 35.2, -97.1, 40.0, 35.25, -97.1, 35.3, -97.1),
            ]
        ),
        tracked=_tracked([(1, "uid_alpha"), (2, "uid_beta"), (4, "uid_delta")]),
    )
    # T1: beta grew to 160 with projections; gamma is new, no projections.
    scans.write(
        "sid-t1",
        T1,
        stats=_stats(
            [
                (2, 160.0, 35.05, -97.0, 55.0, 35.1, -97.0, 35.2, -97.0),
                (3, 50.0, 34.9, -97.2, 30.0, None, None, None, None),
            ]
        ),
        tracked=_tracked([(2, "uid_beta"), (3, "uid_gamma")]),
    )
    store.close()
    return root


@pytest.fixture
def store_root(tmp_path):
    return _build_store(tmp_path)


@pytest.fixture
def client(store_root):
    c = StoreClient(store_root)
    yield c
    c.close()


@pytest.fixture
def snapshot(client):
    return build_snapshot(client, RUN_ID, COLLECTION, growth_window_scans=4)


def _cell(snapshot, uid):
    return next(c for c in snapshot.cells if c.uid == uid)


def test_latest_scan_only(snapshot):
    assert snapshot.scan_time == T1
    # uid_alpha and uid_delta exist only at T0 and must not appear.
    assert {c.uid for c in snapshot.cells} == {"uid_beta", "uid_gamma"}


def test_growth_rate_slope(snapshot):
    # (160 - 100) km2 over 120 min = 0.5 km2/min.
    assert _cell(snapshot, "uid_beta").growth_rate_sqkm_per_min == pytest.approx(0.5)


def test_trajectory_lead_seconds(snapshot):
    trajectory = _cell(snapshot, "uid_beta").trajectory
    assert [p.lead_seconds for p in trajectory] == [7200.0, 14400.0]
    assert [p.lat for p in trajectory] == [35.1, 35.2]


def test_values_include_track_columns(snapshot):
    assert _cell(snapshot, "uid_beta").values["n_scans"] == 2


def test_null_projections_empty_trajectory(snapshot):
    assert _cell(snapshot, "uid_gamma").trajectory == ()


def test_single_scan_growth_zero(snapshot):
    assert _cell(snapshot, "uid_gamma").growth_rate_sqkm_per_min == 0.0


def test_no_rows_raises(client):
    # Unknown runs now fail at the provenance lookup (StoreError), before
    # any cells read — still loud, still names the run.
    with pytest.raises(StoreError, match="bogus"):
        build_snapshot(client, "bogus", COLLECTION, growth_window_scans=4)


def test_at_replays_earlier_scan(client):
    snap = build_snapshot(client, RUN_ID, COLLECTION, growth_window_scans=4, at=T0)

    assert snap.scan_time == T0
    # uid_gamma does not exist yet at T0; alpha and delta do.
    assert {c.uid for c in snap.cells} == {"uid_alpha", "uid_beta", "uid_delta"}
    beta = _cell(snap, "uid_beta")
    assert beta.growth_rate_sqkm_per_min == 0.0  # single scan of history at T0
    assert beta.trajectory == ()


def test_at_sees_the_track_summary_as_of_that_scan(client):
    # Replay must not read the future: at T0 beta has been seen once, although
    # the stored cell_tracks row already counts its later scan at T1.
    snap = build_snapshot(client, RUN_ID, COLLECTION, growth_window_scans=4, at=T0)
    beta = _cell(snap, "uid_beta")
    assert beta.values["n_scans"] == 1
    assert beta.values["duration_seconds"] == 0.0
    assert beta.values["max_area_sqkm"] == pytest.approx(100.0)
    assert beta.values["max_reflectivity"] == pytest.approx(48.1)


def test_end_of_run_summary_is_an_explicit_oracle(client):
    # Perfect-knowledge replay: the track summary as it stands at the end of the run.
    snap = build_snapshot(
        client, RUN_ID, COLLECTION, growth_window_scans=4, at=T0, track_summary="end_of_run"
    )
    assert _cell(snap, "uid_beta").values["n_scans"] == 2


def test_track_summary_spans_the_history_up_to_the_scan(snapshot):
    beta = _cell(snapshot, "uid_beta")
    assert beta.values["n_scans"] == 2
    assert beta.values["duration_seconds"] == pytest.approx(7200.0)
    assert beta.values["max_area_sqkm"] == pytest.approx(160.0)
    assert beta.values["max_reflectivity"] == pytest.approx(55.0)


def test_first_scan_projections_have_no_lead_times(client):
    # At the first scan of a run, projections exist but no scan cadence does:
    # the trajectory is empty (defined condition), not an error.
    snap = build_snapshot(client, RUN_ID, COLLECTION, growth_window_scans=4, at=T0)
    assert _cell(snap, "uid_delta").trajectory == ()


def test_at_before_first_scan_raises(client):
    with pytest.raises(ValueError, match="No scans at or before"):
        build_snapshot(
            client,
            RUN_ID,
            COLLECTION,
            growth_window_scans=4,
            at=datetime(2024, 6, 1, 11, 0, tzinfo=UTC),
        )
