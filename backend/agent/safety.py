"""The safety gate (Goal G3).

Before the agent executes any click on a control that *looks* irreversible
(submit / confirm / pay / buy / delete / send / order / checkout), the graph
must stop and get explicit human sign-off. This module is the single source of
truth for "is this action risky?". The graph enforces it structurally by
calling `interrupt()` whenever `is_risky_action()` returns True — the model is
never trusted to police itself.

This is deliberately a keyword heuristic. It is honest v0.1, not a guarantee:
  * False negatives: an unusual label ("Complete my journey") won't match.
  * False positives: a harmless "Send feedback" button will trip the gate.
Both are documented in the README as known limitations, with the v2 direction
(let the LLM classify action risk) called out.
"""
from __future__ import annotations

from typing import Any, Optional

# Exact keyword set from the PRD (section 8). Matched as case-insensitive
# substrings against a control's accessible name.
RISKY_KEYWORDS: tuple[str, ...] = (
    "submit",
    "confirm",
    "pay",
    "buy",
    "delete",
    "send",
    "order",
    "checkout",
)

# Only clicks on these roles are gated. Filling a textbox or scrolling can never
# trip the gate; typing text is reversible, committing it may not be.
GATED_ROLES: frozenset[str] = frozenset({"button", "link", "menuitem", "tab"})


def is_risky_action(action: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> tuple[bool, Optional[str]]:
    """Return (is_risky, matched_keyword).

    `action` is the decided action dict ({"action": "click", "ref": "e5", ...}).
    `nodes` maps ref -> node dict ({role, name, ...}) from the latest snapshot.
    """
    if action.get("action") != "click":
        return False, None

    node = nodes.get(action.get("ref", ""))
    if not node:
        return False, None

    role = (node.get("role") or "").lower()
    if role not in GATED_ROLES:
        return False, None

    name = (node.get("name") or "").lower()
    for keyword in RISKY_KEYWORDS:
        if keyword in name:
            return True, keyword
    return False, None


def describe_risk(action: dict[str, Any], nodes: dict[str, dict[str, Any]], keyword: str) -> dict[str, Any]:
    """Build the payload shown to the human at the confirmation gate."""
    node = nodes.get(action.get("ref", ""), {})
    label = node.get("name") or action.get("ref", "this control")
    return {
        "action": action,
        "target": {"ref": node.get("ref"), "role": node.get("role"), "name": node.get("name")},
        "matched_keyword": keyword,
        "reason": (
            f'This click targets a {node.get("role", "control")} labelled "{label}", '
            f'which matches the risky keyword "{keyword}". It may be irreversible '
            "(submit / purchase / delete). Approve to proceed, or deny to stop."
        ),
    }
