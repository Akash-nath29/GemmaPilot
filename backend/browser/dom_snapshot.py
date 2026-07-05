"""The DOM -> accessibility-tree extraction script.

Playwright's built-in `page.accessibility.snapshot()` gives roles and names but
NOT a handle you can click. So we run our own walk in the page: assign every
"interesting" element a stable `data-agent-ref`, compute its ARIA-ish
{role, name, value}, and return a trimmed, ordered list. The agent then acts by
ref (`click('e12')` -> `[data-agent-ref="e12"]`), which is far more robust than
CSS selectors invented by a 2B model.

The result shape per node is exactly the PRD's tool contract:
    { ref, role, name, value? }  (+ `tag` and `inViewport` as harmless hints)

This runs entirely in the page via page.evaluate — no network, no cloud.
"""

# NOTE: This is browser-side JavaScript stored as a Python string. Keep it
# dependency-free; it must run on arbitrary third-party pages.
PAGE_STATE_JS = r"""
(maxNodes) => {
  const NAME_MAX = 160;
  const results = [];
  let counter = 0;

  // Clear refs from a previous snapshot so ids never go stale across reads.
  for (const el of document.querySelectorAll('[data-agent-ref]')) {
    el.removeAttribute('data-agent-ref');
  }

  const interactiveTags = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY']);
  const interactiveRoles = new Set(['button','link','checkbox','radio','textbox',
    'combobox','menuitem','menuitemcheckbox','tab','switch','searchbox','option','slider']);
  const textTags = new Set(['P','LI','TD','TH','DD','DT','CAPTION','FIGCAPTION',
    'BLOCKQUOTE','LABEL','H1','H2','H3','H4','H5','H6']);
  const landmarkTags = {NAV:'navigation', MAIN:'main', HEADER:'banner',
    FOOTER:'contentinfo', ASIDE:'complementary', FORM:'form', SECTION:'region'};

  function isVisible(el) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    return true;
  }

  function getRole(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.trim().toLowerCase();
    const tag = el.tagName;
    switch (tag) {
      case 'A': return el.hasAttribute('href') ? 'link' : 'generic';
      case 'BUTTON': return 'button';
      case 'SELECT': return 'combobox';
      case 'TEXTAREA': return 'textbox';
      case 'SUMMARY': return 'button';
      case 'IMG': return 'img';
      case 'INPUT': {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (['button','submit','reset','image'].includes(t)) return 'button';
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'range') return 'slider';
        if (t === 'search') return 'searchbox';
        return 'textbox';
      }
    }
    if (/^H[1-6]$/.test(tag)) return 'heading';
    if (landmarkTags[tag]) return landmarkTags[tag];
    if (tag === 'LI') return 'listitem';
    return 'generic';
  }

  function labelFor(el) {
    if (el.id) {
      const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
    }
    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText.trim()) return parentLabel.innerText.trim();
    return '';
  }

  function getName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();

    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map((id) => {
        const e = document.getElementById(id);
        return e ? e.innerText : '';
      });
      const t = parts.join(' ').replace(/\s+/g, ' ').trim();
      if (t) return t;
    }

    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      const lbl = labelFor(el);
      if (lbl) return lbl;
      const ph = el.getAttribute('placeholder');
      if (ph && ph.trim()) return ph.trim();
      const nm = el.getAttribute('name');
      if (nm && nm.trim()) return nm.trim();
      return '';
    }
    if (tag === 'IMG') return (el.getAttribute('alt') || '').trim();

    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) return text;
    const title = el.getAttribute('title');
    return title ? title.trim() : '';
  }

  function getValue(el) {
    const tag = el.tagName;
    if (tag === 'INPUT') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox' || t === 'radio') return el.checked ? 'checked' : 'unchecked';
      return el.value || '';
    }
    if (tag === 'TEXTAREA') return el.value || '';
    if (tag === 'SELECT') return el.value || '';
    return '';
  }

  function hasDirectText(el) {
    for (const node of el.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim().length > 1) return true;
    }
    return false;
  }

  // Pass 1: gather every interesting element (no refs yet), capped hard so a
  // pathological page can't hang us.
  const HARD_CAP = 1500;
  const candidates = [];
  const all = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of all) {
    if (candidates.length >= HARD_CAP) break;
    if (!isVisible(el)) continue;

    const tag = el.tagName;
    const role = getRole(el);
    const tabindex = el.getAttribute('tabindex');
    const isInteractive = interactiveTags.has(tag) || interactiveRoles.has(role) ||
      el.hasAttribute('onclick') || (tabindex !== null && tabindex !== '-1');
    const isHeading = role === 'heading';
    const isLandmark = !!landmarkTags[tag] ||
      ['navigation','main','banner','contentinfo','complementary','form','region','search'].includes(role);
    const isText = textTags.has(tag) && hasDirectText(el);

    if (!(isInteractive || isHeading || isLandmark || isText)) continue;

    let name = getName(el);
    if (name.length > NAME_MAX) name = name.slice(0, NAME_MAX) + '…';
    const value = getValue(el);
    if (!isInteractive && !name && !value) continue;  // skip empty structure

    const rect = el.getBoundingClientRect();
    const inViewport = rect.bottom > 0 && rect.top < window.innerHeight &&
      rect.right > 0 && rect.left < window.innerWidth;
    candidates.push({ el, role, name, value, tag: tag.toLowerCase(),
                      inViewport, top: rect.top });
  }

  // Pass 2: viewport-anchored window. When there are more candidates than we
  // can send, keep the slice from just above the viewport downward — so
  // scrolling REVEALS new content on long pages instead of returning the same
  // top-of-document nodes every time. Backfill near the bottom.
  let start = 0, hiddenAbove = 0, hiddenBelow = 0;
  if (candidates.length > maxNodes) {
    const margin = window.innerHeight * 0.25;
    let anchor = candidates.findIndex((c) => c.top >= -margin);
    if (anchor < 0) anchor = candidates.length - 1;
    start = Math.max(0, Math.min(anchor, candidates.length - maxNodes));
    hiddenAbove = start;
    hiddenBelow = Math.max(0, candidates.length - start - maxNodes);
  }
  const selected = candidates.slice(start, start + maxNodes);

  for (const c of selected) {
    counter += 1;
    const ref = 'e' + counter;
    c.el.setAttribute('data-agent-ref', ref);
    const node = { ref, role: c.role, name: c.name, tag: c.tag, inViewport: c.inViewport };
    if (c.value) node.value = c.value;
    results.push(node);
  }

  // Use whichever scrolling root exists; during a navigation any of these can
  // briefly be null, so fall back safely instead of throwing.
  const root = document.scrollingElement || document.documentElement || document.body;
  const scrollHeight = root ? root.scrollHeight : 0;

  return {
    url: window.location.href,
    title: document.title,
    scrollY: Math.round(window.scrollY),
    scrollHeight: Math.round(scrollHeight),
    viewportHeight: window.innerHeight,
    atPageBottom: (window.innerHeight + window.scrollY) >= (scrollHeight - 4),
    truncated: candidates.length > selected.length,
    hiddenAbove: hiddenAbove,
    hiddenBelow: hiddenBelow,
    nodes: results,
  };
}
"""
