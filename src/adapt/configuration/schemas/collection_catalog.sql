-- ====================================================================
-- Adapt collection catalog — discovery + provenance ONLY
-- File: catalog.db
-- Location: {root}/collections/{collection_id}/catalog.db
--
-- Science rows live in products.db; run lifecycle lives in the store
-- registry. This database answers: which artifacts/scans exist, which
-- products each scan has, and where every object came from.
-- ====================================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- One row per stored object. artifact_id is uuid4 hex allocated BEFORE the
-- bytes are written; the sha256 checksum is recorded at commit. The file's name
-- is readable (object_name); artifact_id is the identity every link uses.
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id       TEXT PRIMARY KEY,
    artifact_type     TEXT NOT NULL,        -- e.g. raw_volume | gridded3d | segmentation2d
    producer          TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    scan_id           TEXT,                 -- NULL for run-level artifacts
    observation_time  TEXT,                 -- ISO8601 UTC (ordering/display metadata)
    original_filename TEXT,
    source_uri        TEXT,
    object_name       TEXT NOT NULL UNIQUE, -- path under objects/: 'raw/<original name>' or
                                            -- '<run_id>/<module>/<source stem>.<product>.nc'
    checksum_sha256   TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_scan_type
    ON artifacts(run_id, scan_id, artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_source_uri ON artifacts(source_uri);

-- Multi-parent provenance edges; no parent-count limit.
CREATE TABLE IF NOT EXISTS artifact_lineage (
    child_artifact_id  TEXT NOT NULL REFERENCES artifacts(artifact_id),
    parent_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    relationship       TEXT NOT NULL,
    PRIMARY KEY (child_artifact_id, parent_artifact_id, relationship)
);

-- One row per scan per run; keys verbatim from the scan-identity change.
-- A scan is 'complete' once the run's pass over it ended with no module failing
-- (modules skipped for want of earlier scans do not hold it back).
CREATE TABLE IF NOT EXISTS scans (
    run_id           TEXT NOT NULL,
    scan_id          TEXT NOT NULL,   -- content-derived scan identity (join key)
    scan_time        TEXT NOT NULL,   -- ISO8601 UTC (ordering/display metadata)
    scan_date        TEXT NOT NULL,   -- YYYYMMDD (UTC), derived from scan_time
    start_time       TEXT,            -- per-source coverage metadata (nullable)
    end_time         TEXT,            -- per-source coverage metadata (nullable)
    source_file_name TEXT NOT NULL,   -- original source filename (metadata, not identity)
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'complete', 'failed')),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,

    PRIMARY KEY (run_id, scan_id),
    -- Two DIFFERENT scans sharing one nominal second within a run is a data
    -- problem (e.g. a duplicate download); surface it at registration instead
    -- of letting time-ordered reads silently pick one.
    UNIQUE (run_id, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_scans_time ON scans(run_id, scan_time DESC);

-- Which products a scan has, and where each lives (object or products.db
-- table). A reader waits for the product it reads: a scan has a product once
-- its link is here.
CREATE TABLE IF NOT EXISTS scan_products (
    run_id      TEXT NOT NULL,
    scan_id     TEXT NOT NULL,
    product     TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('artifact', 'table')),
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    table_name  TEXT,
    created_at  TEXT NOT NULL,

    PRIMARY KEY (run_id, scan_id, product),
    FOREIGN KEY (run_id, scan_id) REFERENCES scans(run_id, scan_id)
);

-- What became of each module in the run's pass over a scan: it ran ('done'),
-- could not run ('skipped': too few earlier scans, or a module it needs did
-- not run) or raised ('failed'). ``reason`` says why for the last two.
CREATE TABLE IF NOT EXISTS scan_modules (
    run_id     TEXT NOT NULL,
    scan_id    TEXT NOT NULL,
    module     TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('done', 'skipped', 'failed')),
    reason     TEXT,
    updated_at TEXT NOT NULL,

    PRIMARY KEY (run_id, scan_id, module),
    FOREIGN KEY (run_id, scan_id) REFERENCES scans(run_id, scan_id)
);

-- A run's latest checkpoint: the scan it was taken after and the object that
-- holds it (see persistence/checkpoints.py). Written in the same transaction
-- that completes that scan, so the two never disagree; a resumed run starts
-- from it.
CREATE TABLE IF NOT EXISTS run_checkpoints (
    run_id      TEXT PRIMARY KEY,
    scan_id     TEXT NOT NULL,
    scan_time   TEXT NOT NULL,   -- ISO8601 UTC of that scan
    object_name TEXT NOT NULL,   -- path under objects/
    updated_at  TEXT NOT NULL
);

-- Frozen first-frame schema per module table; mirrored in products.db and
-- compared by store.validate_collection.
CREATE TABLE IF NOT EXISTS table_schemas (
    table_name    TEXT PRIMARY KEY,
    owner_module  TEXT NOT NULL,
    granularity   TEXT NOT NULL CHECK (granularity IN ('scan', 'time', 'run')),
    primary_key   TEXT NOT NULL,   -- JSON array of column names
    index_columns TEXT NOT NULL,   -- JSON array of column names
    columns_json  TEXT NOT NULL,   -- frozen [{"name": ..., "type": ...}]
    frozen_at     TEXT NOT NULL
);
