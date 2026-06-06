#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/workspace/tmp/remote-login-browser"
PID_FILE="$RUN_DIR/pids"

if [[ -f "$PID_FILE" ]]; then
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done < "$PID_FILE"
  sleep 1
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done < "$PID_FILE"
fi

rm -f "$PID_FILE" "$RUN_DIR/vnc.pass" "$RUN_DIR/info.env"
echo "Remote login browser stopped."
