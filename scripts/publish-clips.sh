#!/usr/bin/env bash
# publish-clips.sh — Run clip pipeline then publish to shadowtheatre13.com
# Usage: publish-clips.sh YOUTUBE_URL [CLIPS] [DURATION]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIPS_DIR="$ROOT/workspace/clips"
HOSTINGER_USER="u828471719"
HOSTINGER_HOST="145.79.14.55"
HOSTINGER_PORT="65002"
HOSTINGER_KEY="/tmp/ssh_extract/.ssh/id_rsa_ourwardfamily"
HOSTINGER_KNOWN_HOSTS="/tmp/ssh_extract/.ssh/known_hosts"
HOSTINGER_CLIPS_PATH="/home/u828471719/domains/shadowtheatre13.com/public_html/clips"
PUBLIC_BASE_URL="https://shadowtheatre13.com/clips"

URL="${1:-}"
CLIPS="${2:-3}"
DURATION="${3:-45}"

if [[ -z "$URL" ]]; then
  echo "Usage: $0 YOUTUBE_URL [CLIPS] [DURATION]" >&2
  exit 2
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi

# Snapshot clip dir before run
BEFORE=$(ls -1t "$CLIPS_DIR"/*.mp4 2>/dev/null | head -20 || true)

echo "=== Running clip pipeline ==="
bash "$ROOT/scripts/run-clip-pipeline.sh" "$URL" "$CLIPS" "$DURATION" 2>&1 || true

# Find newly created captioned clips (files not in BEFORE snapshot).
# Raw preview clips can contain only a top title overlay, so do not publish them.
AFTER=$(ls -1t "$CLIPS_DIR"/*.mp4 2>/dev/null | head -20 || true)
NEW_FILES=()
while IFS= read -r f; do
  if [[ "$f" != *_subtitled.mp4 ]]; then
    continue
  fi
  if ! echo "$BEFORE" | grep -qF "$f"; then
    NEW_FILES+=("$f")
  fi
done <<< "$AFTER"

if [[ ${#NEW_FILES[@]} -eq 0 ]]; then
  echo "No new subtitled clip files found — pipeline may have failed before local caption render." >&2
  exit 1
fi

echo ""
echo "=== Uploading ${#NEW_FILES[@]} clips to shadowtheatre13.com ==="

PUBLISHED_URLS=()
for f in "${NEW_FILES[@]}"; do
  fname=$(basename "$f")
  echo "  Uploading: $fname"
  scp -i "$HOSTINGER_KEY" \
      -P "$HOSTINGER_PORT" \
      -o "UserKnownHostsFile=$HOSTINGER_KNOWN_HOSTS" \
      -o StrictHostKeyChecking=no \
      -o ConnectTimeout=30 \
      "$f" \
      "$HOSTINGER_USER@$HOSTINGER_HOST:$HOSTINGER_CLIPS_PATH/$fname"
  public_url="$PUBLIC_BASE_URL/$fname"
  PUBLISHED_URLS+=("$public_url")
  echo "  ✓ $public_url"
  if [[ "$fname" =~ ^(.+)_clip([0-9]+)_subtitled\.mp4$ ]]; then
    job_id="${BASH_REMATCH[1]}"
    clip_index="${BASH_REMATCH[2]}"
    "$PY" mvp.py register-preview "$job_id" --clip "$clip_index" --url "$public_url" --source hostinger >/dev/null
    echo "  pending approval: $job_id clip $clip_index"
  fi
done

echo ""
echo "=== Published URLs ==="
for u in "${PUBLISHED_URLS[@]}"; do
  echo "$u"
done
