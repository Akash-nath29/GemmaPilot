<div align="center">
  <!-- Replace with your banner: <img src="docs/banner.png" alt="GemmaPilot Agent" width="800" /> -->
  <img src="extension/icons/icon128.png" alt="GemmaPilot Agent" width="96" />

  <h1>GemmaPilot Agent</h1>

  <p><strong>A browser action agent that reads your current tab and acts on it — powered by Gemma 4 on Ollama Cloud — and never submits, pays, or deletes without your OK.</strong></p>

  <img src="https://img.shields.io/badge/inference-Ollama%20Cloud-7ef0c0" alt="Ollama Cloud" />
  <img src="https://img.shields.io/badge/model-Gemma%204%2031B-6ea8fe" alt="Model" />
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License MIT" />
</div>

---

Give it a natural-language instruction and it acts on whatever page you have open. It does three things:

1. **Reads** the accessibility tree of your active Chrome tab (roles, names, values — not raw HTML).
2. **Acts** — scroll, click, fill, navigate — driven by Gemma 4 (`gemma4:31b-cloud`) running on Ollama Cloud, so you get a 31B model with no local GPU.
3. **Stops** before anything irreversible. Any click on a *submit / confirm / pay / buy / delete / send / order / checkout* control pauses the graph and waits for you to approve or deny — enforced in the agent graph, not just the prompt.

The agent, the browser control, and the safety gate all run on your machine; model inference runs on Ollama Cloud. One `ollama signin` and you're going — no GPU, no per-token API wiring. Prefer everything on-device? Point `OLLAMA_MODEL` at a local tag like `gemma4:e2b` and nothing leaves your machine.

## How it works

```
┌────────────────────┐    WebSocket     ┌──────────────────────────────────────┐
│ Chrome side panel   │◄───────────────►│ Python backend (FastAPI + WebSocket)  │
│ · instruction box   │  reasoning log,  │                                       │
│ · live action log   │  confirm gate    │ LangGraph:  read_state → decide       │
│ · approve / deny     │                 │              → act → evaluate → loop  │
└────────────────────┘                  │                    │                  │
                                         │        interrupt() before any         │
                                         │        submit/pay/delete/confirm       │
                                         │                    │ connect_over_cdp  │
                                         │                    ▼                  │
                                         │   your real Chrome (--remote-          │
                                         │   debugging-port), active tab          │
                                         └──────────────────────────────────────┘
```

The backend attaches to your **real** Chrome over CDP with Playwright, so the agent acts on the exact tab you're looking at. A DOM walk assigns every interactive element a stable `ref` (`e12`) and a clean `{ref, role, name, value}` — the model plans one action at a time against that list, which is far more reliable than handing raw HTML or selectors to the model.

## Quick start

You need **Python 3.12+**, **Chrome**, and **[Ollama](https://ollama.com/download)** with an Ollama account (free) for Cloud.

```bash
# 1. Install deps into a venv
./scripts/setup.sh

# 2. Sign in to Ollama Cloud (one time) so *-cloud models work
ollama signin

# 3. Start the backend (also serves the demo site at /demo)
./scripts/run_backend.sh

# 4. Launch a debuggable Chrome (separate profile, remote-debugging on)
./scripts/launch_chrome.sh
```

Then load the extension: open `chrome://extensions` in that Chrome window → enable **Developer mode** → **Load unpacked** → select the `extension/` folder → click the toolbar icon to open the side panel.

Open any page, type an instruction, and watch the log.

> **No local daemon?** Set `OLLAMA_API_KEY` (from [ollama.com](https://ollama.com/settings/keys)) and `OLLAMA_BASE_URL=https://ollama.com` to hit Ollama Cloud directly.
> **Want everything on-device?** Use a local tag — `OLLAMA_MODEL=gemma4:e2b ./scripts/run_backend.sh` (or `gemma2:2b`); no sign-in or key needed.

## Usage

Type instructions into the side panel. It acts on the current tab.

| On… | Try |
|---|---|
| The demo site (`/demo`) | `Scroll down and click the second result` |
| Wikipedia | `Find the infobox and tell me the population` |
| Wikipedia | `Scroll to the References section` |
| Any form | `Fill this form with name Akash, email akash@example.com` |
| Any form | `Submit this form` → **pauses for your confirmation** |
| The demo site | `Book the second flight for Akash` → multi-step, pauses at "Confirm Booking" |

When the agent wants to click something risky, the side panel shows an amber gate with the reason and the target control. Nothing runs until you press **Approve**.

## Configuration

All optional — set as environment variables before starting the backend.

| Env var | Default | Description |
|---|---|---|
| `OLLAMA_MODEL` | `gemma4:31b-cloud` | Ollama model tag (`*-cloud` runs on Ollama Cloud; use a local tag to stay on-device) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local daemon, or `https://ollama.com` for direct Cloud access |
| `OLLAMA_API_KEY` | _(none)_ | Bearer token for direct Cloud access; unset when using a signed-in local daemon |
| `CDP_ENDPOINT` | `http://localhost:9222` | Chrome remote-debugging endpoint |
| `AGENT_PORT` | `8765` | Backend HTTP/WebSocket port |
| `AGENT_MAX_STEPS` | `40` | Runaway guard — max actions per instruction (the agent self-heals and keeps going toward the goal within this) |
| `AGENT_MAX_PAGE_NODES` | `120` | Max elements fed to the model per turn (snapshot is viewport-anchored, so scrolling reveals more) |

## The safety model

The gate is the point of the project. Before executing **any** click, the graph checks the target's role and accessible name; if it's a `button`/`link` whose name contains a risky keyword, the node calls LangGraph's `interrupt()` and the run halts until a human answers over the WebSocket. Because it lives in the graph — not the prompt — the model cannot talk its way past it.

### Known limitations (honest v0.1)

- **The gate is a keyword heuristic, not a guarantee.** It will miss unusual labels ("Complete my journey") — a **false negative** — and will occasionally stop harmless controls ("Send feedback") — a **false positive**. The keyword set lives in `backend/agent/safety.py`. **Next:** have the model classify action risk instead of matching strings.
- **Inference is remote by default.** With `gemma4:31b-cloud`, the active tab's accessibility tree (which can include text you've typed) is sent to Ollama Cloud for each step. Set `OLLAMA_MODEL` to a local tag to keep everything on-device.
- **Heavy client-side SPAs with poor accessibility semantics** (div-soup, no ARIA) degrade gracefully but aren't solved — the tree is only as good as the page's roles/names. Clean, semantic sites work best.
- **No CAPTCHA solving, anti-bot evasion, or auth-walled flows.** Out of scope by design.
- **Single session.** No multi-user, persistence, or mobile support.

## Evals

The safety invariant is tested, not asserted:

```bash
python eval/run_evals.py
```

- **Layer 1** (no deps): the gate classifier fires on exactly the right controls across the full PRD test matrix.
- **Layer 2** (needs deps): drives the **real** LangGraph graph with a fake browser + scripted planner and proves, structurally, that a risky click never executes before approval, that approval lets it through, that denial stops it, and that benign multi-step flows never pause.

```
24/24 checks passed
✅ Safety invariant holds across the test matrix.
```

## Project layout

```
backend/      FastAPI + WebSocket server, LangGraph agent, Playwright/CDP controller
extension/    Chrome MV3 side-panel UI
demo-site/    Self-contained flight-booking demo (served at /demo)
eval/         Safety-invariant eval harness
scripts/      setup / launch-chrome / run-backend helpers
```

## License

[MIT](LICENSE). Permissive, no surprises — use it, fork it, ship it.



claude --resume ef64b6d6-840b-4454-900f-4c4842489e23