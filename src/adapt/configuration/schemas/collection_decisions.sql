-- Analysis-only decision log: collections/<RADAR>/decisions.db
--
-- A decision table is declared by the module that writes it
-- (DecisionTableWrite) and made by the SchemaLedger with its first row:
-- never static DDL, never ALTER. Only the table registry is static.
-- Never read by the pipeline or the public API.

CREATE TABLE IF NOT EXISTS table_schemas (
    table_name    TEXT PRIMARY KEY,
    owner_module  TEXT NOT NULL,
    granularity   TEXT NOT NULL CHECK (granularity IN ('scan', 'time', 'run')),
    primary_key   TEXT NOT NULL,   -- JSON array of column names
    index_columns TEXT NOT NULL,   -- JSON array of column names
    columns_json  TEXT NOT NULL,   -- frozen [{"name": ..., "type": ...}]
    frozen_at     TEXT NOT NULL
);
