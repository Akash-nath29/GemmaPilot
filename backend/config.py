"""Central configuration for the GemmaPilot browser agent.

Everything is overridable via environment variables so the same code runs
on the demo laptop tonight and on a reviewer's machine tomorrow without edits.
"""
from __future__ import annotations

import os

# Load a project-root .env if present, so .env.example actually works.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional; env vars still work without it
    pass


# --- LLM (Ollama Cloud) ------------------------------------------------------
# The agent runs on Ollama Cloud: gemma4:31b-cloud is served by Ollama's
# infrastructure, so no local GPU is needed. Two ways to authenticate:
#   1. `ollama signin` once and keep OLLAMA_BASE_URL=http://localhost:11434 —
#      the local Ollama daemon proxies *-cloud tags to the cloud for you.
#   2. Set OLLAMA_API_KEY and point OLLAMA_BASE_URL=https://ollama.com for direct
#      cloud access with no local daemon.
# (To run fully on-device instead, set OLLAMA_MODEL to a local tag, e.g.
#  gemma4:e2b, and no sign-in / key is required.)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma4:31b-cloud")
OLLAMA_API_KEY: str = os.getenv("OLLAMA_API_KEY", "")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_NUM_CTX: int = int(os.getenv("LLM_NUM_CTX", "8192"))

# --- Chrome / CDP ------------------------------------------------------------
# The user's real Chrome is launched with --remote-debugging-port=<this> and
# Playwright attaches to it over the Chrome DevTools Protocol (Goal G4).
CDP_ENDPOINT: str = os.getenv("CDP_ENDPOINT", "http://localhost:9222")

# --- Server ------------------------------------------------------------------
HOST: str = os.getenv("AGENT_HOST", "127.0.0.1")
PORT: int = int(os.getenv("AGENT_PORT", "8765"))

# --- Agent loop --------------------------------------------------------------
# Runaway guard: the agent self-heals (breaks loops, recovers from errors) and
# keeps going toward the goal, but a hard ceiling still bounds cloud cost and
# prevents true infinite loops. Raise it for very long tasks.
MAX_STEPS: int = int(os.getenv("AGENT_MAX_STEPS", "40"))
# Cap on how many accessibility nodes we feed the model; keeps the prompt
# focused and the per-step latency/cost down.
MAX_PAGE_NODES: int = int(os.getenv("AGENT_MAX_PAGE_NODES", "120"))
