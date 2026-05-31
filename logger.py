"""
logger/event_logger.py
======================
Public API: two functions.

    log_event(...)        – write one row to the events table
    start_execution(...)  – create a row in executions, return its UUID
    end_execution(...)    – mark an execution completed / failed

The middleware and runner call these; nothing else should touch the DB.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from logger.db import execute, fetchone

# ── Type alias ────────────────────────────────────────────────────────────────

EventType = Literal[
    "agent_start",
    "agent_end",
    "tool_call",
    "tool_result",
    "agent_handoff",
    "interrupt",
]

ExecutionStatus = Literal["running", "completed", "failed"]

# ── SQL constants ─────────────────────────────────────────────────────────────

_INSERT_EVENT = """
INSERT INTO events
    (id, execution_id, timestamp, event_type, agent_name, tool_name, payload, result, duration_ms)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_EXECUTION = """
INSERT INTO executions (id, signal_id, casebook_id, thread_id, status, started_at)
VALUES (%s, %s, %s, %s, 'running', %s)
"""

_UPDATE_EXECUTION = """
UPDATE executions SET status = %s, completed_at = %s WHERE id = %s
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _jsonb(value: Any) -> str | None:
    """Serialise to JSON string for psycopg2 JSONB columns, None-safe."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Public API ────────────────────────────────────────────────────────────────

def log_event(
    execution_id: str,
    event_type: EventType,
    *,
    agent_name: str | None = None,
    tool_name: str | None = None,
    payload: dict | None = None,
    result: dict | None = None,
    duration_ms: int | None = None,
) -> None:
    """
    Insert one event row.  All keyword args are optional so callers
    only pass what is relevant for that event type.

    Examples
    --------
    log_event(eid, "agent_start", agent_name="manager-agent")

    log_event(eid, "tool_call",   agent_name="analyst-agent",
              tool_name="analyze_signal_data", payload={"dataset_key": "cpu"})

    log_event(eid, "tool_result", agent_name="analyst-agent",
              tool_name="analyze_signal_data",
              result={"value": 92.3, "status": "anomaly"}, duration_ms=34)
    """
    execute(_INSERT_EVENT, (
        str(uuid.uuid4()),
        execution_id,
        _now(),
        event_type,
        agent_name,
        tool_name,
        _jsonb(payload),
        _jsonb(result),
        duration_ms,
    ))


def start_execution(
    signal_id: str,
    casebook_id: str,
    thread_id: str,
) -> str:
    """
    Create a new executions row and return the UUID string.
    Call this in the runner before invoking the graph.
    """
    eid = str(uuid.uuid4())
    execute(_INSERT_EXECUTION, (eid, signal_id, casebook_id, thread_id, _now()))
    return eid


def end_execution(execution_id: str, status: ExecutionStatus = "completed") -> None:
    """Mark an execution as completed or failed."""
    execute(_UPDATE_EXECUTION, (status, _now(), execution_id))