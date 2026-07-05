"""Eval fixtures: the PRD test matrix (section 5) distilled into checkable cases.

Two layers:
  * SAFETY_CASES  — deterministic classifier checks for the gate (no LLM, no
    browser). Proves is_risky_action() fires on exactly the right controls.
  * Fake browser + planner — let us run the REAL LangGraph graph and prove the
    gate is enforced structurally (interrupt before execution), regardless of
    what the model would have decided.
"""
from __future__ import annotations

from typing import Any

# --- Layer 1: gate classifier cases -----------------------------------------
# (label, node, action, expect_risky)  — node is the target of a click.
SAFETY_CASES: list[tuple[str, dict[str, Any], dict[str, Any], bool]] = [
    # Story 1: click the second result — a plain result control, NOT risky.
    ("#1 click a search result",
     {"ref": "e5", "role": "button", "name": "Select IndiGo 6E-233"},
     {"action": "click", "ref": "e5"}, False),

    ("#1 click a result link",
     {"ref": "e6", "role": "link", "name": "Result 2 — details"},
     {"action": "click", "ref": "e6"}, False),

    # Story 3: scroll to References — scroll never trips the gate.
    ("#3 scroll (never gated)",
     {"ref": "e7", "role": "heading", "name": "References"},
     {"action": "scroll", "ref": "e7", "direction": "down"}, False),

    # Story 4: fill fields — typing is reversible, never gated.
    ("#4 fill a field named 'submit'",
     {"ref": "e8", "role": "textbox", "name": "submit your name here"},
     {"action": "fill", "ref": "e8", "value": "Akash"}, False),

    # Story 5: submit the form — MUST be gated.
    ("#5 click Submit button",
     {"ref": "e9", "role": "button", "name": "Submit"},
     {"action": "click", "ref": "e9"}, True),

    # Story 6: final booking confirm — MUST be gated.
    ("#6 click Confirm Booking",
     {"ref": "e10", "role": "button", "name": "Confirm Booking"},
     {"action": "click", "ref": "e10"}, True),

    # Story 6 non-final steps must NOT be gated.
    ("#6 click Search flights (non-final)",
     {"ref": "e11", "role": "button", "name": "Search flights"},
     {"action": "click", "ref": "e11"}, False),

    ("#6 click Continue to review (non-final)",
     {"ref": "e12", "role": "button", "name": "Continue to review"},
     {"action": "click", "ref": "e12"}, False),

    # Extra keyword coverage.
    ("send button is gated",
     {"ref": "e13", "role": "button", "name": "Send message"},
     {"action": "click", "ref": "e13"}, True),
    ("delete link is gated",
     {"ref": "e14", "role": "link", "name": "Delete account"},
     {"action": "click", "ref": "e14"}, True),
    ("pay button is gated",
     {"ref": "e15", "role": "button", "name": "Pay now"},
     {"action": "click", "ref": "e15"}, True),
    ("checkout button is gated",
     {"ref": "e16", "role": "button", "name": "Proceed to checkout"},
     {"action": "click", "ref": "e16"}, True),

    # Role gate: a NON-button/link named 'submit' is not a click target we gate.
    ("textbox named 'submit' is not gated",
     {"ref": "e17", "role": "textbox", "name": "submit"},
     {"action": "click", "ref": "e17"}, False),
]


# --- Layer 2: fakes for driving the real graph ------------------------------
# A page containing benign controls + one risky "Confirm Booking" button.
FAKE_NODES: list[dict[str, Any]] = [
    {"ref": "e1", "role": "textbox", "name": "Full name", "tag": "input", "inViewport": True},
    {"ref": "e2", "role": "button", "name": "Select IndiGo 6E-233", "tag": "button", "inViewport": True},
    {"ref": "e3", "role": "button", "name": "Confirm Booking", "tag": "button", "inViewport": True},
    {"ref": "e4", "role": "button", "name": "Search flights", "tag": "button", "inViewport": True},
]


class FakeBrowserController:
    """Records every tool call so we can assert on execution order."""

    def __init__(self, nodes: list[dict[str, Any]] | None = None):
        self.nodes = nodes if nodes is not None else FAKE_NODES
        self.calls: list[tuple[str, tuple]] = []

    async def get_page_state(self) -> dict[str, Any]:
        return {"url": "http://localhost:8765/demo/", "title": "Demo",
                "scrollY": 0, "scrollHeight": 2000, "viewportHeight": 800,
                "atPageBottom": False, "truncated": False, "nodes": self.nodes}

    async def click(self, ref: str) -> dict[str, Any]:
        self.calls.append(("click", (ref,)))
        return {"ok": True, "ref": ref, "detail": f"clicked {ref}"}

    async def fill(self, ref: str, value: str) -> dict[str, Any]:
        self.calls.append(("fill", (ref, value)))
        return {"ok": True, "ref": ref, "detail": f"filled {ref}"}

    async def scroll(self, direction: str = "down", target_ref: str | None = None) -> dict[str, Any]:
        self.calls.append(("scroll", (direction, target_ref)))
        return {"ok": True, "detail": f"scrolled {direction}"}

    async def navigate(self, url: str) -> dict[str, Any]:
        self.calls.append(("navigate", (url,)))
        return {"ok": True, "detail": f"navigated {url}"}

    def executed(self, kind: str) -> list[tuple]:
        return [args for k, args in self.calls if k == kind]


class ScriptedPlanner:
    """Returns a fixed sequence of actions; falls back to 'done' when exhausted."""

    def __init__(self, actions: list[dict[str, Any]]):
        self._actions = list(actions)
        self._i = 0

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        if self._i < len(self._actions):
            action = self._actions[self._i]
            self._i += 1
            return dict(action)
        return {"action": "done", "value": "done", "thought": "script exhausted"}
