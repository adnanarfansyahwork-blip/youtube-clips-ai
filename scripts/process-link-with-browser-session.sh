#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 YOUTUBE_URL [--clips N] [--duration SECONDS]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_ROOT="${YT_AUTOMATION_PROFILE_ROOT:-$ROOT/secrets/browser-profiles}"
PROFILE_NAME="${YT_AUTOMATION_PROFILE_NAME:-yt-automation}"
PROFILE_DIR="$PROFILE_ROOT/$PROFILE_NAME"

cd "$ROOT"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

if command -v firefox >/dev/null 2>&1; then
  export YTDLP_COOKIES_FROM_BROWSER="${YTDLP_COOKIES_FROM_BROWSER:-firefox:$PROFILE_DIR}"
elif command -v google-chrome >/dev/null 2>&1; then
  export YTDLP_COOKIES_FROM_BROWSER="${YTDLP_COOKIES_FROM_BROWSER:-chrome:$PROFILE_DIR}"
elif command -v chromium >/dev/null 2>&1; then
  export YTDLP_COOKIES_FROM_BROWSER="${YTDLP_COOKIES_FROM_BROWSER:-chromium:$PROFILE_DIR}"
fi

exec python mvp.py process-link-free "$@"
