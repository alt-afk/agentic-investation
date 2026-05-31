"""
middleware/logging_middleware.py
================================
Two exports:

    AgentLoggingMiddleware   – class, hooks agent start / end lifecycle
    tool_logging_middleware  – @wrap_tool_call, hooks every tool call / result

Both extract everything they need from `runtime` / `state` / `request.config`.
No initialisation per agent.  No repeated logging in tool functions.

Event coverage
--------------
  agent_start    before_agent fires
  agent_end      after_agent fires  (+ duration_ms)
  tool_call      before handler()   (skipped for the `task` delegation tool)
  tool_result    after handler()    (skipped for the `task` delegation tool)
  agent_handoff  when tool name is "task" (DeepAgents subagent delegation)
  interrupt      when GraphInterrupt is raised inside a tool
"""

from __future__ import annotations

import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langgraph.errors import GraphInterrupt

from logger import log_event

# Key used to stash the agent-start monotonic timestamp in graph state.
# Must be unique enough to avoid clashing with any real state key.
_START_NS_KEY = "__mw_agent_start_ns__"


# ── Helpers (module-level, shared by both middleware pieces) ──────────────────

def _agent_name(runtime) -> str:
    """
    Resolve agent name:
      1. lc_agent_name in LangGraph run metadata  (set automatically by DeepAgents
         for every subagent, most reliable source)
      2. runtime.context.current_agent             (set by the runner for the manager)
      3. fallback
    """
    name = runtime.config.get("metadata", {}).get("lc_agent_name", "")
    if not name:
        ctx = getattr(runtime, "context", None)
        name = getattr(ctx, "current_agent", "") or "unknown-agent"
    return name


def _execution_id(runtime) -> str:
    ctx = getattr(runtime, "context", None)
    return getattr(ctx, "execution_id", "") if ctx else ""


def _execution_id_from_config(config: dict) -> str:
    """
    Used inside wrap_tool_call where we only have request.config, not a runtime.
    The runner stores execution_id in config["configurable"] at invoke time.
    """
    return config.get("configurable", {}).get("execution_id", "")


# ── Agent lifecycle middleware ─────────────────────────────────────────────────

class AgentLoggingMiddleware(AgentMiddleware):
    """
    One class, no __init__, no per-agent state.
    Reads everything it needs from `runtime` and `state`.
    """

    def before_agent(self, state: dict, runtime) -> dict:
        log_event(
            _execution_id(runtime),
            "agent_start",
            agent_name=_agent_name(runtime),
        )
        # Stash wall-clock ns in graph state so after_agent can diff it.
        # Never use self.x — concurrent subagents would race.
        return {_START_NS_KEY: time.monotonic_ns()}

    def after_agent(self, state: dict, runtime) -> dict:
        start_ns = state.get(_START_NS_KEY)
        duration_ms = (
            int((time.monotonic_ns() - start_ns) / 1_000_000)
            if start_ns else None
        )

        # Surface the agent's final output if present.
        result = _extract_agent_result(state)

        log_event(
            _execution_id(runtime),
            "agent_end",
            agent_name=_agent_name(runtime),
            result=result,
            duration_ms=duration_ms,
        )
        return {}


def _extract_agent_result(state: dict) -> dict | None:
    """
    Pull a lightweight result summary from state without importing message types.
    Prefers structured_response; falls back to the last AI message content.
    """
    structured = state.get("structured_response")
    if structured:
        return structured if isinstance(structured, dict) else {"value": str(structured)}

    messages = state.get("messages") or []
    for msg in reversed(messages):
        # Works for both dict-messages and LangChain message objects
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("ai", "assistant"):
            content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
            return {"summary": str(content)[:500]}

    return None


# ── Tool call middleware ───────────────────────────────────────────────────────

@wrap_tool_call
def tool_logging_middleware(request, handler):
    """
    Wraps every tool call.  Single function, registered once, shared by all agents.

    Routing logic
    -------------
    • tool == "task"  →  agent_handoff event  (DeepAgents subagent delegation)
    • everything else →  tool_call + tool_result events
    • GraphInterrupt  →  interrupt event (re-raised after logging)
    """
    config      = request.config
    agent_name  = config.get("metadata", {}).get("lc_agent_name", "unknown-agent")
    tool_name   = request.name
    payload     = dict(request.args) if request.args else {}
    eid         = _execution_id_from_config(config)
    thread_id   = config.get("configurable", {}).get("thread_id", "")

    # ── Handoff: manager delegating to a subagent via task() ─────────────────
    if tool_name == "task":
        log_event(
            eid,
            "agent_handoff",
            agent_name=agent_name,
            payload={
                "from_agent": agent_name,
                "to_agent":   payload.get("name", "unknown"),
                "task":       payload.get("task", "")[:300],
            },
        )
        return handler(request)

    # ── Regular tool: log call, execute, log result ───────────────────────────
    log_event(eid, "tool_call", agent_name=agent_name, tool_name=tool_name, payload=payload)

    start_ns = time.monotonic_ns()

    try:
        result = handler(request)

    except GraphInterrupt:
        log_event(
            eid,
            "interrupt",
            agent_name=agent_name,
            tool_name=tool_name,
            payload={"thread_id": thread_id, "reason": "human_approval_required"},
        )
        raise  # LangGraph must see this to pause the graph

    except Exception as exc:
        duration_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
        log_event(
            eid,
            "tool_result",
            agent_name=agent_name,
            tool_name=tool_name,
            result={"error": str(exc)},
            duration_ms=duration_ms,
        )
        raise

    else:
        duration_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
        log_event(
            eid,
            "tool_result",
            agent_name=agent_name,
            tool_name=tool_name,
            result=_coerce_result(result),
            duration_ms=duration_ms,
        )
        return result


def _coerce_result(value: Any) -> dict:
    """Ensure tool result is always stored as a dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        return {"items": value}
    return {"output": str(value)[:1000]}