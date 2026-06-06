#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$ROOT/secrets"
RUN_DIR="$ROOT/workspace/tmp/remote-login-browser"
PROFILE_ROOT="${YT_AUTOMATION_PROFILE_ROOT:-$SECRETS/browser-profiles}"
PROFILE_NAME="${YT_AUTOMATION_PROFILE_NAME:-yt-automation}"
PROFILE_DIR="$PROFILE_ROOT/$PROFILE_NAME"
DISPLAY_NUM="${YT_REMOTE_DISPLAY:-88}"
DISPLAY_NAME=":$DISPLAY_NUM"
VNC_PORT="${YT_REMOTE_VNC_PORT:-5908}"
WEB_PORT="${YT_REMOTE_WEB_PORT:-6088}"
URL="${1:-https://www.youtube.com/}"

mkdir -p "$SECRETS" "$RUN_DIR" "$PROFILE_DIR"
chmod 700 "$SECRETS" "$PROFILE_ROOT" "$PROFILE_DIR" "$RUN_DIR" 2>/dev/null || true

"$ROOT/scripts/stop-remote-login-browser.sh" >/dev/null 2>&1 || true

PASSWORD="$(python3 - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits
print("yt-" + "".join(secrets.choice(alphabet) for _ in range(14)))
PY
)"
PASS_FILE="$RUN_DIR/vnc.pass"
PID_FILE="$RUN_DIR/pids"
INFO_FILE="$RUN_DIR/info.env"

printf '%s\n' "$PASSWORD" > "$PASS_FILE"
chmod 600 "$PASS_FILE"

nohup Xvfb "$DISPLAY_NAME" -screen 0 1280x720x24 -nolisten tcp >"$RUN_DIR/xvfb.log" 2>&1 </dev/null &
XVFB_PID=$!
sleep 1

DISPLAY="$DISPLAY_NAME" nohup firefox --profile "$PROFILE_DIR" --no-remote "$URL" >"$RUN_DIR/firefox.log" 2>&1 </dev/null &
FIREFOX_PID=$!
sleep 2

nohup x11vnc -display "$DISPLAY_NAME" -localhost -passwdfile "$PASS_FILE" -rfbport "$VNC_PORT" -forever -shared -quiet >"$RUN_DIR/x11vnc.log" 2>&1 </dev/null &
X11VNC_PID=$!
sleep 1

nohup websockify --web /usr/share/novnc --wrap-mode=ignore "127.0.0.1:$WEB_PORT" "127.0.0.1:$VNC_PORT" >"$RUN_DIR/websockify.log" 2>&1 </dev/null &
WEBSOCKIFY_PID=$!

cat > "$PID_FILE" <<EOF
$XVFB_PID
$FIREFOX_PID
$X11VNC_PID
$WEBSOCKIFY_PID
EOF

cat > "$INFO_FILE" <<EOF
WEB_URL=http://127.0.0.1:$WEB_PORT/vnc.html?host=127.0.0.1&port=$WEB_PORT&autoconnect=1
VNC_PASSWORD=$PASSWORD
PROFILE_DIR=$PROFILE_DIR
DISPLAY=$DISPLAY_NAME
EOF
chmod 600 "$INFO_FILE"

cat <<EOF
Remote login browser is running locally.

Local URL:
  http://127.0.0.1:$WEB_PORT/vnc.html?host=127.0.0.1&port=$WEB_PORT&autoconnect=1

VNC password:
  $PASSWORD

Profile:
  $PROFILE_DIR

This is bound to localhost only. To open from a phone, create a temporary tunnel to port $WEB_PORT, then stop it after login.
EOF

if [[ "${YT_REMOTE_KEEP_FOREGROUND:-0}" == "1" ]]; then
  trap '"$ROOT/scripts/stop-remote-login-browser.sh" >/dev/null 2>&1 || true' EXIT
  wait "$XVFB_PID" "$FIREFOX_PID" "$X11VNC_PID" "$WEBSOCKIFY_PID" || true
fi
