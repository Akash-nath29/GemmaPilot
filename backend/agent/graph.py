"""The LangGraph agent graph.

    read_state -> decide -> act -> (evaluate) -> loop

`act` calls `interrupt()` whenever the safety gate flags the decided click as
risky, so a human confirmation is architecturally required before any
submit/pay/delete/confirm click executes (Goal G3). The graph is built with an
injected browser controller and planner so the eval harness can swap in fakes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from backend import config
from backend.agent.planner import OllamaPlanner, Planner
from backend.agent.safety import describe_risk, is_risky_action
from backend.agent.state import AgentState

# A controller is anything exposing the async tool methods; typed loosely so the
# fake controller in the eval harness satisfies it without inheritance.
Controller = Any


def _emit(event: dict[str, Any]) -> None:
    """Stream a structured event to the sidepanel, if we're in a stream."""
    try:
        writer = get_stream_writer()
        writer(event)
    except Exception:
        pass  # invoked outside a streaming context (e.g. some eval paths)


async def _dispatch(controller: Controller, action: dict[str, Any]) -> dict[str, Any]:
    kind = action.get("action")
    if kind == "click":
        return await controller.click(action.get("ref", ""))
    if kind == "fill":
        return await controller.fill(action.get("ref", ""), action.get("value", ""))
    if kind == "scroll":
        return await controller.scroll(action.get("direction", "down"), action.get("ref"))
    if kind == "navigate":
        return await controller.navigate(action.get("value", ""))
    return {"ok": False, "error": f"unknown action '{kind}'"}


def _action_signature(action: dict[str, Any]) -> tuple:
    return (action.get("action"), action.get("ref"),
            action.get("direction"), action.get("value"))


def _repetition_note(history: list[dict[str, Any]]) -> str | None:
    """Self-healing: detect a stuck loop (same action repeated) and nudge a change."""
    acts = [h.get("action") for h in history if h.get("action")]
    if len(acts) < 2:
        return None
    last_sig = _action_signature(acts[-1])
    repeats = 0
    for a in reversed(acts):
        if _action_signature(a) == last_sig:
            repeats += 1
        else:
            break
    if repeats < 2:
        return None
    kind = acts[-1].get("action", "action")
    return (
        f"SELF-CHECK: you have run the same '{kind}' action {repeats} times in a row "
        "with no new progress — you are stuck. Do NOT repeat it. Change strategy now: "
        "extract the answer from the ELEMENTS already listed and finish with 'done', "
        "choose a different element, or scroll the other direction."
    )


def build_graph(controller: Controller, planner: Planner | None = None):
    """Compile the agent graph. `planner` defaults to the real Ollama planner."""
    planner = planner or OllamaPlanner()

    # --- nodes ---------------------------------------------------------------
    async def read_state(state: AgentState) -> dict[str, Any]:
        page = await controller.get_page_state()
        nodes_by_ref = {n["ref"]: n for n in page.get("nodes", [])}
        _emit({
            "type": "observation",
            "url": page.get("url"),
            "title": page.get("title"),
            "num_elements": len(page.get("nodes", [])),
            "scrollY": page.get("scrollY"),
            "scrollHeight": page.get("scrollHeight"),
        })
        return {"page": page, "nodes_by_ref": nodes_by_ref}

    def decide(state: AgentState) -> dict[str, Any]:
        step = state.get("step", 0)
        max_steps = state.get("max_steps", config.MAX_STEPS)
        if step >= max_steps:
            action = {"action": "done",
                      "value": f"Reached the {max_steps}-step limit without finishing.",
                      "thought": "step-limit"}
        else:
            # Fold in any self-healing note (loop break / prior failure) so the
            # planner sees it prominently, then plan.
            scratch = state.get("scratch")
            note = _repetition_note(state.get("history", []))
            if note:
                scratch = "\n".join(s for s in (scratch, note) if s)
            action = planner.plan({**state, "scratch": scratch})
        _emit({
            "type": "reasoning",
            "step": step + 1,
            "thought": action.get("thought", ""),
            "action": action.get("action"),
            "ref": action.get("ref"),
            "value": action.get("value"),
            "direction": action.get("direction"),
        })
        return {"next_action": action, "scratch": None}

    async def act(state: AgentState) -> dict[str, Any]:
        action = state["next_action"]
        kind = action.get("action")
        history = list(state.get("history", []))
        step = state.get("step", 0)

        # Terminal action -----------------------------------------------------
        if kind == "done":
            result = action.get("value") or "Task complete."
            _emit({"type": "done", "result": result})
            return {"done": True, "result": result}

        # Local tool that needs no browser + no gate -------------------------
        if kind == "get_datetime":
            now = datetime.now().astimezone().strftime("%A, %Y-%m-%d %H:%M %Z")
            note = f"Current date/time is {now}."
            _emit({"type": "action", "status": "executed",
                   "action": action, "result": {"ok": True, "detail": note}})
            history.append({"thought": action.get("thought", ""), "action": action, "result": note})
            return {"scratch": note, "history": history, "step": step + 1}

        # --- SAFETY GATE (Goal G3) ------------------------------------------
        # Everything above this line is side-effect free, so it is safe for the
        # node to re-run from the top when resumed after interrupt().
        risky, keyword = is_risky_action(action, state.get("nodes_by_ref", {}))
        if risky:
            payload = describe_risk(action, state.get("nodes_by_ref", {}), keyword)
            decision = interrupt(payload)  # <-- pauses graph until human responds
            approved = decision.get("approved") if isinstance(decision, dict) else bool(decision)
            if not approved:
                _emit({"type": "action", "status": "denied", "action": action,
                       "result": {"ok": False, "detail": "Denied by user — not executed."}})
                history.append({"thought": action.get("thought", ""), "action": action,
                                "result": "DENIED by user — not executed."})
                return {"done": True,
                        "result": "Stopped: you declined the confirmation, so nothing was submitted.",
                        "history": history, "step": step + 1}

        # --- execute ---------------------------------------------------------
        result = await _dispatch(controller, action)
        _emit({"type": "action", "status": "executed", "action": action, "result": result})
        history.append({
            "thought": action.get("thought", ""),
            "action": action,
            "result": result.get("detail") or ("ok" if result.get("ok") else result.get("error", "failed")),
        })
        # Self-heal: surface a failure to the next planning step so it adapts
        # instead of blindly repeating the same broken action.
        scratch = None
        if not result.get("ok"):
            scratch = (f"Your last action FAILED: {result.get('error', 'unknown error')}. "
                       "Do not repeat it — try a different element or approach.")
        elif kind == "scroll" and result.get("moved") == 0:
            # The page didn't move — we're pinned at the top/bottom. Scrolling
            # again is futile; extract the answer from what's shown and finish.
            scratch = ("Scrolling had NO effect — you are at the edge of the page. "
                       "Stop scrolling: answer from the ELEMENTS already listed and "
                       "finish with 'done'.")
        return {"history": history, "step": step + 1, "scratch": scratch}

    # --- evaluate (routing) --------------------------------------------------
    def should_continue(state: AgentState) -> str:
        return END if state.get("done") else "read_state"

    graph = StateGraph(AgentState)
    graph.add_node("read_state", read_state)
    graph.add_node("decide", decide)
    graph.add_node("act", act)

    graph.add_edge(START, "read_state")
    graph.add_edge("read_state", "decide")
    graph.add_edge("decide", "act")
    graph.add_conditional_edges("act", should_continue, {"read_state": "read_state", END: END})

    return graph.compile(checkpointer=MemorySaver())


def initial_state(instruction: str, max_steps: int | None = None) -> AgentState:
    return {
        "instruction": instruction,
        "history": [],
        "step": 0,
        "max_steps": max_steps or config.MAX_STEPS,
        "done": False,
        "result": "",
        "scratch": None,
    }
