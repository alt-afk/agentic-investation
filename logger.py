"""
logger/event_logger.py
======================
Public API — three functions:

    start_execution(signal_id, casebook_id, thread_id) → execution_id
        Creates the SQL row, initialises an in-memory event buffer.

    log_event(execution_id, event_type, **fields)
        Appends one event dict to the in-memory buffer.
        No DB write. No I/O.

    end_execution(execution_id, status, final_result)
        Pops the buffer, builds the artifact dict,
        gzip-uploads to S3, updates the SQL row with
        artifact_uri + status + completed_at.
        Single S3 write. Single SQL UPDATE.

Thread safety
-------------
_buffers is protected by _lock.  Concurrent executions each
own their own list; they never share or block each other except
for the dict insert/pop which is microseconds.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from logger.db import execute
from logger.s3 import upload_artifact

EventType = Literal[
    "agent_start",
    "agent_end",
    "tool_call",
    "tool_result",
    "agent_handoff",
    "interrupt",
]

ExecutionStatus = Literal["running", "completed", "failed"]

# ── SQL ───────────────────────────────────────────────────────────────────────

_INSERT_EXECUTION = """
INSERT INTO executions (execution_id, casebook_id, signal_id, status, started_at)
VALUES (%s, %s, %s, 'running', %s)
"""

_UPDATE_EXECUTION = """
UPDATE executions
SET    status = %s, completed_at = %s, artifact_uri = %s
WHERE  execution_id = %s
"""

# ── In-memory buffer ──────────────────────────────────────────────────────────
# Structure per execution_id:
#   {
#     "started_at":  datetime,
#     "signal_id":   str,
#     "casebook_id": str,
#     "events":      list[dict],
#   }

_buffers: dict[str, dict] = {}
_lock    = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compact(d: dict) -> dict:
    """Drop None values so event dicts stay lean."""
    return {k: v for k, v in d.items() if v is not None}


# ── Public API ────────────────────────────────────────────────────────────────

def start_execution(signal_id: str, casebook_id: str, thread_id: str) -> str:
    """
    Insert the executions row and open an event buffer.
    Returns the new execution_id UUID string.
    """
    eid = str(uuid.uuid4())
    now = _now()

    execute(_INSERT_EXECUTION, (eid, casebook_id, signal_id, now))

    with _lock:
        _buffers[eid] = {
            "started_at":  now,
            "signal_id":   signal_id,
            "casebook_id": casebook_id,
            "events":      [],
        }

    return eid


def log_event(
    execution_id: str,
    event_type: EventType,
    *,
    agent_name:  str | None = None,
    tool_name:   str | None = None,
    payload:     dict | None = None,
    result:      dict | None = None,
    duration_ms: int | None = None,
) -> None:
    """
    Append one event to the in-memory buffer.
    Zero I/O — this is intentionally cheap so middleware stays non-blocking.
    """
    event = _compact({
        "ts":          _now().isoformat(timespec="milliseconds"),
        "event_type":  event_type,
        "agent_name":  agent_name,
        "tool_name":   tool_name,
        "payload":     payload,
        "result":      result,
        "duration_ms": duration_ms,
    })

    with _lock:
        buf = _buffers.get(execution_id)
        if buf is not None:
            buf["events"].append(event)
        # Silently drop if buffer already flushed (shouldn't happen in normal flow)


def end_execution(
    execution_id: str,
    status: ExecutionStatus = "completed",
    final_result: dict | None = None,
) -> str:
    """
    Flush the event buffer → build artifact → gzip upload to S3 → update SQL.
    Returns the artifact_uri (s3://...).

    Called once per execution, from runner.py, after the graph finishes
    (or in the except block on failure).
    """
    completed_at = _now()

    with _lock:
        buf = _buffers.pop(execution_id, {})

    artifact = {
        "execution_id": execution_id,
        "signal_id":    buf.get("signal_id", ""),
        "casebook_id":  buf.get("casebook_id", ""),
        "status":       status,
        "started_at":   buf["started_at"].isoformat() if buf.get("started_at") else None,
        "completed_at": completed_at.isoformat(),
        "events":       buf.get("events", []),
        "result":       final_result,
    }

    casebook_id  = buf.get("casebook_id", "unknown")
    artifact_uri = upload_artifact(casebook_id, execution_id, artifact)

    execute(_UPDATE_EXECUTION, (status, completed_at, artifact_uri, execution_id))

    return artifact_uri