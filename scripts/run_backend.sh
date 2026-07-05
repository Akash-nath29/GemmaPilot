#!/usr/bin/env bash
# Start the FastAPI + WebSocket backend (also serves the demo site at /demo).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
exec python -m backend.main
