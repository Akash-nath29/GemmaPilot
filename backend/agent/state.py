"""Shared LangGraph state for the agent."""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    instruction: str

    # Refreshed every loop by the read_state node
    page: dict[str, Any]            # full snapshot {url, title, nodes, ...}
    nodes_by_ref: dict[str, dict]   # ref -> node, for safety lookups

    # Decision + execution
    next_action: dict[str, Any]     # {"action": ..., "ref": ..., ...}
    history: list[dict[str, Any]]   # rolling log of {thought, action, result}
    step: int
    max_steps: int

    # Extra observation surfaced back to the model (e.g. get_datetime result)
    scratch: Optional[str]

    # Termination
    done: bool
    result: str
