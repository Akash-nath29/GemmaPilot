"""The 'decide' brain: turn (instruction + page state + history) into ONE action.

We drive Gemma via constrained JSON output rather than native tool-calling.
"Emit a single JSON object matching this schema" is more reliable than an
OpenAI-style function-calling loop and keeps the whole thing model-agnostic —
so the same code works on a 2B local tag or the 31B cloud model. The graph
parses the JSON and dispatches.

`Planner` is a Protocol so the eval harness can inject a deterministic fake
planner and test the graph / safety gate without Ollama running.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from backend import config

# The action vocabulary the model may emit. Mirrors the PRD tool schema.
ACTION_SCHEMA = """
Respond with ONE JSON object and nothing else. Schema:

{
  "thought": "<one short sentence: what you see and why this action>",
  "action": "click" | "fill" | "scroll" | "navigate" | "get_datetime" | "done",
  "ref": "<element ref like e12, required for click/fill and optional for scroll>",
  "value": "<text to type for fill; a URL for navigate; the final answer for done>",
  "direction": "up" | "down"   // only for scroll
}

Rules:
- Choose exactly ONE action per turn. Prefer using an existing ref over navigate.
- Use refs EXACTLY as shown in the ELEMENTS list. Never invent a ref.
- The ELEMENTS list ALREADY includes off-screen items on the current screen. To
  read/extract information, gather it straight from the list — do NOT scroll just
  to "look". Only scroll when the list says content is hidden above/below.
- Scroll purposefully: scroll "down" only when "N below not shown" > 0, "up" only
  when "N above not shown" > 0. If the page is [at bottom] and you still can't
  find it, stop scrolling and answer with what you have.
- NEVER repeat the same action two turns in a row if it produced no new result.
  If something isn't working, change approach: try a different element, scroll the
  other way, or finish with your best answer.
- If an action FAILED, read the error, then try a different element or method
  rather than repeating the failed one.
- Use "get_datetime" if the task needs today's date (e.g. "next Friday").
- When the task is complete — or you cannot make further progress — use action
  "done" with the answer/summary in "value". Always deliver your best answer;
  never give up silently.
- Do NOT try to click a submit/pay/confirm/delete button unless the user's
  instruction explicitly asks to complete that step; a human will confirm it.
""".strip()

SYSTEM_PROMPT = (
    "You are a careful browser automation agent operating a real web page on the "
    "user's behalf. You are given the user's instruction, the current page, and a "
    "list of elements you can act on. Think step by step but act ONE step at a "
    "time.\n\n" + ACTION_SCHEMA
)


def render_page(state: dict[str, Any]) -> str:
    """Compact, model-friendly rendering of the current page snapshot."""
    page = state.get("page", {}) or {}
    nodes = page.get("nodes", [])
    hidden_above = page.get("hiddenAbove", 0)
    hidden_below = page.get("hiddenBelow", 0)
    hidden_bits = []
    if hidden_above:
        hidden_bits.append(f"{hidden_above} above not shown")
    if hidden_below:
        hidden_bits.append(f"{hidden_below} below not shown")
    hidden_note = f'  [{", ".join(hidden_bits)}]' if hidden_bits else ""
    lines = [
        f'PAGE: {page.get("title", "")}  ({page.get("url", "")})',
        f'scroll: {page.get("scrollY", 0)}/{page.get("scrollHeight", 0)}px'
        f'{"  [at bottom]" if page.get("atPageBottom") else ""}'
        f'{hidden_note}',
        "",
        "ELEMENTS (ref | role | name | value):",
    ]
    for n in nodes:
        mark = "" if n.get("inViewport") else "  (off-screen)"
        val = f'  = "{n["value"]}"' if n.get("value") else ""
        lines.append(f'  [{n["ref"]}] {n["role"]}: "{n.get("name", "")}"{val}{mark}')
    if not nodes:
        lines.append("  (no interactive elements detected)")
    return "\n".join(lines)


def render_history(state: dict[str, Any]) -> str:
    history = state.get("history", [])
    if not history:
        return "No actions taken yet."
    lines = []
    for i, h in enumerate(history[-8:], 1):  # last few steps only
        act = h.get("action", {})
        res = h.get("result", "")
        lines.append(f'{i}. {json.dumps(act)} -> {res}')
    return "\n".join(lines)


def build_user_prompt(state: dict[str, Any]) -> str:
    parts = [
        f'USER INSTRUCTION: {state.get("instruction", "")}',
        "",
        render_page(state),
        "",
        "ACTIONS SO FAR:",
        render_history(state),
    ]
    if state.get("scratch"):
        parts += ["", f'NOTE: {state["scratch"]}']
    parts += ["", "Decide the single next action as JSON."]
    return "\n".join(parts)


@runtime_checkable
class Planner(Protocol):
    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return the next action dict for the given state."""
        ...


class OllamaPlanner:
    """Real planner backed by a Gemma model on Ollama Cloud (or a local tag)."""

    def __init__(self, model: str | None = None, base_url: str | None = None):
        # Imported lazily so the eval harness / tests don't need langchain.
        from langchain_ollama import ChatOllama

        self.model_name = model or config.OLLAMA_MODEL
        # If an API key is set, forward it as a bearer token so we can talk to
        # Ollama Cloud directly (base_url=https://ollama.com). Without a key, a
        # local daemon that has run `ollama signin` proxies *-cloud tags for us.
        client_kwargs: dict[str, Any] = {}
        if config.OLLAMA_API_KEY:
            client_kwargs["headers"] = {"Authorization": f"Bearer {config.OLLAMA_API_KEY}"}

        self._llm = ChatOllama(
            model=self.model_name,
            base_url=base_url or config.OLLAMA_BASE_URL,
            temperature=config.LLM_TEMPERATURE,
            num_ctx=config.LLM_NUM_CTX,
            format="json",  # constrain output to valid JSON
            client_kwargs=client_kwargs or None,
        )

    def plan(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = [
            ("system", SYSTEM_PROMPT),
            ("human", build_user_prompt(state)),
        ]
        response = self._llm.invoke(messages)
        return parse_action(response.content)


def parse_action(raw: str) -> dict[str, Any]:
    """Best-effort parse of the model's JSON. Never raises; degrades to 'done'."""
    text = (raw or "").strip()
    # Strip ```json fences if a chatty model added them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return {"action": "done", "value": "Could not parse a valid action.",
                        "thought": "parse-error", "_error": raw}
        else:
            return {"action": "done", "value": "Could not parse a valid action.",
                    "thought": "parse-error", "_error": raw}

    if not isinstance(data, dict) or "action" not in data:
        return {"action": "done", "value": "No action produced.", "thought": "empty"}
    data["action"] = str(data["action"]).strip().lower()
    return data
