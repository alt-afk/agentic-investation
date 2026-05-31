"""
runner.py
=========
Invokes the manager graph for one signal.

Flow
----
1.  start_execution()  → SQL INSERT, buffer open, execution_id returned
2.  graph.invoke()     → middleware calls log_event() (buffer only, no I/O)
3.  end_execution()    → buffer flushed, gzip-uploaded to S3, SQL UPDATE
                         with artifact_uri + status.  One S3 write total.
"""

from __future__ import annotations

import logging
from typing import Any

from config.constants import FIXED_THREAD_ID, MANAGER_AGENT_NAME, THREAD_ID_PREFIX
from config.context import RuntimeContext
from graph import build_manager
from logger import end_execution, start_execution

log = logging.getLogger("runner")

_graph = build_manager()


def _make_thread_id(signal_id: str) -> str:
    return FIXED_THREAD_ID or f"{THREAD_ID_PREFIX}-{signal_id}"


def _extract_final_output(state: dict) -> dict | None:
    """
    Pull the manager's final output from the graph state.
    Prefers structured_response; falls back to the last AI message content.
    """
    structured = state.get("structured_response")
    if structured:
        return structured if isinstance(structured, dict) else {"value": str(structured)}

    for msg in reversed(state.get("messages") or []):
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("ai", "assistant"):
            content = getattr(msg, "content", None) or (msg.get("content", "") if isinstance(msg, dict) else "")
            return {"summary": str(content)}

    return None


def invoke_manager(signal_id: str, casebook_id: str, signal_payload: dict) -> dict:
    """
    Run the manager agent for one signal end-to-end.

    Returns the final graph state dict.
    Raises on unrecoverable failure (after marking execution as failed in SQL).
    """
    thread_id    = _make_thread_id(signal_id)
    execution_id = start_execution(signal_id, casebook_id, thread_id)

    context = RuntimeContext(
        execution_id=execution_id,
        signal_id=signal_id,
        casebook_id=casebook_id,
        thread_id=thread_id,
        current_agent=MANAGER_AGENT_NAME,
    )

    # execution_id in configurable → available to tool_logging_middleware
    # via request.config (wrap_tool_call doesn't expose ToolRuntime)
    config = {
        "configurable": {
            "thread_id":    thread_id,
            "execution_id": execution_id,
        }
    }

    user_message = {
        "role":    "user",
        "content": (
            f"Signal received.\n\n"
            f"signal_id:   {signal_id}\n"
            f"casebook_id: {casebook_id}\n\n"
            f"Payload:\n{signal_payload}"
        ),
    }

    state: dict = {}
    try:
        state = _graph.invoke(
            {"messages": [user_message]},
            config=config,
            context=context,
        )
        final_result = _extract_final_output(state)
        artifact_uri = end_execution(execution_id, "completed", final_result)
        log.info("execution_complete execution_id=%s artifact=%s", execution_id, artifact_uri)
        return state

    except Exception as exc:
        # Flush partial event buffer even on failure — partial logs > no logs
        artifact_uri = end_execution(execution_id, "failed")
        log.exception(
            "execution_failed execution_id=%s signal_id=%s artifact=%s error=%s",
            execution_id, signal_id, artifact_uri, exc,
        )
        raise