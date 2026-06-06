#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${YT_AUTOMATION_PROFILE_ROOT:-$ROOT/secrets/browser-profiles}"
PROFILE_NAME="${YT_AUTOMATION_PROFILE_NAME:-yt-automation}"
PROFILE_DIR="$PROFILE_ROOT/$PROFILE_NAME"
URL="${1:-https://www.youtube.com/}"

mkdir -p "$PROFILE_DIR"
chmod 700 "$ROOT/secrets" "$PROFILE_ROOT" "$PROFILE_DIR" 2>/dev/null || true

if command -v firefox >/dev/null 2>&1; then
  echo "Opening Firefox automation profile: $PROFILE_DIR"
  exec firefox --profile "$PROFILE_DIR" --no-remote "$URL"
fi

if command -v google-chrome >/dev/null 2>&1; then
  echo "Opening Chrome automation profile: $PROFILE_DIR"
  exec google-chrome --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check "$URL"
fi

if command -v chromium >/dev/null 2>&1; then
  echo "Opening Chromium automation profile: $PROFILE_DIR"
  exec chromium --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check "$URL"
fi

cat >&2 <<EOF
No supported browser found.

Install one of these first:
  - firefox
  - google-chrome
  - chromium

Then run this script again and login to YouTube in the opened automation profile.
EOF
exit 1
