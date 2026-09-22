-- Analysis-only decision log: collections/<RADAR>/decisions.db
--
-- Written by the output router from the tracking and detection modules'
-- decision records (adapt.contracts.decisions); never read by the pipeline or
-- the public API. Rows mirror the frozen record fields plus run/scan stamps.
-- Config thresholds are not repeated here: join run_id to the run registry.

-- user_version 2: split/merge tests carry both normalisations and their areas;
-- tracking_unmatched carries the best candidate's overlaps and cost;
-- segmentation_frames counts size-filter exemptions for carried cells.
-- CREATE TABLE IF NOT EXISTS does not migrate an existing file — a collection
-- written under version 1 keeps its old columns, so start a new base_dir when
-- the new fields are needed.
PRAGMA user_version = 2;

CREATE TABLE IF NOT EXISTS tracking_frames (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    dt_s REAL,
    reset_code TEXT,
    n_prev INTEGER NOT NULL,
    n_curr INTEGER NOT NULL,
    n_pairs INTEGER NOT NULL,
    n_pass_overlap INTEGER NOT NULL,
    n_pass_kinematic INTEGER NOT NULL,
    n_propagated INTEGER NOT NULL,
    n_hungarian INTEGER NOT NULL,
    n_split INTEGER NOT NULL,
    n_merge INTEGER NOT NULL,
    n_initiation INTEGER NOT NULL,
    n_termination INTEGER NOT NULL,
    PRIMARY KEY (run_id, scan_id)
);

CREATE TABLE IF NOT EXISTS tracking_candidates (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    prev_cell_uid TEXT NOT NULL,
    prev_cell_label INTEGER NOT NULL,
    curr_cell_label INTEGER NOT NULL,
    track_steps_before INTEGER NOT NULL,
    hull_area_px INTEGER NOT NULL,
    hull_centroid_x REAL NOT NULL,
    hull_centroid_y REAL NOT NULL,
    curr_area_px INTEGER NOT NULL,
    curr_centroid_x REAL NOT NULL,
    curr_centroid_y REAL NOT NULL,
    intersection_px INTEGER NOT NULL,
    opc REAL NOT NULL,
    ocp REAL NOT NULL,
    overlap_passed INTEGER NOT NULL,
    speed_ms REAL,
    previous_speed_ms REAL,
    accel_cap_ms REAL,
    kinematic_code TEXT,
    displacement_m REAL,
    length_scale_m REAL,
    heading_change_deg REAL,
    cost REAL,
    component_n_prev INTEGER,
    component_n_curr INTEGER,
    cost_rank INTEGER,
    match_method TEXT,
    last_stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    PRIMARY KEY (run_id, scan_id, prev_cell_uid, curr_cell_label)
);
CREATE INDEX IF NOT EXISTS idx_tracking_candidates_prev
    ON tracking_candidates (run_id, prev_cell_uid);

CREATE TABLE IF NOT EXISTS tracking_unmatched (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    side TEXT NOT NULL,
    cell_uid TEXT NOT NULL,
    cell_label INTEGER NOT NULL,
    n_candidates INTEGER NOT NULL,
    best_candidate_label INTEGER,
    best_stage TEXT,
    best_opc REAL,
    best_ocp REAL,
    best_cost REAL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, scan_id, side, cell_uid)
);
CREATE INDEX IF NOT EXISTS idx_tracking_unmatched_uid
    ON tracking_unmatched (run_id, cell_uid);

CREATE TABLE IF NOT EXISTS tracking_split_merge_tests (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    kind TEXT NOT NULL,
    continuing_cell_uid TEXT NOT NULL,
    tested_cell_label INTEGER NOT NULL,
    overlap_fraction REAL NOT NULL,   -- the fraction compared against `threshold`
    threshold REAL NOT NULL,
    passed INTEGER NOT NULL,
    -- Both normalisations and the raw areas: MERGE decides on hull_fraction,
    -- SPLIT on cell_fraction. Kept side by side so the denominator is auditable.
    intersection_px INTEGER,
    hull_px INTEGER,
    tested_px INTEGER,
    hull_fraction REAL,
    cell_fraction REAL,
    PRIMARY KEY (run_id, scan_id, kind, continuing_cell_uid, tested_cell_label)
);

CREATE TABLE IF NOT EXISTS segmentation_frames (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    mask_px INTEGER NOT NULL,
    closed_mask_px INTEGER NOT NULL,
    n_components INTEGER NOT NULL,
    n_independent_seeds INTEGER NOT NULL,
    n_carried_claims INTEGER NOT NULL,
    n_carried_admitted INTEGER NOT NULL,
    n_basins INTEGER NOT NULL,
    n_dropped_small INTEGER NOT NULL,
    n_dropped_large INTEGER NOT NULL,
    n_cells INTEGER NOT NULL,
    n_size_exempt INTEGER,
    PRIMARY KEY (run_id, scan_id)
);

CREATE TABLE IF NOT EXISTS segmentation_seeds (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    seq INTEGER NOT NULL,
    marker_id INTEGER,
    origin TEXT NOT NULL,
    row INTEGER NOT NULL,
    col INTEGER NOT NULL,
    peak_value REAL,
    plateau_px INTEGER,
    component_id INTEGER,
    prior_label INTEGER,
    prior_age INTEGER,
    footprint_px INTEGER,
    clearance_px REAL,
    footprint_holds_seed INTEGER,
    component_claims INTEGER,
    component_seeds INTEGER,
    component_deficit INTEGER,
    decision TEXT NOT NULL,
    basin_px INTEGER,
    final_label INTEGER,
    PRIMARY KEY (run_id, scan_id, seq)
);

CREATE TABLE IF NOT EXISTS segmentation_components (
    run_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    component_id INTEGER NOT NULL,
    area_px INTEGER NOT NULL,
    field_max REAL NOT NULL,
    field_min REAL NOT NULL,
    n_seeds INTEGER NOT NULL,
    n_claims INTEGER NOT NULL,
    deficit INTEGER NOT NULL,
    n_admitted INTEGER NOT NULL,
    n_final_labels INTEGER NOT NULL,
    PRIMARY KEY (run_id, scan_id, component_id)
);
