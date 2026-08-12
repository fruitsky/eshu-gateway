#!/bin/bash
# eshu-fleet-runner.sh — executed on the gateway by the poller via systemd-run
# for Fleet Run (P1b). Runs a fleet command as root with a timeout, then posts
# the result back to the dashboard.
#
# Args: DASHBOARD_URL GATEWAY_TOKEN TARGET_IP CMD_ID TIMEOUT CMD_FILE
set -u

DASHBOARD_URL="$1"
GATEWAY_TOKEN="$2"
TARGET_IP="$3"
CMD_ID="$4"
TIMEOUT="${5:-180}"
CMD_FILE="$6"
OUT_FILE="/tmp/eshu-fleet-out-$CMD_ID.txt"

post_result() {
  local status="$1" exit_code="$2" out_file="$3"
  # The heredoc below is python's program (python3 - reads stdin as the script),
  # so the command output is passed as a FILE ARG — stdin cannot carry it.
  python3 - "$DASHBOARD_URL" "$GATEWAY_TOKEN" "$TARGET_IP" "$CMD_ID" "$status" "$exit_code" "$out_file" <<'PYEOF'
import os, sys, json, urllib.request
url, tok, ip, cid, status, exit_code, out_file = sys.argv[1:8]
output = ""
truncated = False
try:
    size = os.path.getsize(out_file)
    if size > 1048576:
        truncated = True
    with open(out_file, "rb") as f:
        output = f.read(1048576).decode("utf-8", "replace")
    if truncated:
        output += "\n… [output truncated to 1MB]"
except Exception:
    pass
try:
    code = int(exit_code)
except (ValueError, TypeError):
    code = None
payload = json.dumps({
    "gateway_ip": ip,
    "status": status,
    "exit_code": code,
    "output": output,
}).encode()
req = urllib.request.Request(
    f"{url}/api/fleet/commands/{cid}/result",
    data=payload,
    headers={"Content-Type": "application/json", "X-Gateway-Token": tok},
    method="POST",
)
try:
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
PYEOF
}

# 1. Mark as running
post_result "running" "" "/dev/null"

# 2. Freeze guard — a fleet approved before a freeze must not execute
if [ -f /etc/eshu-freeze ] && [ "$(cat /etc/eshu-freeze 2>/dev/null || echo '')" = "1" ]; then
  echo "Fleet is FROZEN — command not executed." > "$OUT_FILE"
  post_result "failed" 1 "$OUT_FILE"
  rm -f "$CMD_FILE" "$OUT_FILE"
  exit 0
fi

# 3. Execute with timeout (runs as root — the same privilege as gateway scripts).
# --kill-after escalates SIGTERM to SIGKILL 5s later, so a signal-ignoring
# process can't run past the timeout and wedge the per-gateway queue.
: > "$OUT_FILE"
timeout --kill-after=5 "$TIMEOUT" bash "$CMD_FILE" > "$OUT_FILE" 2>&1
RC=$?

# 4. Report result — 124 = timed out (SIGTERM), 137 = SIGKILL after kill-after
if [ "$RC" = "124" ] || [ "$RC" = "137" ]; then
  STATUS="timeout"
elif [ "$RC" = "0" ]; then
  STATUS="success"
else
  STATUS="failed"
fi
post_result "$STATUS" "$RC" "$OUT_FILE"
rm -f "$CMD_FILE" "$OUT_FILE"
exit 0
