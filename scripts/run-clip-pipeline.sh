#!/usr/bin/env bash
# One-command video clip pipeline:
#   yt-dlp download -> local ffmpeg render -> faster-whisper captions
#
# Usage: run-clip-pipeline.sh YOUTUBE_URL [CLIPS] [DURATION]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

URL="${1:-}"
CLIPS="${2:-10}"
DURATION="${3:-45}"

if [[ -z "$URL" ]]; then
  echo "Usage: $0 YOUTUBE_URL [CLIPS] [DURATION]" >&2
  exit 2
fi

cd "$ROOT"

# Source local pipeline settings such as YTDLP_COOKIES_FROM_BROWSER.
for env_file in "$ROOT/secrets/local-pipeline.env"; do
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
done

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

OUT_FILE="$(mktemp)"
trap 'rm -f "$OUT_FILE"' EXIT

# Run the autopilot pipeline (yt-dlp -> ffmpeg -> faster-whisper).
# Autopilot prints a JSON result and always exits 0; we inspect the JSON to decide.
"$PY" mvp.py autopilot "$URL" --clips "$CLIPS" --duration "$DURATION" | tee "$OUT_FILE"

# Parse the result JSON and report.
"$PY" - "$OUT_FILE" <<'PY'
import json, sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
status = data.get("status")
job_id = data.get("job_id")
clips = data.get("clips") or []

print("", file=sys.stderr)
print(f"job_id: {job_id}", file=sys.stderr)
print(f"status: {status}", file=sys.stderr)

# A locally-rendered, captioned run produces clip files we can list.
local_files = [c.get("captioned_file") or c.get("file") for c in clips if isinstance(c, dict)]
local_files = [f for f in local_files if f]

if status in ("captioned", "rendered") and local_files:
    print(f"clips ({len(local_files)}):", file=sys.stderr)
    for f in local_files:
        print(f"  {f}", file=sys.stderr)
    sys.exit(0)

# Anything else is a download failure.
err = data.get("error") or {}
print("DOWNLOAD/RENDER FAILED.", file=sys.stderr)
if err:
    print(f"  stage: {err.get('stage')}", file=sys.stderr)
    print(f"  message: {err.get('message')}", file=sys.stderr)
for attempt in data.get("attempts", []):
    print(f"  attempt {attempt.get('provider')}: {attempt.get('status')} "
          f"{attempt.get('message','')}".rstrip(), file=sys.stderr)
sys.exit(1)
PY
