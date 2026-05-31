"""
config/context.py
=================
Runtime context that flows from the runner invocation through every
subagent and into every tool via `runtime.context`.

Usage inside a tool
-------------------
    from langchain.tools import tool, ToolRuntime
    from config.context import RuntimeContext

    @tool
    def my_tool(param: str, runtime: ToolRuntime[RuntimeContext]) -> str:
        signal_id    = runtime.context.signal_id
        casebook_id  = runtime.context.casebook_id
        execution_id = runtime.context.execution_id
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RuntimeContext:
    execution_id: str   # UUID of the executions row — links all events for this run
    signal_id:    str   # inbound signal identifier
    casebook_id:  str   # casebook this run writes to
    thread_id:    str   # LangGraph thread (for interrupt resumption)
    current_agent: str  # name of the agent that spawned this context (manager sets this)