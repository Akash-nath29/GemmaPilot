#!/usr/bin/env bash
# One-time setup: Python venv + backend deps.
# Requires: python3, and (separately) Ollama with the Gemma model pulled.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Creating virtualenv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing backend requirements"
pip install --upgrade pip >/dev/null
pip install -r backend/requirements.txt

cat <<'EOF'

==> Done.

Still needed (once):
  1. Install Ollama:            https://ollama.com/download
  2. Sign in to Ollama Cloud:  ollama signin
       (the default model gemma4:31b-cloud runs on Ollama Cloud)
       -- alternatives --
       * direct Cloud API: set OLLAMA_API_KEY + OLLAMA_BASE_URL=https://ollama.com
       * fully on-device:  set OLLAMA_MODEL=gemma4:e2b (no sign-in needed)
  3. Make sure Ollama is up:   ollama serve   (usually auto-runs)

Then, in three terminals:
  A)  source .venv/bin/activate && python -m backend.main
  B)  ./scripts/launch_chrome.sh
  C)  load ./extension at chrome://extensions and click the toolbar icon
EOF
