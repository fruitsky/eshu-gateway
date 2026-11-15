#!/bin/bash
# eshu-fleet-runner.sh — executed on the gateway by the poller via systemd-run
# for Fleet Run (P1b). Runs a fleet command as root with a timeout, then posts
# the result back to the dashboard.
#
# Args: DASHBOARD_URL TARGET_IP CMD_ID TIMEOUT CMD_FILE [OUT_FILE]
#
# BJ2: the gateway token is NOT an argument. The runner runs as root, so it
# reads the token from /etc/eshu-token (0600) instead: a transient unit's
# ExecStart can be read by any local user through `systemctl show` for as
# long as the unit exists, and python argv is readable in /proc. The token
# reaches python through the environment, which only root can read.
set -u

DASHBOARD_URL="$1"
# Older pollers passed the token as $2. Accept that shape for the one update
# cycle before the installer replaces them, but never use the argument: an IP
# contains a dot or a colon (IPv4/IPv6), a token has neither, so the two
# shapes are distinguishable.
if [ -n "${2:-}" ] && [ "${2#*[.:]}" != "$2" ]; then
  TARGET_IP="$2"
  CMD_ID="$3"
  TIMEOUT="${4:-180}"
  CMD_FILE="$5"
  OUT_FILE="${6:-}"
else
  TARGET_IP="$3"
  CMD_ID="$4"
  TIMEOUT="${5:-180}"
  CMD_FILE="$6"
  OUT_FILE="${7:-}"
fi
GATEWAY_TOKEN=$(head -n1 /etc/eshu-token 2>/dev/null | tr -d '[:space:]')
if [ -z "$GATEWAY_TOKEN" ]; then
  echo "No /etc/eshu-token on this gateway; refusing to run the fleet command." >&2
  exit 1
fi
if [ -z "$OUT_FILE" ]; then
  # Older poller (six args) compatibility: a random sibling of the command
  # file, never a fixed path in a world-writable directory.
  OUT_FILE=$(mktemp "${CMD_FILE}.out.XXXXXX") || OUT_FILE="/dev/null"
fi

post_result() {
  local status="$1" exit_code="$2" out_file="$3"
  # The heredoc below is python's program (python3 - reads stdin as the script),
  # so the command output is passed as a FILE ARG — stdin cannot carry it.
  # BJ2: the token is in the environment, not argv. /proc/<pid>/environ is
  # root-only; argv is world-readable while the call runs.
  ESHU_FLEET_TOKEN="$GATEWAY_TOKEN" python3 - "$DASHBOARD_URL" "$TARGET_IP" "$CMD_ID" "$status" "$exit_code" "$out_file" <<'PYEOF'
import os, sys, json, urllib.request
url, ip, cid, status, exit_code, out_file = sys.argv[1:7]
tok = os.environ.get("ESHU_FLEET_TOKEN", "")
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
