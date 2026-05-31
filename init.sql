-- migrations/001_init.sql
-- Run once to create the two core tables.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- for gen_random_uuid()

-- ── Executions ────────────────────────────────────────────────────────────────
-- One row per signal invocation.  Tracks the lifecycle of a full agent run.

CREATE TABLE IF NOT EXISTS executions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id    TEXT        NOT NULL,
    casebook_id  TEXT        NOT NULL,
    thread_id    TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'running',   -- running | completed | failed
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_executions_signal_id   ON executions (signal_id);
CREATE INDEX IF NOT EXISTS idx_executions_casebook_id ON executions (casebook_id);
CREATE INDEX IF NOT EXISTS idx_executions_status      ON executions (status);

-- ── Events ────────────────────────────────────────────────────────────────────
-- One row per event emitted by the agent middleware.

CREATE TABLE IF NOT EXISTS events (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID        NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type   VARCHAR(50) NOT NULL,   -- agent_start | agent_end | tool_call |
                                          -- tool_result | agent_handoff | interrupt
    agent_name   VARCHAR(100),
    tool_name    VARCHAR(100),
    payload      JSONB,                  -- input args / context at event time
    result       JSONB,                  -- output / return value
    duration_ms  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_execution_id ON events (execution_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type   ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp    ON events (timestamp);
-- Fast lookup of all tool calls by name across runs
CREATE INDEX IF NOT EXISTS idx_events_tool_name    ON events (tool_name) WHERE tool_name IS NOT NULL;