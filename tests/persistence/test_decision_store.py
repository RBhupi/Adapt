# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""decisions.db: bundles land in their tables with run/scan stamps, replace on re-write,
and the router dispatches ``DecisionTableWrite`` there without touching products."""

import sqlite3
from datetime import UTC, datetime

import pytest

from adapt.contracts import (
    DecisionTableWrite,
    PersistenceMeta,
    SegmentationComponent,
    SegmentationDecisions,
    SegmentationFrame,
    SegmentationSeed,
    TrackingCandidate,
    TrackingDecisions,
    TrackingFrame,
    TrackingUnmatched,
)
from adapt.persistence.decision_store import DecisionStore
from adapt.persistence.output_router import StoreOutputRouter
from adapt.persistence.store import Store, init_store

pytestmark = pytest.mark.unit

SCAN_TIME = datetime(2026, 8, 13, 17, 31, 58, tzinfo=UTC)
META = PersistenceMeta(
    scan_time=SCAN_TIME, scan_id="abc123", run_id="run-1", source_file="f", collection_id="KILX"
)


def _tracking() -> TrackingDecisions:
    frame = TrackingFrame(270.0, None, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0)
    candidate = TrackingCandidate(
        prev_cell_uid="AAAA",
        prev_cell_label=1,
        curr_cell_label=1,
        track_steps_before=0,
        hull_area_px=4,
        hull_centroid_x=2500.0,
        hull_centroid_y=2500.0,
        curr_area_px=4,
        curr_centroid_x=3500.0,
        curr_centroid_y=2500.0,
        intersection_px=2,
        opc=0.5,
        ocp=0.5,
        overlap_passed=True,
        speed_ms=3.7,
        previous_speed_ms=None,
        accel_cap_ms=None,
        kinematic_code=None,
        displacement_m=1000.0,
        length_scale_m=2256.0,
        heading_change_deg=None,
        cost=0.94,
        component_n_prev=1,
        component_n_curr=1,
        cost_rank=1,
        match_method="PROPAGATED",
        last_stage="ASSIGNMENT",
        outcome="CONTINUE",
    )
    return TrackingDecisions(frame, (candidate,), (), ())


def _segmentation() -> SegmentationDecisions:
    frame = SegmentationFrame(10, 10, 1, 1, 1, 0, 1, 0, 0, 1)
    seeds = (
        SegmentationSeed(1, "HMAXIMA", 5, 3, 48.0, 1, 1, *([None] * 8), "SEEDED", 10, 1),
        SegmentationSeed(
            None,
            "CARRIED",
            5,
            7,
            None,
            None,
            1,
            1,
            0,
            9,
            4.0,
            False,
            1,
            1,
            0,
            "NO_DEFICIT",
            None,
            None,
        ),
    )
    components = (SegmentationComponent(1, 10, 48.0, 40.0, 1, 1, 0, 0, 1),)
    return SegmentationDecisions(frame, seeds, components)


@pytest.fixture
def collection(tmp_path):
    store = Store.open(init_store(tmp_path / "store"))
    coll = store.collection("KILX")
    yield coll
    store.close()


def _rows(path, sql):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_tracking_bundle_lands_in_its_tables_with_stamps(collection):
    with DecisionStore(collection.decisions_path) as store:
        store.write(_tracking(), META)
    (frame,) = _rows(collection.decisions_path, "select * from tracking_frames")
    (cand,) = _rows(collection.decisions_path, "select * from tracking_candidates")
    assert (frame["run_id"], frame["scan_id"], frame["n_prev"]) == ("run-1", "abc123", 1)
    assert frame["scan_time"].startswith("2026-08-13T17:31:58")
    assert (cand["prev_cell_uid"], cand["outcome"], cand["overlap_passed"]) == (
        "AAAA",
        "CONTINUE",
        1,
    )
    assert cand["previous_speed_ms"] is None


def test_segmentation_bundle_numbers_seeds_in_order(collection):
    with DecisionStore(collection.decisions_path) as store:
        store.write(_segmentation(), META)
    seeds = _rows(collection.decisions_path, "select * from segmentation_seeds order by seq")
    assert [s["seq"] for s in seeds] == [0, 1]
    assert [s["decision"] for s in seeds] == ["SEEDED", "NO_DEFICIT"]
    assert seeds[1]["footprint_holds_seed"] == 0 and seeds[1]["final_label"] is None


def test_rewriting_a_scan_replaces_its_rows(collection):
    with DecisionStore(collection.decisions_path) as store:
        store.write(_tracking(), META)
        store.write(_tracking(), META)
    assert len(_rows(collection.decisions_path, "select * from tracking_candidates")) == 1


def test_router_dispatches_decision_writes_without_a_scan_product(collection):
    class _Module:
        name = "tracking"
        persistence = (DecisionTableWrite(key="tracking_decisions"),)

    StoreOutputRouter(collection).persist([_Module()], {"tracking_decisions": _tracking()}, META)
    assert len(_rows(collection.decisions_path, "select * from tracking_frames")) == 1
    assert _rows(collection.products_path, "select * from table_schemas") == []


def test_router_skips_when_the_key_is_absent(collection):
    class _Module:
        name = "tracking"
        persistence = (DecisionTableWrite(key="tracking_decisions"),)

    StoreOutputRouter(collection).persist([_Module()], {}, META)
    assert _rows(collection.decisions_path, "select * from tracking_frames") == []


def test_unmatched_rows_keep_their_reason(collection):
    unmatched = (TrackingUnmatched("prev", "AAAA", 1, 0, None, None, "NO_CANDIDATE"),)
    bundle = TrackingDecisions(_tracking().frame, _tracking().candidates, unmatched, ())
    with DecisionStore(collection.decisions_path) as store:
        store.write(bundle, META)
    (row,) = _rows(collection.decisions_path, "select * from tracking_unmatched")
    assert (row["side"], row["reason"], row["best_candidate_label"]) == (
        "prev",
        "NO_CANDIDATE",
        None,
    )
