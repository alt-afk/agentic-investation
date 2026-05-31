-- migrations/001_init.sql
-- Simplified schema: executions metadata only.
-- Events + final output live in S3 as a compressed artifact.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS executions (
    execution_id  UUID        PRIMARY KEY,
    casebook_id   TEXT        NOT NULL,
    signal_id     TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'running',  -- running | completed | failed
    artifact_uri  TEXT,          -- s3://bucket/casebook-id/execution-id.json.gz
                                 -- NULL until the run completes and the file is written
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_executions_casebook   ON executions (casebook_id);
CREATE INDEX IF NOT EXISTS idx_executions_signal     ON executions (signal_id);
CREATE INDEX IF NOT EXISTS idx_executions_status     ON executions (status);