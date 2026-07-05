#!/usr/bin/env bash
# Launch Chrome with the remote debugging port so Playwright (in the backend)
# can attach over CDP. Uses a SEPARATE profile dir so it won't clash with your
# everyday Chrome and always opens a fresh debuggable instance.
#
#   ./scripts/launch_chrome.sh [PORT]     (default port 9222)
#
# Load the extension in THIS window: chrome://extensions -> Developer mode ->
# "Load unpacked" -> select the ./extension folder.
set -euo pipefail

PORT="${1:-9222}"
PROFILE="${CHROME_PROFILE:-$HOME/.gemmapilot-chrome-profile}"
START_URL="${START_URL:-http://127.0.0.1:8765/demo/}"

find_chrome() {
  for c in google-chrome google-chrome-stable chromium chromium-browser brave-browser \
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; do
    if command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

CHROME="$(find_chrome)" || { echo "No Chrome/Chromium found in PATH." >&2; exit 1; }

echo "Launching: $CHROME"
echo "  debug port : $PORT   (CDP endpoint http://localhost:$PORT)"
echo "  profile    : $PROFILE"
echo "  start url  : $START_URL"
echo
echo "Next: chrome://extensions -> Developer mode -> Load unpacked -> ./extension"

exec "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --new-window "$START_URL"
