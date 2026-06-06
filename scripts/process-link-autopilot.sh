#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 YOUTUBE_URL [--clips 3] [--duration 45]" >&2
  exit 2
fi

cd "$ROOT"

if [[ -f "$ROOT/secrets/local-pipeline.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/secrets/local-pipeline.env"
  set +a
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" mvp.py autopilot "$@"
fi

exec python3 mvp.py autopilot "$@"
