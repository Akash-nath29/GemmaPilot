// GemmaPilot sidepanel client.
// Connects to the local backend over WebSocket, renders the agent's flight path
// (observe → plan → act → done), and drives the human-in-the-loop safety gate.

const WS_URL = "ws://127.0.0.1:8765/ws";

const els = {
  log: document.getElementById("log"),
  empty: document.getElementById("empty-state"),
  status: document.getElementById("status"),
  statusLabel: document.querySelector("#status .status__label"),
  input: document.getElementById("input"),
  run: document.getElementById("run-btn"),
  gate: document.getElementById("gate"),
  gateReason: document.getElementById("gate-reason"),
  gateTarget: document.getElementById("gate-target"),
  approve: document.getElementById("approve-btn"),
  deny: document.getElementById("deny-btn"),
};

let ws = null;
let connected = false;
let running = false;
let reconnectTimer = null;

// ---------- connection ----------
function setStatus(text, cls) {
  els.statusLabel.textContent = text;
  els.status.className = "status status--" + cls;
}

function connect() {
  clearTimeout(reconnectTimer);
  setStatus("linking", "busy");
  try {
    ws = new WebSocket(WS_URL);
  } catch {
    return scheduleReconnect();
  }
  ws.onopen = () => { connected = true; setStatus("online", "on"); els.run.disabled = running || !els.input.value.trim(); };
  ws.onmessage = (ev) => { let m; try { m = JSON.parse(ev.data); } catch { return; } handleEvent(m); };
  ws.onclose = () => { connected = false; setStatus("offline", "off"); els.run.disabled = true; scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch {} };
}
function scheduleReconnect() { clearTimeout(reconnectTimer); reconnectTimer = setTimeout(connect, 1500); }
function send(obj) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); }

// ---------- rendering ----------
function clearEmpty() { if (els.empty) { els.empty.remove(); els.empty = null; } }

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// A waypoint on the flight path. `variant` maps to CSS colour (--node).
function addEntry({ variant, kind, headHtml, subHtml, cardClass }) {
  clearEmpty();
  const el = document.createElement("article");
  el.className = "entry entry--" + variant;
  const head = cardClass
    ? `<div class="${cardClass}">${headHtml}</div>`
    : `<p class="entry__head">${headHtml}</p>`;
  el.innerHTML =
    `<div class="entry__rail"><span class="entry__node"></span></div>` +
    `<div class="entry__body">` +
      `<p class="entry__eyebrow"><span class="entry__kind">${esc(kind)}</span></p>` +
      head +
      (subHtml ? `<p class="entry__sub">${subHtml}</p>` : "") +
    `</div>`;
  els.log.appendChild(el);
  els.log.scrollTop = els.log.scrollHeight;
  return el;
}

// Human-readable summary of an action, refs shown as code.
function actionSummary(a) {
  if (!a) return "";
  switch (a.action) {
    case "click": return `Click <code>${esc(a.ref)}</code>`;
    case "fill": return `Fill <code>${esc(a.ref)}</code> with “${esc(a.value)}”`;
    case "scroll": return `Scroll ${esc(a.direction || (a.ref ? "to " + a.ref : "down"))}`;
    case "navigate": return `Go to ${esc(a.value)}`;
    case "get_datetime": return `Check the date &amp; time`;
    case "done": return `Finish`;
    default: return esc(a.action);
  }
}

// ---------- events ----------
function handleEvent(msg) {
  switch (msg.type) {
    case "connected":
      addEntry({ variant: "observe", kind: "Linked", headHtml: `Backend ready · <code>${esc(msg.model)}</code>` });
      break;

    case "accepted":
      addEntry({ variant: "task", kind: "Task", headHtml: esc(msg.instruction), cardClass: "task-card" });
      break;

    case "observation":
      addEntry({
        variant: "observe", kind: "Observe",
        headHtml: `Read the page · ${msg.num_elements} elements`,
        subHtml: `${esc(msg.title || "")}`,
      });
      break;

    case "reasoning":
      addEntry({
        variant: "plan", kind: `Step ${msg.step} · Plan`,
        headHtml: actionSummary(msg),
        subHtml: esc(msg.thought),
      });
      break;

    case "action": {
      const ok = msg.result && msg.result.ok;
      const detail = msg.result ? (msg.result.detail || msg.result.error || "") : "";
      if (msg.status === "denied") {
        addEntry({ variant: "stop", kind: "Held", headHtml: actionSummary(msg.action), subHtml: "Not executed." });
      } else {
        addEntry({ variant: ok ? "act" : "warn", kind: ok ? "Act" : "Retry",
                   headHtml: actionSummary(msg.action), subHtml: esc(detail) });
      }
      break;
    }

    case "confirmation_required":
      showGate(msg);
      break;

    case "confirmation_ack":
      hideGate();
      if (msg.approved) addEntry({ variant: "act", kind: "Cleared", headHtml: "You approved the action." });
      else addEntry({ variant: "stop", kind: "Held", headHtml: "You declined. Nothing was submitted." });
      break;

    case "done":
      addEntry({ variant: "done", kind: "Done", headHtml: esc(msg.result), cardClass: "result-card" });
      break;

    case "complete":
      finishRun();
      break;

    case "error":
      addEntry({ variant: "stop", kind: "Fault", headHtml: esc(msg.message) });
      finishRun();
      setStatus("fault", "err");
      break;
  }
}

// ---------- safety gate ----------
function showGate(msg) {
  els.gateReason.textContent = msg.reason || "This action may be irreversible.";
  if (msg.target && msg.target.name) {
    els.gateTarget.textContent = `${msg.target.role || "control"}: “${msg.target.name}”`;
    els.gateTarget.hidden = false;
  } else {
    els.gateTarget.hidden = true;
  }
  els.gate.classList.remove("hidden");
  setStatus("hold for clearance", "busy");
  els.approve.focus();
}
function hideGate() { els.gate.classList.add("hidden"); }

els.approve.addEventListener("click", () => { send({ type: "confirm", approved: true }); hideGate(); setStatus("flying", "busy"); });
els.deny.addEventListener("click", () => { send({ type: "confirm", approved: false }); hideGate(); });

// ---------- run control ----------
function startRun() {
  const text = els.input.value.trim();
  if (!text || !connected || running) return;
  running = true;
  els.run.disabled = true;
  els.input.disabled = true;
  setStatus("flying", "busy");
  send({ type: "instruction", text });
  els.input.value = "";
  autogrow();
}
function finishRun() {
  running = false;
  els.input.disabled = false;
  els.run.disabled = !connected || !els.input.value.trim();
  if (connected) setStatus("online", "on");
  els.input.focus();
}

function autogrow() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 132) + "px";
}

els.run.addEventListener("click", startRun);
els.input.addEventListener("input", () => { autogrow(); els.run.disabled = !connected || running || !els.input.value.trim(); });
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); startRun(); }
});

// Flight-plan chips: load the instruction, let the pilot edit before launch.
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    els.input.value = chip.dataset.fill;
    autogrow();
    els.input.focus();
    els.run.disabled = !connected || running;
  });
});

connect();
