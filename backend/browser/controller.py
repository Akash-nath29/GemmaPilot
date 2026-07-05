"""Playwright-over-CDP browser controller.

We do NOT launch a fresh browser. We attach to the user's *real* Chrome (started
with --remote-debugging-port) via `connect_over_cdp`, find the active tab, and
drive it. This is what makes the agent act on "whatever page is open" (Goal G4).

Every method returns a small JSON-serializable dict so the graph can log it and
stream it to the sidepanel verbatim.
"""
from __future__ import annotations

from typing import Any, Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

from backend import config
from backend.browser.dom_snapshot import PAGE_STATE_JS


class BrowserController:
    """Thin async wrapper around the active tab of the user's Chrome."""

    def __init__(self, cdp_endpoint: str | None = None, max_nodes: int | None = None):
        self.cdp_endpoint = cdp_endpoint or config.CDP_ENDPOINT
        self.max_nodes = max_nodes or config.MAX_PAGE_NODES
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        # Last snapshot's nodes, keyed by ref -> node dict. Used by the safety
        # gate so it can inspect the target of a click without re-querying.
        self.last_nodes: dict[str, dict[str, Any]] = {}

    # --- lifecycle -----------------------------------------------------------
    async def connect(self) -> None:
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_endpoint)
        except Exception as exc:  # pragma: no cover - surfaced to the user
            await self._pw.stop()
            self._pw = None
            raise ConnectionError(
                f"Could not attach to Chrome at {self.cdp_endpoint}. "
                "Launch Chrome with --remote-debugging-port first "
                "(see scripts/launch_chrome.sh)."
            ) from exc

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    # --- tab selection -------------------------------------------------------
    async def active_page(self) -> Page:
        """Return the tab the user is actually looking at.

        Heuristic: among real http(s)/file pages, prefer the one Chrome reports
        as visible (only the foreground tab of the focused window is), else the
        most recently opened. Never returns the extension's own page.
        """
        if not self._browser:
            raise ConnectionError("Not connected to Chrome. Call connect() first.")

        candidates: list[Page] = []
        for context in self._browser.contexts:
            for page in context.pages:
                if page.is_closed():
                    continue
                url = page.url or ""
                if url.startswith(("chrome://", "chrome-extension://", "devtools://", "about:")):
                    continue
                candidates.append(page)

        if not candidates:
            raise RuntimeError(
                "No usable tab found. Open a normal http(s) page in the "
                "Chrome window that has remote debugging enabled."
            )

        for page in reversed(candidates):  # newest first
            try:
                if await page.evaluate("document.visibilityState === 'visible'"):
                    return page
            except Exception:
                continue
        return candidates[-1]

    # --- tools (the PRD tool schema) ----------------------------------------
    async def get_page_state(self) -> dict[str, Any]:
        page = await self.active_page()
        # A prior action may have triggered a navigation; wait for the new
        # document to parse before snapshotting so we never read a half-loaded
        # (or torn-down) DOM. Best-effort — some pages never fully settle.
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        # Retry once: a navigation can still land mid-evaluate and destroy the
        # execution context. One transient hiccup must not kill the whole run.
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                state = await page.evaluate(PAGE_STATE_JS, self.max_nodes)
                self.last_nodes = {n["ref"]: n for n in state.get("nodes", [])}
                return state
            except Exception as exc:
                last_err = exc
                if attempt == 0:
                    await page.wait_for_timeout(500)

        # Degrade gracefully rather than raising — hand back an empty page so
        # the agent can decide what to do (e.g. scroll, wait, or finish).
        self.last_nodes = {}
        return {
            "url": (page.url if not page.is_closed() else ""),
            "title": "", "scrollY": 0, "scrollHeight": 0, "viewportHeight": 0,
            "atPageBottom": False, "truncated": False, "nodes": [],
            "warning": f"could not read page: {last_err}",
        }

    def _selector(self, ref: str) -> str:
        return f'[data-agent-ref="{ref}"]'

    async def click(self, ref: str) -> dict[str, Any]:
        page = await self.active_page()
        node = self.last_nodes.get(ref, {})
        try:
            await page.click(self._selector(ref), timeout=5000)
            return {"ok": True, "ref": ref, "name": node.get("name", ""),
                    "detail": f'clicked {node.get("role", "element")} "{node.get("name", ref)}"'}
        except Exception as exc:
            return {"ok": False, "ref": ref, "error": str(exc)}

    async def fill(self, ref: str, value: str) -> dict[str, Any]:
        page = await self.active_page()
        node = self.last_nodes.get(ref, {})
        selector = self._selector(ref)
        name = node.get("name", ref)
        try:
            # <select> dropdowns can't be typed into — choose an option instead.
            if (node.get("tag") == "select") or (node.get("role") == "combobox"):
                chosen = await self._select_option(page, selector, value)
                return {"ok": True, "ref": ref, "value": chosen,
                        "detail": f'selected "{chosen}" in "{name}"'}
            await page.fill(selector, value, timeout=5000)
            return {"ok": True, "ref": ref, "value": value,
                    "detail": f'filled "{name}" with "{value}"'}
        except Exception as exc:
            return {"ok": False, "ref": ref, "error": str(exc)}

    async def _select_option(self, page, selector: str, value: str) -> str:
        """Pick the best-matching <select> option for `value`.

        Reads the options once (instant) and resolves the match before calling
        select_option, so a near-miss never burns Playwright's timeout waiting
        for an option that doesn't exist.
        """
        options = await page.eval_on_selector(
            selector,
            "(el) => Array.from(el.options).map(o => ({value: o.value, "
            "label: (o.label || o.text || '').trim()}))",
        )
        target = value.strip().lower()
        match = None
        for opt in options:  # 1) exact value or label
            if opt["value"].lower() == target or opt["label"].lower() == target:
                match = opt
                break
        if match is None:  # 2) substring, either direction
            for opt in options:
                lab, val = opt["label"].lower(), opt["value"].lower()
                if target in lab or lab in target or target in val:
                    match = opt
                    break
        if match is None:
            raise ValueError(
                f'no <select> option matching "{value}" '
                f'(choices: {[o["label"] or o["value"] for o in options]})'
            )
        await page.select_option(selector, value=match["value"], timeout=3000)
        return match["label"] or match["value"]

    async def scroll(self, direction: str = "down", target_ref: str | None = None) -> dict[str, Any]:
        """Advance the viewport, guaranteeing real progress.

        The model often sends BOTH a `direction` and a `ref`. Naively honouring
        the ref via scroll_into_view_if_needed is a no-op when that element is
        already on screen — and because our snapshot is viewport-anchored, every
        ref the model can name is on screen, so the page never moves and the run
        loops until the step limit. So: only scroll-to-ref when the target is
        genuinely OFF-screen; otherwise (or if that moved nothing) fall back to a
        directional scroll of ~one viewport. Always report the true pixel delta
        so the planner can see when it has hit the top/bottom and should stop.
        """
        page = await self.active_page()
        try:
            before = await page.evaluate("() => Math.round(window.scrollY)")

            # Only a ref that's actually off-screen is worth scrolling *to*; an
            # on-screen (or stale/unknown) ref falls through to a plain scroll.
            node = self.last_nodes.get(target_ref or "", {})
            scrolled_to_ref = False
            if target_ref and node.get("inViewport") is False:
                try:
                    await page.locator(self._selector(target_ref)).scroll_into_view_if_needed(timeout=3000)
                    scrolled_to_ref = True
                except Exception:
                    scrolled_to_ref = False  # ref vanished/detached — use direction

            after = await page.evaluate("() => Math.round(window.scrollY)")

            # No ref used, or the ref scroll didn't move us: guarantee progress
            # with a directional scroll of ~one viewport (instant so we can read
            # the settled position immediately, even under scroll-behavior:smooth).
            if after == before:
                scrolled_to_ref = False
                factor = -0.85 if direction == "up" else 0.85
                await page.evaluate(
                    "(f) => window.scrollBy({top: window.innerHeight * f, behavior: 'instant'})",
                    factor,
                )
                after = await page.evaluate("() => Math.round(window.scrollY)")

            delta = after - before
            if delta == 0:
                edge = "top" if direction == "up" else "bottom"
                return {"ok": True, "moved": 0,
                        "detail": f"already at the {edge} of the page — nothing more to scroll {direction}"}
            if scrolled_to_ref:
                name = self.last_nodes.get(target_ref, {}).get("name", target_ref)
                return {"ok": True, "moved": delta,
                        "detail": f'scrolled "{name}" into view ({before}→{after}px)'}
            return {"ok": True, "moved": delta,
                    "detail": f"scrolled {direction} {abs(delta)}px ({before}→{after}px)"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def navigate(self, url: str) -> dict[str, Any]:
        page = await self.active_page()
        if not url.startswith(("http://", "https://", "file://")):
            url = "https://" + url
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return {"ok": True, "detail": f"navigated to {url}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
