#!/bin/bash
set -eo pipefail
IFS=$'\n\t'

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/eshu-gateway
export LANG=C.UTF-8

for v in $(env | awk -F= '{print $1}'); do
  case "$v" in PATH|HOME|LANG|SSH_CONNECTION|SSH_ORIGINAL_COMMAND|USER|ESHU_HTTP_TIMEOUT|ESHU_HTTP_CONNECT_TIMEOUT|ESHU_JIT_WAIT) ;; *) unset "$v" ;; esac
done

TARGET_IP="__TARGET_IP__"
DASHBOARD_URL="__DASHBOARD_URL__"
GATEWAY_VERSION="__GATEWAY_VERSION__"
GATEWAY_TOKEN="__GATEWAY_TOKEN__"
TICKETS_FILE="/var/run/eshu.tickets"

# Runtime config. The installer bakes a URL, but /etc/eshu/dashboard_url wins so
# an operator can move the dashboard (e.g. LAN HTTP for a slow/ARM gateway)
# without reinstalling. Timeouts are generous enough for TLS on ARMv6; the
# previous 2s values made every policy/ticket call fail on a Pi 1.
if [ -s /etc/eshu/dashboard_url ]; then
  DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || echo "$DASHBOARD_URL")
fi
HTTP_TIMEOUT="${ESHU_HTTP_TIMEOUT:-10}"
HTTP_CONNECT_TIMEOUT="${ESHU_HTTP_CONNECT_TIMEOUT:-3}"
JIT_WAIT_SECONDS="${ESHU_JIT_WAIT:-90}"

# F5: execution results. Capture is opt-in per gateway (/etc/eshu-capture) and
# must never cut the agent's stream: the FIFO reader drains whatever `tail`
# leaves unread, and a disconnect still ends the command at its next write
# (plain `tee` — `-p` would keep it alive after the agent is gone).
ESHU_RUN_DIR="${ESHU_RUN_DIR:-/run/eshu}"
# BI2: every lockbox read-modify-write (this script's claim/remove and the
# poller's cleanup/append) holds flock on one file in the root-only run dir.
TICKETS_LOCK="$ESHU_RUN_DIR/eshu.tickets.lock"
ESHU_RUN_OWNER="${ESHU_RUN_OWNER:-0}"
ESHU_CAPTURE_FILE="${ESHU_CAPTURE_FILE:-/etc/eshu-capture}"
ESHU_OUT_TAIL=""
RESULT_REQUEST_ID=""
RESULT_PATH="allowlist"

# F12: SSH through Eshu over MCP.
# * `--dashboard-key` is the one fixed argument on the dashboard key's forced
#   command. sshd sets it; a caller on the shared key cannot (G6).
# * /etc/eshu-ssh-mode is written by the poller from the policy response:
#   direct+mcp (default) or mcp_only, which requires a valid signature.
DASHBOARD_KEY="0"
if [ "${1:-}" = "--dashboard-key" ]; then DASHBOARD_KEY="1"; fi
ESHU_SSH_MODE_FILE="/etc/eshu-ssh-mode"
# G4: persistent, root-only store. /run is cleared on reboot, and a container
# restarts in seconds, well inside the ±300 s replay window.
ESHU_NONCE_DIR="${ESHU_NONCE_DIR:-/var/lib/eshu}"
ESHU_NONCE_OWNER="${ESHU_NONCE_OWNER:-0}"
ESHU_NONCE_FILE="$ESHU_NONCE_DIR/nonces"
# Prefix state. ESHU_MCP selects the envelope *format* (even for a refusal —
# the caller learns why); it never authorises anything by itself.
ESHU_AGENT=""; ESHU_AGENT_SIG=""; ESHU_TS=""; ESHU_NONCE=""
ESHU_SESSION_ID=""; ESHU_SESSION_SIG=""; ESHU_EXECUTION_ID=""
ESHU_WINDOW_TOKEN=""; ESHU_MCP=""; ESHU_FORCE_JIT=""
ESHU_CHECK_TOKEN=""; ESHU_CHECK_TOKEN_VERIFIED=""
ESHU_MSG_JSON=""
ESHU_MCP_CAP=""

# BE3: the gateway token must never be a curl argv element — any local user
# can read /proc/<pid>/cmdline while the call runs. The header travels on
# stdin; printf is a shell builtin, so the value never leaves this shell.
gateway_header() {
  printf 'X-Gateway-Token: %s\n' "${GATEWAY_TOKEN:-}"
}
ESHU_VERIFIED_AGENT=""
SIG_STATE="none"

# F19: run limits for a row the dashboard approved with no human watching.
# Empty means "no limit" (a human approved this). A limit the gateway cannot
# enforce, or cannot parse, refuses the call: never a partially applied bound.
RUN_LIMIT_SECS=""
RUN_LIMIT_BYTES=""
RUN_LIMIT_STATE="absent"        # absent | valid | invalid
RUN_LIMIT_HIT=""                # '' | time | bytes (what fired)
ESHU_LIMIT_PROBE=""
# L6: the time bound is only correct with GNU timeout (it signals the process
# group; BusyBox signals only the direct child). The capability is reported on
# every request, so a self-updated or rolled-back script is never trusted
# because a row still says v1.
RUN_LIMITS_CAP=""
# Captured first: under `set -o pipefail` a `timeout --version | grep` pipeline
# reports grep's failure too, and the "is it GNU?" question must not depend on
# how the two ends of a pipe are scheduled.
TIMEOUT_VERSION=$(timeout --version 2>/dev/null || true)
if printf '%s' "$TIMEOUT_VERSION" | grep -q 'GNU coreutils'; then
  RUN_LIMITS_CAP="v1"
fi

# W1: one directory check for every file this script creates under /run.
# Deliberately not a `mkdir`: the poller owns the directory's creation.
run_dir_safe() {
  [ -d "$ESHU_RUN_DIR" ] && [ ! -L "$ESHU_RUN_DIR" ] \
    && [ "$(stat -c '%a' "$ESHU_RUN_DIR" 2>/dev/null)" = "700" ] \
    && [ "$(stat -c '%u' "$ESHU_RUN_DIR" 2>/dev/null)" = "$ESHU_RUN_OWNER" ]
}

# L2: three answers, not two. A later response that omits the field does not
# clear an earlier limit, and two limits combine per field with min().
# `python3 -I`: no user site-packages, no PYTHONPATH — the interpreter that
# parses dashboard JSON must not import anything from the environment.
parse_run_limit() {   # $1 = dashboard JSON body. 0 = ok (absent or valid)
  local kind="" secs="" bytes="" triple
  triple=$(printf '%s' "$1" | python3 -I -c '
import json, sys

# AF2: the range check lives here, in the parser, not in bash arithmetic.
# Bash `[ -lt ]` on an integer over 2^63 errors, and inside an `if` that
# error counts as false, so a huge value would pass as valid; GNU timeout
# would then treat it as effectively unbounded.
SECS_MIN, SECS_MAX = 5, 300
BYTES_MIN, BYTES_MAX = 65536, 8388608

try:
    doc = json.load(sys.stdin) or {}
except ValueError:
    print("invalid"); raise SystemExit(0)
unattended = doc.get("unattended") is True
rl = doc.get("run_limit")

def _valid(value):
    if not isinstance(value, dict):
        return None
    s, b = value.get("seconds"), value.get("bytes")
    if isinstance(s, bool) or isinstance(b, bool) \
            or not isinstance(s, int) or not isinstance(b, int):
        return None
    if not (SECS_MIN <= s <= SECS_MAX and BYTES_MIN <= b <= BYTES_MAX):
        return None
    return s, b

if rl is None:
    # AF1: an unattended row must never arrive without a limit; "absent"
    # means "a human approved this" and is only allowed when it is not
    # marked unattended. A sentinel object ({} or null) with unattended
    # true falls through to invalid.
    print("invalid" if unattended else "absent")
else:
    parsed = _valid(rl)
    if parsed is None:
        print("invalid")
    else:
        print("valid\t%d\t%d" % parsed)
' 2>/dev/null) || triple="invalid"
  IFS=$'\t' read -r kind secs bytes <<< "$triple"
  case "$kind" in
    absent)
      return 0 ;;
    valid)
      # Second layer (the Python above is the rule). The digit case keeps a
      # value that is not plain digits out of `[ -lt ]` entirely.
      case "${secs}${bytes}" in ''|*[!0-9]*) RUN_LIMIT_STATE="invalid"; return 1 ;; esac
      case "$secs" in ?|??|???) ;; *) RUN_LIMIT_STATE="invalid"; return 1 ;; esac
      case "$bytes" in ?????|??????|???????) ;; *) RUN_LIMIT_STATE="invalid"; return 1 ;; esac
      if [ "$secs" -lt 5 ] || [ "$secs" -gt 300 ] \
         || [ "$bytes" -lt 65536 ] || [ "$bytes" -gt 8388608 ]; then
        RUN_LIMIT_STATE="invalid"; return 1
      fi
      if [ "$RUN_LIMIT_STATE" = "valid" ]; then
        [ "$secs" -lt "$RUN_LIMIT_SECS" ] && RUN_LIMIT_SECS="$secs"
        [ "$bytes" -lt "$RUN_LIMIT_BYTES" ] && RUN_LIMIT_BYTES="$bytes"
      else
        RUN_LIMIT_SECS="$secs"; RUN_LIMIT_BYTES="$bytes"
      fi
      RUN_LIMIT_STATE="valid"; return 0 ;;
    *) RUN_LIMIT_STATE="invalid"; return 1 ;;
  esac
}

cap_raw()     { if [ -n "$RUN_LIMIT_BYTES" ]; then head -c $((RUN_LIMIT_BYTES + 1)); else cat; fi; }
cap_visible() {
  if [ -n "$RUN_LIMIT_BYTES" ]; then
    head -c $((RUN_LIMIT_BYTES + 1)) | tee "$ESHU_LIMIT_PROBE"
  else
    cat
  fi
}

# TARGET_IP is baked in by the installer and interpolated into JSON bodies.
# Refuse to run if it is not a bare IP (checked once, before any dashboard call).
if [[ ! "$TARGET_IP" =~ ^[0-9A-Fa-f:.]{1,64}$ ]]; then
  logger -t eshu-gateway "INVALID TARGET_IP (length=${#TARGET_IP}) — refusing to run"
  echo "[LOCKED] Invalid gateway TARGET_IP — refusing to run. [Gateway $GATEWAY_VERSION]"
  exit 1
fi

# B1: GNU sed block-buffers its output into a pipe, so a slow command's first
# bytes never reach a closed connection and the command keeps running after its
# agent hangs up (the D1 check timed 39-41 s). `stdbuf -oL` makes sed
# line-buffered. It is found once per call on the fixed PATH; without stdbuf
# (BusyBox) the script falls back to plain sed and says so once, in syslog
# only — never in the agent's stream.
STDBUF_BIN=""
if command -v stdbuf >/dev/null 2>&1; then
  STDBUF_BIN="stdbuf"
else
  logger -t eshu-gateway "BUFFERING: stdbuf is unavailable; a slow command may outlive a disconnect"
fi
SANITIZE_CMD=(sed -E)
if [ -n "$STDBUF_BIN" ]; then
  SANITIZE_CMD=(stdbuf -oL sed -E)
fi

sanitize() {
  "${SANITIZE_CMD[@]}" \
    -e 's/("?(API_KEY|PASSWORD|TOKEN|SECRET|KEY)"?[[:space:]]*[:=][[:space:]]*")[^"]+/\1[REDACTED]/gI' \
    -e 's/(API_KEY|PASSWORD|TOKEN|SECRET|KEY)=([^,[:space:]]+)/\1=[REDACTED]/gI' \
    -e 's/(--pass|--password|--key|-p)[=[:space:]][^[:space:]]+/\1=[REDACTED]/gI'
}

# ── F12 helpers ─────────────────────────────────────────────────────────────

# JSON-escape one string for the envelope's `messages`. Every value here is a
# message this script built (never raw agent text), but the command name can
# appear in a refusal, so quotes/backslashes/control bytes are escaped anyway.
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  s=$(printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037')
  printf '%s' "$s"
}

# A3/G8: the only human-facing writer. Direct mode prints the line; MCP mode
# appends it to the envelope's `messages` and prints nothing, so a command's
# own output can never be mistaken for gateway status.
say() {
  if [ "$ESHU_MCP" = "1" ]; then
    ESHU_MSG_JSON="${ESHU_MSG_JSON:+$ESHU_MSG_JSON,}\"$(json_escape "$*")\""
  else
    printf '%s\n' "$*"
  fi
}

# The envelope is the only stdout in MCP mode, on every path.
emit_envelope() {   # $1 = status, $2 = exit code
  local status="$1" rc="$2" b64="" trunc="false" size=0 rid=0
  if [ -n "$ESHU_MCP_CAP" ] && [ -s "$ESHU_MCP_CAP" ]; then
    size=$(wc -c < "$ESHU_MCP_CAP")
    if [ "$size" -gt 65536 ]; then
      trunc="true"
      b64=$(tail -c 65536 "$ESHU_MCP_CAP" | base64 -w0)
    else
      b64=$(base64 -w0 < "$ESHU_MCP_CAP")
    fi
  fi
  case "${REQUEST_ID_INT:-}" in
    ''|*[!0-9]*) rid=0 ;;
    *) rid="$REQUEST_ID_INT" ;;
  esac
  # N2: request_id is the integer id, never the zero-padded DISPLAY_ID.
  printf 'ESHU-MCP-ENVELOPE {"status":"%s","request_id":%s,"exit_code":%s,"output_b64":"%s","truncated":%s,"messages":[%s]}\n' \
    "$status" "$rid" "$rc" "$b64" "$trunc" "${ESHU_MSG_JSON:-}"
}

# Every refusal/success exit goes through here in MCP mode.
finish() {          # $1 = status, $2 = exit code
  if [ "$ESHU_MCP" = "1" ]; then
    emit_envelope "$1" "$2"
  fi
  exit "$2"
}

# G4: the nonce store lives in a persistent root-only 0700 dir and is written
# with a temp file + rename, never truncated in place.
nonce_store_ready() {
  if [ ! -d "$ESHU_NONCE_DIR" ]; then
    install -d -m 700 -o root -g root "$ESHU_NONCE_DIR" 2>/dev/null || return 1
  fi
  [ ! -L "$ESHU_NONCE_DIR" ] || return 1
  [ "$(stat -c '%a' "$ESHU_NONCE_DIR" 2>/dev/null)" = "700" ] || return 1
  [ "$(stat -c '%u' "$ESHU_NONCE_DIR" 2>/dev/null)" = "$ESHU_NONCE_OWNER" ] || return 1
  return 0
}

# Returns 0 accepted, 2 replay, 3 full, 1 store error.
consume_nonce() {            # $1 = nonce hex, $2 = ts
  local nonce="$1" ts="$2" now lo tmp rc=0
  nonce_store_ready || return 1
  now=$(date +%s)
  lo=$((now - 300))
  tmp=$(mktemp -p "$ESHU_NONCE_DIR" nonces.XXXXXX) || return 1
  # The lock file is separate from the store: a rename must never invalidate
  # the descriptor we hold.
  exec 9>>"$ESHU_NONCE_DIR/nonces.lock" || { rm -f "$tmp"; return 1; }
  flock -w 2 9 || { rm -f "$tmp"; exec 9>&-; return 1; }
  if [ ! -f "$ESHU_NONCE_FILE" ]; then
    : > "$ESHU_NONCE_FILE" 2>/dev/null || rc=1
  fi
  if [ "$rc" = 0 ]; then
    awk -v lo="$lo" '$1 >= lo' "$ESHU_NONCE_FILE" > "$tmp" 2>/dev/null || rc=1
  fi
  # N1: match the nonce on its own, so a repeat is refused whatever ts it
  # carries (the MAC binds ts, but the store must not depend on that).
  if [ "$rc" = 0 ] && awk -v n="$nonce" '$2 == n { f = 1 } END { exit !f }' "$tmp"; then rc=2; fi
  if [ "$rc" = 0 ] && [ "$(wc -l < "$tmp")" -ge 4096 ]; then rc=3; fi
  if [ "$rc" = 0 ]; then printf '%s %s\n' "$ts" "$nonce" >> "$tmp" || rc=1; fi
  # G4b: rename, never truncate the live store. A failed write leaves the
  # previous store intact (the caller refuses the call either way).
  if [ "$rc" = 0 ]; then mv -f "$tmp" "$ESHU_NONCE_FILE" 2>/dev/null || rc=1; fi
  rm -f "$tmp"
  exec 9>&-
  return "$rc"
}

log_auto_approve() {
  local status="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  # Safe concatenation: TARGET_IP is checked at startup, the ESHU_* prefix
  # values are checked after parsing (both against strict charsets), status is
  # an internal literal and enc_cmd is base64. Any new field must be validated
  # to a safe charset or JSON-escaped before it is added here.
  { gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/log" \
       -H @- \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"'"$status"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'"}' ; } >/dev/null 2>&1 &
}

log_window_reject() {
  local reason="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  # Safe concatenation: TARGET_IP and the ESHU_* prefix values are validated,
  # enc_cmd is base64, and reason is an internal literal supplied by a feature
  # script. Any new field (or a reason that ever becomes external input) must
  # be validated to a safe charset or JSON-escaped before it is added here.
  # BH3: the window token is a credential, so the body (which carries it) goes
  # on stdin, never in argv; the header uses a private process substitution for
  # the same reason.
  local body
  body='{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"window-rejected","reason":"'"$reason"'","token":"'"$ESHU_WINDOW_TOKEN"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'"}'
  printf '%s' "$body" | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/log" \
       -H @<(gateway_header) \
       -H "Content-Type: application/json" \
       --data-binary @- >/dev/null 2>&1 &
}

# F5: pass every line through to the agent AND keep the last 16 KiB of it for
# the execution result. One process, bounded memory (≈ 16 K + one line).
#
# This used to be `tee` into a FIFO with a second `tail` reader. The reader is
# a child the script must wait for, and on some hosts (GitHub's CI runners,
# reproducibly) that reader never saw EOF and the call hangs — a gateway call
# that never returns is a root process that never returns. Fusing the "keep the
# tail" step into the stream itself removes the second process, the FIFO and
# the wait; the capture file is written by END, in the same process.
# S1: the MCP envelope still uses the ring that way (its stream goes to
# /dev/null, not to the agent). `tail` mode now feeds the ring a `tee` copy
# again, through capture_job below, with a bounded poll in place of the wait.
#
# C2: a ring of lines with a running byte total, so the cost is linear in the
# output. (The first version re-copied a growing 16 KiB buffer for every line:
# 26.6 s for `seq 2000000` with gawk.) 20000 slots is enough because at most
# 16385 lines can be retained (one empty line costs one byte), so a live slot
# is never overwritten.
# C3: LC_ALL=C — the cap counts bytes. In a UTF-8 locale gawk counts
# characters, and mawk and gawk disagree about invalid byte sequences.
# C4: portable awk cannot see a missing final newline (only gawk's RT), so a
# stream that does not end in "\n" stores one extra newline. `tee` was
# byte-exact; this is accepted and documented in § 2.7 and pinned by a test.
capture_tail() {   # $1 = capture file, $2 = byte cap (default 16385)
  # C1: the capture is best effort. If the file cannot be written (tmpfs full,
  # the path replaced), awk exits non-zero at END — after the agent already got
  # every line — and no diagnostic is pushed into the agent's stream.
  local cap_bytes="${2:-16385}"
  # F12: the MCP envelope needs the last 64 KiB + 1, F5 the last 16 KiB + 1;
  # one ring serves both. The ring must hold at most cap_bytes lines (a line is
  # at least one byte), so cap_bytes + 2 slots can never overwrite a live one.
  LC_ALL=C awk -v cap="$1" -v limit="$cap_bytes" -v ring="$((cap_bytes + 2))" '
    BEGIN { head = 1 }
    { print; fflush()
      n++
      slot = n % ring
      line[slot] = $0
      size[slot] = length($0) + 1
      total += size[slot]
      while (n - head >= 1 && total - size[head % ring] >= limit) {
        # C5: free the slot. Advancing `head` alone kept old lines alive.
        slot = head % ring
        total -= size[slot]
        delete line[slot]
        delete size[slot]
        head++
      } }
    END {
      for (i = head; i <= n; i++) {
        s = line[i % ring]
        if (i == head && total > limit) s = substr(s, total - limit + 1)
        printf "%s\n", s > cap
      }
      close(cap) }' 2>/dev/null \
    || { logger -t eshu-gateway "CAPTURE FAILED: cannot write the capture file"
         return 1; }
}

# S1 (tee capture): in `tail` mode the capture is a copy of the visible stream,
# not a step in it. awk in the stream held a slow writer after the agent hung
# up (mawk reads stdin in 4 KiB blocks: 10.7 s measured); `tee` writes as it
# reads, so the disconnect reaches the command at its next write.
# This runs in a process substitution, with stdout and stderr on /dev/null so
# it never holds the agent's channel open. The ring writes `part`; only a
# complete capture is renamed to `tail`, the one name the result post reads.
# `cat` drains anything left if awk stopped early, so `tee` never sees EPIPE
# from this side and the agent's stream is never cut (D1). Its last step is
# always removing `part`: that is the "done" signal run_sanitized polls for.
capture_job() {   # $1 = part file, $2 = final file
  if capture_tail "$1" >/dev/null; then
    mv -fT "$1" "$2" 2>/dev/null \
      || logger -t eshu-gateway "CAPTURE FAILED: cannot rename the capture file"
  fi
  cat >/dev/null
  rm -rf -- "$1" 2>/dev/null
}

# S1: the bounded wait for capture_job. A file poll, never `wait`: the process
# substitution is a child of the `tee` pipeline element, not of this script,
# and a capture that never finishes must not hold a root call (the CI-hang
# shape). 40 ticks of 0.05 s = 2 s; a normal capture is done in a tick or two.
CAPTURE_WAIT_TICKS=40
capture_wait() {   # $1 = part file; 0 when the capture finished in time
  local ticks=0
  while [ -e "$1" ] || [ -L "$1" ]; do
    if [ "$ticks" -ge "$CAPTURE_WAIT_TICKS" ]; then return 1; fi
    sleep 0.05
    ticks=$((ticks + 1))
  done
  return 0
}

capture_mode() {
  # off | exit-only | tail. Anything else (missing file, garbage, a symlink)
  # is exit-only: an unknown value must never turn capture on.
  local v="exit-only"
  if [ -f "$ESHU_CAPTURE_FILE" ] && [ ! -L "$ESHU_CAPTURE_FILE" ]; then
    # D8: no trailing-newline surprise from any writer.
    IFS= read -r v < "$ESHU_CAPTURE_FILE" || true
    case "$v" in off|exit-only|tail) ;; *) v="exit-only" ;; esac
  fi
  printf '%s' "$v"
}

post_exec_result() {   # $1=rc  $2=duration_ms  $3=mode  $4=path
  local rc="$1" duration_ms="$2" mode="$3" path="$4"
  local enc_cmd enc_out="" truncated="false" rid="" body
  if [ "$mode" = "off" ]; then return 0; fi
  enc_cmd=$(printf '%s' "$cmd" | base64 -w 0)
  # F12: in MCP mode the F5 tail is the last 16 KiB of the envelope capture
  # (the same stream, one reader).
  local out_src="$ESHU_OUT_TAIL"
  if [ -n "$ESHU_MCP_CAP" ] && [ -s "$ESHU_MCP_CAP" ]; then out_src="$ESHU_MCP_CAP"; fi
  # R4: the operator's capture setting decides what is stored, in MCP mode
  # too (§2.5). `off` posts nothing, `exit-only` stores the exit code and
  # duration only, `tail` stores the tail. The envelope already carried the
  # bytes to the agent; being more auditable does not override an explicit
  # choice to keep output off the dashboard.
  # F19: a byte cut is a truncated result even when the stored tail is short.
  if [ "${RUN_LIMIT_HIT:-}" = "bytes" ]; then truncated="true"; fi
  if [ "$mode" = "tail" ] && [ -n "$out_src" ] && [ -f "$out_src" ] \
     && [ -s "$out_src" ]; then
    if [ "$(stat -c '%s' "$out_src")" -gt 16384 ]; then
      truncated="true"
      enc_out=$(tail -c 16384 "$out_src" | base64 -w 0) || enc_out=""
    else
      enc_out=$(base64 -w 0 < "$out_src") || enc_out=""
    fi
  fi
  # JSON body: all interpolated values are internal literals, validated
  # (TARGET_IP at startup, request id is digits-only), arithmetic integers or
  # base64. Any new field must be validated to a safe charset or JSON-escaped
  # before it is added here.
  case "${RESULT_REQUEST_ID:-}" in
    ''|*[!0-9]*) rid="" ;;
    *) rid=',"request_id":'"${RESULT_REQUEST_ID}" ;;
  esac
  # F19: the limit marker travels beside the result; only the three literal
  # values are ever interpolated.
  case "${RUN_LIMIT_HIT:-}" in time|bytes) limit_field=',"limit":"'"$RUN_LIMIT_HIT"'"' ;; *) limit_field="" ;; esac
  body='{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","path":"'"$path"'","exit_code":'"$rc"',"duration_ms":'"$duration_ms"',"encoded_output":"'"$enc_out"'","output_truncated":'"$truncated"''"$rid"''"$limit_field"'}'
  # D2: the body (up to ~22 KB) goes on stdin, never into argv. BG2: the token
  # header is a private process substitution, not a named file in /tmp: it
  # exists only for curl's read, holds no name another process can race, and
  # needs no cleanup (the old mktemp file is gone).
  printf '%s' "$body" | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
       -X POST "$DASHBOARD_URL/api/exec-result" \
       -H @<(gateway_header) \
       -H "Content-Type: application/json" \
       --data-binary @- >/dev/null 2>&1 &
}

run_sanitized() {
  local rc=0 mode start_ns end_ns duration_ms path mcp_mode="0"
  local -a run_argv=("$@")
  mode=$(capture_mode)
  path="${RESULT_PATH:-allowlist}"
  case "$path" in allowlist|window|lockbox|jit) ;; *) path="allowlist" ;; esac
  ESHU_OUT_TAIL=""
  ESHU_CAP_DIR=""
  ESHU_CAP_PART=""
  ESHU_LIMIT_PROBE=""
  RUN_LIMIT_HIT=""
  if [ "$ESHU_MCP" = "1" ]; then mcp_mode="1"; fi

  # L6: a limit without GNU timeout would bound only the direct child (BusyBox
  # signals no process group), so it is refused rather than half-applied.
  if [ -n "$RUN_LIMIT_SECS" ] && [ "$RUN_LIMITS_CAP" != "v1" ]; then
    say "[LOCKED] The run limit cannot be enforced on this gateway (no GNU timeout) — refusing to run."
    finish refused 1
  fi
  if [ -n "$RUN_LIMIT_SECS" ]; then
    # -k, not --kill-after: both GNU and BusyBox understand it.
    run_argv=(timeout -k 5 "$RUN_LIMIT_SECS" "${run_argv[@]}")
  fi

  # W1: the byte limit needs a private file whether or not capture is on, so
  # the directory is checked before anything is created in it.
  if [ -n "$RUN_LIMIT_BYTES" ] && ! run_dir_safe; then
    say "[LOCKED] The run limit cannot be enforced (unsafe $ESHU_RUN_DIR) — refusing to run."
    finish refused 1
  fi

  # F12: an MCP caller always gets the output inside the envelope, whatever
  # /etc/eshu-capture says. The same safety check as F5 guards the directory.
  if [ "$mode" = "tail" ] || [ "$mcp_mode" = "1" ]; then
    if ! run_dir_safe; then
      logger -t eshu-gateway "CAPTURE DISABLED: unsafe $ESHU_RUN_DIR"
      if [ "$mcp_mode" = "1" ]; then
        # The envelope must carry the output; without a safe directory it
        # cannot, so refuse rather than return a silently empty result.
        say "[LOCKED] MCP capture directory is unavailable — refusing to run."
        finish refused 1
      fi
      mode="exit-only"
    else
      if [ "$mcp_mode" = "1" ]; then
        ESHU_MCP_CAP=$(mktemp -p "$ESHU_RUN_DIR" eshu-mcp.XXXXXX) || {
          say "[LOCKED] MCP capture file is unavailable — refusing to run."
          finish refused 1
        }
      elif [ "$mode" = "tail" ]; then
        # S1: a private 0700 directory per call. `part` is where the ring
        # writes, `tail` appears only by rename. The EXIT trap removes the
        # directory, so a capture that finishes after the bounded wait has
        # nowhere left to write.
        if ESHU_CAP_DIR=$(mktemp -d -p "$ESHU_RUN_DIR" eshu-out.XXXXXX) \
           && : > "$ESHU_CAP_DIR/part"; then
          ESHU_CAP_PART="$ESHU_CAP_DIR/part"
          ESHU_OUT_TAIL="$ESHU_CAP_DIR/tail"
          # S1: plain tee drops a failed output and carries on when SIGPIPE
          # is ignored, so after a disconnect it would feed the capture for
          # ever. `--output-error=exit` (coreutils >= 8.24) exits on EPIPE
          # whatever the signal disposition; capture_job drains its side, so
          # the capture pipe itself never fails. Probed only in tail mode;
          # an older tee falls back to plain tee (the sshd case, SIGPIPE at
          # its default, behaves the same).
          TEE_CMD=(tee)
          if tee --output-error=exit </dev/null >/dev/null 2>&1; then
            TEE_CMD=(tee --output-error=exit)
          fi
        else
          logger -t eshu-gateway "CAPTURE DISABLED: mktemp failed in $ESHU_RUN_DIR"
          mode="exit-only"; ESHU_OUT_TAIL=""; ESHU_CAP_PART=""
          if [ -n "$ESHU_CAP_DIR" ]; then rm -rf -- "$ESHU_CAP_DIR" 2>/dev/null || true; fi
          ESHU_CAP_DIR=""
        fi
      fi
    fi
  fi

  # F19: the cut probe. Sanitized bytes only, bounded at bytes + 1, never
  # stored as a result. It exists so "cut" is distinguishable from "ended
  # exactly at the cap" without confusing F5's last-16-KiB capture for a probe.
  if [ -n "$RUN_LIMIT_BYTES" ]; then
    ESHU_LIMIT_PROBE=$(mktemp -p "$ESHU_RUN_DIR" eshu-limit.XXXXXX) || {
      say "[LOCKED] The run limit cannot be enforced (no probe file) — refusing to run."
      finish refused 1
    }
  fi

  if [ -n "$ESHU_CAP_DIR" ] || [ -n "$ESHU_MCP_CAP" ] || [ -n "$ESHU_LIMIT_PROBE" ]; then
    # F5/F19: the first EXIT trap in this script. A later one must extend this,
    # not replace it, or the files leak. Best effort: a file already gone must
    # not change the call's exit status or write into the agent's stream.
    # S1: ESHU_CAP_DIR is only ever a mktemp -d result or empty.
    trap 'rm -f "$ESHU_MCP_CAP" "$ESHU_LIMIT_PROBE" 2>/dev/null || true
          if [ -n "$ESHU_CAP_DIR" ]; then rm -rf -- "$ESHU_CAP_DIR" 2>/dev/null || true; fi' EXIT
  fi

  start_ns=$(date +%s%N)
  # C1: the command's status is the first pipeline element's, never the
  # filter's. `set -o pipefail` is right for a plain pipeline but wrong here:
  # under it an unwritable capture file (awk exits 2) turned rc 0 into 2 and
  # rc 3 into 2, i.e. the trimmer's failure was reported as the command's.
  # `set +e` keeps `set -e` from exiting before the sentinel and the post.
  # F19: cap_raw bounds the raw stream before `sed` can hold a newline-free
  # line in memory; cap_visible is what the agent sees and what decides the
  # marker (redaction can shorten a cut stream, which is an accepted
  # imprecision — no unsanitized byte is ever written).
  set +e
  if [ "$mcp_mode" = "1" ]; then
    # F12 + CI fix + Q3: the envelope capture is the same in-stream ring at
    # 65 537 bytes; the command's own stream must not reach stdout.
    "${run_argv[@]}" 2>&1 | cap_raw | sanitize | cap_visible \
      | capture_tail "$ESHU_MCP_CAP" 65537 >/dev/null
  elif [ "$mode" = "tail" ] && [ -n "$ESHU_OUT_TAIL" ]; then
    # S1: the agent reads tee's stdout; the capture reads a copy. Never
    # `tee -p`: it would keep feeding the capture after the agent hangs up,
    # and the command would never see the broken pipe. The `exec` moves the
    # substitution's OWN stdout/stderr off the agent's channel: a redirect on
    # the function call alone leaves the subshell holding them, and a stuck
    # capture would then keep the SSH session open.
    "${run_argv[@]}" 2>&1 | cap_raw | sanitize | cap_visible \
      | "${TEE_CMD[@]}" >(exec >/dev/null 2>&1
                          capture_job "$ESHU_CAP_PART" "$ESHU_OUT_TAIL")
  else
    "${run_argv[@]}" 2>&1 | cap_raw | sanitize | cap_visible
  fi
  rc=${PIPESTATUS[0]}
  set -e
  end_ns=$(date +%s%N)
  duration_ms=$(( (end_ns - start_ns) / 1000000 ))

  # S1: bounded wait for the tee copy. On timeout the result is posted with
  # no output (as for CAPTURE FAILED); rc, the sentinel and the stream are
  # already the command's. duration_ms is taken before the wait on purpose.
  if [ -n "$ESHU_CAP_PART" ] && ! capture_wait "$ESHU_CAP_PART"; then
    logger -t eshu-gateway "CAPTURE TIMEOUT: posting the result without output"
    ESHU_OUT_TAIL=""
  fi

  # F19: which limit fired. `time` needs rc 124 AND the elapsed time, because a
  # command may exit 124 by itself. `bytes` is the probe byte: one byte over
  # the cap means the stream was cut; never scanning the output for a marker
  # the file itself could contain.
  if [ -n "$RUN_LIMIT_BYTES" ] && [ -s "$ESHU_LIMIT_PROBE" ] \
     && [ "$(stat -c '%s' "$ESHU_LIMIT_PROBE")" -gt "$RUN_LIMIT_BYTES" ]; then
    RUN_LIMIT_HIT="bytes"
  fi
  # AD1: `timeout -k 5` SIGKILLs a command that ignores SIGTERM, and then
  # timeout exits 137, not 124. Both mean the limit fired; the elapsed-time
  # guard still keeps a command that exits 124/137 on its own from being
  # labelled a limit hit.
  case "$rc" in
    124|137)
      if [ -n "$RUN_LIMIT_SECS" ] \
         && [ "$duration_ms" -ge $(( RUN_LIMIT_SECS * 1000 - 200 )) ]; then
        RUN_LIMIT_HIT="time"
      fi
      ;;
  esac
  if [ "$RUN_LIMIT_HIT" = "time" ]; then
    say "   [ESHU limit: stopped after ${RUN_LIMIT_SECS} s]"
  elif [ "$RUN_LIMIT_HIT" = "bytes" ]; then
    say "   [ESHU limit: output cut at ${RUN_LIMIT_BYTES} bytes]"
  fi

  # The exit sentinel is a direct-SSH affordance. In MCP mode the exit code is
  # a field in the envelope, not a stdout line.
  if [ "$rc" -ne 0 ] && [ "$ESHU_MCP" != "1" ]; then
    printf '[ESHU exit=%s]\n' "$rc"
  fi
  post_exec_result "$rc" "$duration_ms" "$mode" "$path"
  finish ran "$rc"
}

if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then exit 1; fi
cmd="$SSH_ORIGINAL_COMMAND"

# Prefix metadata is concatenated into JSON bodies below, so a value that is
# set must match a strict charset (mirrored by core/utils.py on the dashboard).
# G8: the command is logged only after every prefix is stripped, and no prefix
# value, signature or nonce is ever logged. A duplicate key is refused (G2)
# rather than letting the last one win.
_seen_keys=" "
_duplicate_prefix="0"
while :; do
  _word="${cmd%% *}"
  case "$_word" in
    ESHU_*=*) ;;
    *) break ;;
  esac
  _name="${_word%%=*}"
  _value="${_word#*=}"
  case "$_name" in
    ESHU_WINDOW_TOKEN|ESHU_SESSION_ID|ESHU_SESSION_SIG|ESHU_EXECUTION_ID|ESHU_AGENT|ESHU_AGENT_SIG|ESHU_TS|ESHU_NONCE|ESHU_MCP|ESHU_FORCE_JIT|ESHU_CHECK_TOKEN) ;;
    *) break ;;
  esac
  case "$_seen_keys" in *" $_name "*) _duplicate_prefix="1"; break ;; esac
  _seen_keys="$_seen_keys$_name "
  printf -v "$_name" '%s' "$_value"
  case "$cmd" in
    *' '*) cmd="${cmd#* }" ;;
    *) cmd="" ;;
  esac
done

# The returned ssh_prefix ends with a space. An agent/wrapper that joins with
# its own space leaves cmd starting with one — which misses the exact whitelist
# and every ^-anchored regex. The prefix must never change classification, so
# normalise surrounding whitespace before any policy check. G3 guarantees the
# dashboard signed the untrimmed bytes only when there is nothing to trim.
cmd="${cmd#"${cmd%%[![:space:]]*}"}"
cmd="${cmd%"${cmd##*[![:space:]]}"}"


prefix_refusal=""
# R3/G2: callers on the shared key reach this script directly, so the gateway
# itself refuses a command whose first word is still an ESHU_ key. The parse
# loop stops at an unknown key, which would otherwise become part of the
# command; this also catches a known key placed after an unknown one.
if [ -z "$prefix_refusal" ]; then
  case "${cmd%% *}" in
    ESHU_*) prefix_refusal="Unknown ESHU_ prefix — refusing." ;;
  esac
fi
check_charset() {   # $1=name $2=value $3=regex $4=charset
  local name="$1" value="$2" regex="$3" charset="$4"
  if [ -n "$value" ] && [[ ! "$value" =~ $regex ]]; then
    logger -t eshu-gateway "INVALID PREFIX: $name length=${#value} (allowed: $charset)"
    # First failure wins; always return 0 so `set -e` never aborts silently.
    prefix_refusal="${prefix_refusal:-Invalid ESHU_${name} prefix value — allowed: ${charset}.}"
  fi
  return 0
}

if [ "$_duplicate_prefix" = "1" ]; then
  logger -t eshu-gateway "DUPLICATE PREFIX KEY — refusing"
  prefix_refusal="Duplicate ESHU_ prefix key — refusing."
fi
if [ -z "$prefix_refusal" ]; then
  check_charset "SESSION_ID" "${ESHU_SESSION_ID:-}" '^[A-Za-z0-9_-]{1,64}$' "A-Z a-z 0-9 _ -"
  check_charset "SESSION_SIG" "${ESHU_SESSION_SIG:-}" '^[0-9a-f]{64}$' "64 lowercase hex characters"
  check_charset "EXECUTION_ID" "${ESHU_EXECUTION_ID:-}" '^[A-Za-z0-9_.:-]{1,64}$' "A-Z a-z 0-9 _ . : -"
  check_charset "WINDOW_TOKEN" "${ESHU_WINDOW_TOKEN:-}" '^[A-Za-z0-9]{1,64}$' "A-Z a-z 0-9"
  check_charset "AGENT" "${ESHU_AGENT:-}" '^[A-Za-z0-9_.:-]{1,64}$' "A-Z a-z 0-9 _ . : -"
  check_charset "AGENT_SIG" "${ESHU_AGENT_SIG:-}" '^[0-9a-f]{64}$' "64 lowercase hex characters"
  check_charset "TS" "${ESHU_TS:-}" '^[0-9]{1,12}$' "up to 12 digits"
  check_charset "NONCE" "${ESHU_NONCE:-}" '^[0-9a-f]{16,64}$' "16-64 lowercase hex characters"
  check_charset "MCP" "${ESHU_MCP:-}" '^[01]$' "0 or 1"
  check_charset "FORCE_JIT" "${ESHU_FORCE_JIT:-}" '^[01]$' "0 or 1"
  # F22/AV1: the plan check's per-attempt token. base64url, 16-128 chars.
  check_charset "CHECK_TOKEN" "${ESHU_CHECK_TOKEN:-}" '^[A-Za-z0-9_-]{16,128}$' "16-128 base64url characters"
fi

# G2: the six identity keys require a full, valid signature in every ssh_mode.
MANIFEST_PRESENT="0"
if [ -n "${ESHU_AGENT}${ESHU_AGENT_SIG}${ESHU_TS}${ESHU_NONCE}${ESHU_MCP}${ESHU_FORCE_JIT}${ESHU_CHECK_TOKEN}" ]; then
  MANIFEST_PRESENT="1"
fi
if [ -z "$ESHU_MCP" ]; then ESHU_MCP="0"; fi
if [ -z "$ESHU_FORCE_JIT" ]; then ESHU_FORCE_JIT="0"; fi

# S1: ESHU_WINDOW_TOKEN and ESHU_EXECUTION_ID are outside the agent MAC. The
# dashboard never sends them on a signed call, so a signed prefix that carries
# one is refused rather than trusted for the window/execution path.
if [ "$MANIFEST_PRESENT" = "1" ] && [ -z "$prefix_refusal" ] \
   && [ -n "${ESHU_WINDOW_TOKEN}${ESHU_EXECUTION_ID}" ]; then
  prefix_refusal="A signed prefix must not carry ESHU_WINDOW_TOKEN or ESHU_EXECUTION_ID."
fi

if [ "$MANIFEST_PRESENT" = "1" ] && [ -z "$prefix_refusal" ]; then
  if [ -z "$ESHU_AGENT" ] || [ -z "$ESHU_AGENT_SIG" ] || [ -z "$ESHU_TS" ] || [ -z "$ESHU_NONCE" ]; then
    SIG_STATE="bad"
    prefix_refusal="Incomplete ESHU agent prefix — a full signed prefix is required."
  else
    now=$(date +%s)
    ts_int=$((10#$ESHU_TS))
    if [ "$ts_int" -lt $((now - 300)) ] || [ "$ts_int" -gt $((now + 300)) ]; then
      SIG_STATE="bad"
      prefix_refusal="Stale ESHU_TS — the signed prefix is outside the +/-300 s window."
    else
      cmd_hash=$(printf '%s' "$cmd" | sha256sum | cut -d' ' -f1)
      if [ -n "${ESHU_CHECK_TOKEN:-}" ]; then
        # F22/AV1: the v2 canonical form inserts the signed check token
        # before the command hash. The token is inside the MAC, so only the
        # dashboard's own runner can produce a request that carries it.
        canonical=$(printf 'v2\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s' \
          "$ESHU_AGENT" "${ESHU_SESSION_ID:-}" "$ESHU_MCP" "$ESHU_FORCE_JIT" \
          "$ESHU_TS" "$ESHU_NONCE" "$TARGET_IP" "$ESHU_CHECK_TOKEN" "$cmd_hash")
      else
        canonical=$(printf 'v1\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s' \
          "$ESHU_AGENT" "${ESHU_SESSION_ID:-}" "$ESHU_MCP" "$ESHU_FORCE_JIT" \
          "$ESHU_TS" "$ESHU_NONCE" "$TARGET_IP" "$cmd_hash")
      fi
      # `-c`, not a heredoc: the canonical has to arrive on stdin, and a
      # heredoc would take stdin for the program itself.
      if printf '%s' "$canonical" | python3 -I -c '
import hashlib
import hmac
import sys

sig = sys.argv[1].strip()
with open("/etc/eshu-token", "rb") as handle:
    token = handle.read().strip()
key = hmac.new(token, b"eshu-ssh-agent-v1", hashlib.sha256).digest()
want = hmac.new(key, sys.stdin.buffer.read(), hashlib.sha256).hexdigest()
sys.exit(0 if hmac.compare_digest(sig, want) else 1)
' "$ESHU_AGENT_SIG"
      then
        SIG_STATE="ok"
        ESHU_VERIFIED_AGENT="$ESHU_AGENT"
        # AY2: the token is forwarded only after the MAC verified. It is a
        # signed v2 field, never a value a bare prefix can set.
        ESHU_CHECK_TOKEN_VERIFIED="$ESHU_CHECK_TOKEN"
      else
        SIG_STATE="bad"
        prefix_refusal="Invalid ESHU_AGENT_SIG — the signature does not cover this command on this gateway."
      fi
    fi
  fi
fi

# G4c: check the timestamp and the MAC first, and write the nonce only after
# both pass — unsigned garbage must never fill the store.
if [ "$SIG_STATE" = "ok" ] && [ -z "$prefix_refusal" ]; then
  if consume_nonce "$ESHU_NONCE" "$ts_int"; then
    :
  else
    case "$?" in
      2) SIG_STATE="bad"; prefix_refusal="ESHU_NONCE was already used — replayed prefix refused." ;;
      3) SIG_STATE="bad"; prefix_refusal="The nonce store is full — retry in a few minutes." ;;
      *) SIG_STATE="bad"; prefix_refusal="The nonce store is unavailable — refusing the signed call." ;;
    esac
  fi
fi

# G6: a call that arrived on the dashboard key must be signed, in every mode.
if [ -z "$prefix_refusal" ] && [ "$DASHBOARD_KEY" = "1" ] && [ "$SIG_STATE" != "ok" ]; then
  prefix_refusal="The dashboard key requires a valid signed request."
fi

# ssh_mode: mcp_only retires the shared key on this gateway.
SSH_MODE="direct+mcp"
if [ -f "$ESHU_SSH_MODE_FILE" ] && [ ! -L "$ESHU_SSH_MODE_FILE" ]; then
  _mode_value=""
  IFS= read -r _mode_value < "$ESHU_SSH_MODE_FILE" || true
  if [ "$_mode_value" = "mcp_only" ]; then SSH_MODE="mcp_only"; fi
fi
if [ -z "$prefix_refusal" ] && [ "$MANIFEST_PRESENT" = "0" ] && [ "$SSH_MODE" = "mcp_only" ]; then
  prefix_refusal="This gateway only accepts signed MCP requests (mcp_only)."
fi

if [ -z "$prefix_refusal" ] && [ -z "$cmd" ]; then
  prefix_refusal="No command to run."
fi

if [ -n "$prefix_refusal" ]; then
  # R2/G8: only the agent name that passed the MAC check is logged — an
  # unverified value can carry control bytes and forge a log line.
  logger -t eshu-gateway "PREFIX REFUSED from=${SSH_CONNECTION:-unknown} agent=${ESHU_VERIFIED_AGENT:-none} sig=${SIG_STATE:-none} gateway_key=${DASHBOARD_KEY} mode=${SSH_MODE} reason=${prefix_refusal%%[[:space:]]*}"
  say "[LOCKED] ${prefix_refusal} [Gateway $GATEWAY_VERSION]"
  finish refused 1
fi

# G8: one log line, after every prefix is stripped, with no prefix value.
logger -t eshu-gateway "from=${SSH_CONNECTION:-unknown} agent=${ESHU_VERIFIED_AGENT:-none} sig=${SIG_STATE:-none} gateway_key=${DASHBOARD_KEY} mode=${SSH_MODE} cmd=${cmd}"

# ============================================================
# 0. EMERGENCY FREEZE (global circuit breaker — absolute)
# ============================================================
if [ -f /etc/eshu-freeze ] && [ "$(cat /etc/eshu-freeze 2>/dev/null || echo '')" = "1" ]; then
    logger -t eshu-gateway "FLEET FROZEN — rejecting command: $cmd"
    log_auto_approve "$cmd" "frozen"
    say "[LOCKED] The fleet is currently FROZEN — all commands are rejected (including whitelisted and window commands). Contact the operator to unfreeze."
    finish refused 1
fi

# ============================================================
# 1. HARDCODED CORE BLOCKLIST (NON-EDITABLE — SELF-PROTECTION + EVASION)
#    FATAL is reserved for the explicit Eshu paths/tokens below; a false
#    positive here is permanent because no human can approve it. Broader
#    matches (wildcards near esh, any user's authorized_keys) are force-JIT
#    instead: never auto-run, but a human may approve them.
#
#    This is defence in depth, NOT a sandbox: commands run as root, and the
#    real boundary is stage 2a (metacharacters -> JIT) plus human approval.
#    Paths are normalised first so /etc//eshu and /etc/./eshu cannot slip past.
# ============================================================
normalize_paths() {
  local value="$1"
  while [[ "$value" == *"//"* ]]; do
    value="${value//\/\//\/}"
  done
  while [[ "$value" == *"/./"* ]]; do
    value="${value//\/.\//\/}"
  done
  printf '%s' "$value"
}

normalized_cmd=$(normalize_paths "$cmd")
self_access=0
case "$normalized_cmd" in
    *"/usr/local/bin/eshu-"*|*"/etc/eshu"*|*"/var/run/eshu"*|*"/run/eshu"*|*"/var/lib/eshu"*|*"eshu.db"*|*"eshu.tickets"*|*"eshu-session-key"*|*"eshu/features.d"*|*"/etc/sudoers.d/eshu"*)
        self_access=1
        ;;
esac
if [ "$self_access" = "1" ]; then
    logger -t eshu-gateway "HARDCODED BLOCK: Eshu self-access attempt: $cmd"
    log_auto_approve "$cmd" "blocked"
    say "[LOCKED] FATAL: Access to Eshu Gateway files is permanently blocked. [Gateway $GATEWAY_VERSION]"
    finish refused 1
fi

# ============================================================
# 1.5 FORCE-JIT SELF-PROTECTION (never FATAL, never auto-run)
# ============================================================
SELF_JIT="0"
case "$normalized_cmd" in
    *".ssh/authorized_keys"*) SELF_JIT="1" ;;
esac
if [ "$SELF_JIT" = "0" ]; then
    lower_cmd="${normalized_cmd,,}"
    case "$lower_cmd" in
        esh*|*"/"esh*|*" "esh*|*"="esh*)
            case "$normalized_cmd" in
                *'*'*|*'?'*|*'['*) SELF_JIT="1" ;;
            esac
            ;;
    esac
fi
if [ "$SELF_JIT" = "1" ]; then
    logger -t eshu-gateway "SELF-PROTECTION JIT: forcing JIT for: $cmd"
fi

case "$cmd" in
    *'$(which '*|*'`which '*)
        logger -t eshu-gateway "HARDCODED BLOCK: Command substitution evasion detected: $cmd"
        log_auto_approve "$cmd" "blocked"
        say "[LOCKED] FATAL: Command substitution via which is permanently blocked (potential evasion attempt). [Gateway $GATEWAY_VERSION]"
        finish refused 1
        ;;
esac

# ============================================================
# 2. FILE-BASED BLACKLIST (Supplemental Custom Blocks)
# ============================================================
if [ -f /etc/eshu-rblack.txt ] && [ -s /etc/eshu-rblack.txt ]; then
  while IFS= read -r pattern || [ -n "${pattern:-}" ]; do
    [ -z "${pattern:-}" ] && continue
    [[ "${pattern:-}" =~ ^[[:space:]]*# ]] && continue
    # Strip optional ^ and $ anchors for safe substring matching
    clean_pattern="${pattern:-}"
    [[ "$clean_pattern" == ^* ]] && clean_pattern="${clean_pattern#^}"
    [[ "$clean_pattern" == *$ ]] && clean_pattern="${clean_pattern%$}"
    if [[ "$cmd" == *"$clean_pattern"* ]]; then
      logger -t eshu-gateway "BLACKLIST MATCH: $pattern -> $cmd"
      log_auto_approve "$cmd" "blocked"
      say "[LOCKED] ERROR: Command blocked by Central Dashboard Policy (blacklist match: $pattern). [Gateway $GATEWAY_VERSION]"
      finish refused 1
    fi
  done < /etc/eshu-rblack.txt
fi

# ============================================================
# 2.5 SESSION GATE (strict mode — signed, minted session required)
# ============================================================
# The signature proves the id was minted by this dashboard (session_begin).
# The gateway verifies it locally with the key from its last policy poll; no
# per-session sync and no round-trip. Freeze and the blocklists above still
# win, so a genuinely bad command reports the real reason first.
if [ -f /etc/eshu-require-session ] && [ "$(cat /etc/eshu-require-session 2>/dev/null || echo '')" = "1" ]; then
  if [ -z "${ESHU_SESSION_ID:-}" ]; then
    logger -t eshu-gateway "SESSION REQUIRED: no id for command: $cmd"
    log_auto_approve "$cmd" "session-required"
    say "[LOCKED] A minted Session is required. Call session_begin(goal=\"...\", session_id=\"...\") and prefix every command with the returned ssh_prefix (ESHU_SESSION_ID + ESHU_SESSION_SIG). [Gateway $GATEWAY_VERSION]"
    finish refused 1
  fi
  if [ "$SIG_STATE" = "ok" ] && [ -n "${ESHU_VERIFIED_AGENT:-}" ] && [ -n "${ESHU_SESSION_ID:-}" ]; then
    # G2: the agent MAC covers the session id, so a verified MCP caller may
    # vouch for it in place of ESHU_SESSION_SIG (the dashboard minted both).
    logger -t eshu-gateway "SESSION VOUCHED: agent=$ESHU_VERIFIED_AGENT id=$ESHU_SESSION_ID (signed prefix)"
  elif [ -f /etc/eshu-session-key ]; then
    # AF3: a root process's argv is readable by every local user through
    # /proc, so no key or signature goes there. Python reads the key files
    # itself (argv carries only their paths); the id and signature travel in
    # the child's environment, which /proc shows to root only.
    if ! ESHU_CHECK_SID="$ESHU_SESSION_ID" ESHU_CHECK_SIG="${ESHU_SESSION_SIG:-}" \
         python3 -I - /etc/eshu-session-key /etc/eshu-session-key.prev <<'PY'
import hashlib
import hmac
import os
import sys


def read_key(path):
    # A missing or unreadable file is an empty key, as `cat || echo ""` was.
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return handle.read(4096).strip()
    except OSError:
        return ""


sid = os.environ.get("ESHU_CHECK_SID", "")
sig = os.environ.get("ESHU_CHECK_SIG", "")
if not sid or not sig:
    sys.exit(1)
for candidate in (read_key(sys.argv[1]), read_key(sys.argv[2])):
    if not candidate:
        continue
    try:
        expected = hmac.new(bytes.fromhex(candidate), sid.encode(),
                            hashlib.sha256).hexdigest()
    except ValueError:
        continue
    if hmac.compare_digest(expected, sig):
        sys.exit(0)
sys.exit(1)
PY
    then
      logger -t eshu-gateway "SESSION INVALID: id=$ESHU_SESSION_ID for command: $cmd"
      log_auto_approve "$cmd" "session-required"
      say "[LOCKED] Session id/signature is invalid or was not minted by this dashboard. Re-run session_begin and use the new ssh_prefix. [Gateway $GATEWAY_VERSION]"
      finish refused 1
    fi
  else
    # A brand-new gateway can reach its first command before the first policy
    # poll writes the key. Fail open only in that window; once the key exists,
    # a missing/invalid signature is refused.
    logger -t eshu-gateway "SESSION KEY MISSING: allowing $ESHU_SESSION_ID until first policy sync"
  fi
fi

# ============================================================
# 2.6 ENCODED PAYLOAD DETECTION (never auto-approve)
# ============================================================
# A base64/eval wrapper hides the real command from the literal blocklist and
# allowlist. Force JIT so the dashboard can decode it, scan the decoded text,
# and show it to the operator. Windows are skipped for the same reason.
ENCODED_EXEC="0"
case "$cmd" in
  *'base64 -d'*|*'base64 --decode'*|*'base64 -D'*|*'xxd -r'*|*'openssl enc -d'*|*'gzip -d'*|*'eval '*|*'bash -c "$('*|*'sh -c "$('*|*'python -c'*'exec('*|*'python -c'*'eval('*|*'perl -e '*)
    ENCODED_EXEC="1" ;;
esac
if [ "$ENCODED_EXEC" = "1" ]; then
  logger -t eshu-gateway "ENCODED PAYLOAD: forcing JIT for: $cmd"
fi

# ============================================================
# 2.7 SHELL METACHARACTER GATE (never auto-approve)
# ============================================================
# A regex/prefix allowlist that is not fully anchored would otherwise approve
# "allowed-cmd; anything". Any metacharacter means the literal command cannot be
# what the policy author approved, so all auto-run paths (exact, regex and
# prefix windows) are skipped; exact windows still require byte equality. The
# dashboard mirrors this in core/policy_eval.py.
HAS_META="0"
case "$cmd" in
  *';'*|*'&'*|*'|'*|*'`'*|*'$('*|*'>'*|*'<'*|*$'\n'*|*$'\r'*|*\\*)
    HAS_META="1" ;;
esac
if [ "$HAS_META" = "1" ]; then
  logger -t eshu-gateway "META: routing to JIT for: $cmd"
fi

# ============================================================
# 3. EXACT MATCH WHITELIST
# ============================================================
# Zero-Trust gateways skip auto-run — allowlisted commands route to JIT instead.
# Content-based (mirrors /etc/eshu-freeze): the poller always creates the marker
# file (empty when off), so existence alone is NOT enough — only "1" means active.
ZT_ACTIVE="0"
if [ -f /etc/eshu-zero-trust ] && [ "$(cat /etc/eshu-zero-trust 2>/dev/null || echo '')" = "1" ]; then
  ZT_ACTIVE="1"
fi

# F12: force-JIT means the dashboard (agent policy) demanded a human, so no
# auto-run stage may fire here either.
if [ "$ENCODED_EXEC" = "0" ] && [ "$HAS_META" = "0" ] && [ "$SELF_JIT" = "0" ] && [ "$ESHU_FORCE_JIT" != "1" ] && [ "$ZT_ACTIVE" != "1" ] && [ -f /etc/eshu-exact.txt ] && grep -qFx "$cmd" /etc/eshu-exact.txt; then
  log_auto_approve "$cmd" "auto-approved"
  RESULT_PATH="allowlist"
  run_sanitized bash -c "$cmd"
fi

# ============================================================
# 4. REGEX SMART WHITELIST
# ============================================================
if [ -f /etc/eshu-rwhite.txt ] && [ -s /etc/eshu-rwhite.txt ]; then
  while IFS= read -r pattern || [ -n "${pattern:-}" ]; do
    [ -z "${pattern:-}" ] && continue
    [[ "${pattern:-}" =~ ^[[:space:]]*# ]] && continue
    if [ "$ENCODED_EXEC" = "0" ] && [ "$HAS_META" = "0" ] && [ "$SELF_JIT" = "0" ] && [ "$ESHU_FORCE_JIT" != "1" ] && [ "$ZT_ACTIVE" != "1" ] && [[ "$cmd" =~ $pattern ]]; then
      log_auto_approve "$cmd" "auto-approved"
      RESULT_PATH="allowlist"
      run_sanitized bash -c "$cmd"
    fi
  done < /etc/eshu-rwhite.txt
fi

# ============================================================
# 4.5 FEATURE SCRIPTS (downloaded by poller when flag enabled)
# ============================================================
FEATURES_DIR="/etc/eshu/features.d"
# Windows are an auto-run path too: a force-jit command must not be approved
# by one, so the feature scripts are skipped entirely for it.
if [ "$ENCODED_EXEC" = "0" ] && [ "$SELF_JIT" = "0" ] && [ "$ESHU_FORCE_JIT" != "1" ] && [ -d "$FEATURES_DIR" ]; then
  for f in "$FEATURES_DIR"/*.sh; do
    [ -f "$f" ] && . "$f"
  done
fi

# Zero-Trust gateways route non-window commands to JIT (allowlists were skipped above)
if [ "$ZT_ACTIVE" = "1" ] && [ -z "${ESHU_WINDOW_TOKEN:-}" ]; then
  logger -t eshu-gateway "ZERO-TRUST: routing to JIT for approval: $cmd"
fi

# ============================================================
# 5. CLAIM-AND-BURN JIT LOCKBOX
# ============================================================
# Claim exactly one live lockbox ticket whose command is byte-for-byte $cmd,
# burning it and leaving every other line untouched. Returns 0 on a match;
# the caller decides whether to execute. Reused by the "consumed" branch so
# that path cannot execute without a deposited ticket either.
# Parse one lockbox line into T_TS / T_AGENT / T_SESSION / T_CMD.
# G5: a line tagged `v2` is `v2|ts|agent|session|cmd`; a line without the tag
# is an old `ts|cmd` and agent-less, whatever its command looks like.
parse_ticket_line() {
  local f1="" f2="" f3="" f4="" rest=""
  IFS='|' read -r f1 f2 f3 f4 rest <<< "$1"
  if [ "$f1" = "v2" ]; then
    T_TS="$f2"; T_AGENT="$f3"; T_SESSION="$f4"; T_CMD="$rest"
  else
    T_TS="$f1"; T_AGENT=""; T_SESSION=""
    T_CMD="$f2"
    [ -n "$f3" ] && T_CMD="$T_CMD|$f3"
    [ -n "$f4" ] && T_CMD="$T_CMD|$f4"
    [ -n "$rest" ] && T_CMD="$T_CMD|$rest"
  fi
}

ticket_allowed() {   # $1 = ticket agent, $2 = ticket session
  # A signed request claims only its own agent's ticket (and session); an
  # unsigned one claims only an agent-less ticket. Neither may take another
  # agent's approval.
  if [ -n "${ESHU_VERIFIED_AGENT:-}" ] && [ -n "${ESHU_SESSION_ID:-}" ]; then
    [ "$1" = "$ESHU_VERIFIED_AGENT" ] && [ "$2" = "$ESHU_SESSION_ID" ]
  elif [ -n "${ESHU_VERIFIED_AGENT:-}" ]; then
    [ "$1" = "$ESHU_VERIFIED_AGENT" ]
  else
    [ -z "$1" ]
  fi
}

# F19/L3: an unattended row is claimed by id, in the same round trip as the
# request response, so the limit travels with it. The poller never gets one.
parse_claim_body() {   # $1 = /api/ticket JSON body; sets CLAIM_AGENT/SESSION/CMD
  local line
  line=$(printf '%s' "$1" | python3 -I -c '
import json
import sys

ticket = json.load(sys.stdin).get("ticket") or ""
if not isinstance(ticket, str) or "|" not in ticket:
    sys.exit(1)
parts = ticket.split("|", 4)
if parts[0] == "v2":
    if len(parts) < 5:
        sys.exit(1)
    _, ts, agent, session, command = parts
else:
    ts, command = ticket.split("|", 1)
    agent = session = ""
sys.stdout.write("\x1f".join([agent, session, command]))
' 2>/dev/null) || return 1
  CLAIM_AGENT=""; CLAIM_SESSION=""; CLAIM_CMD=""
  IFS=$'\x1f' read -r CLAIM_AGENT CLAIM_SESSION CLAIM_CMD <<< "$line" || return 1
  return 0
}

response_is_approved() {   # $1 = /api/request JSON body
  printf '%s' "$1" | python3 -I -c '
import json, sys
try:
    doc = json.load(sys.stdin) or {}
except ValueError:
    raise SystemExit(1)
sys.exit(0 if doc.get("approved") is True else 1)' 2>/dev/null
}

claim_ticket_by_id() {   # $1 = request id; 0 when the ticket is ours
  CLAIM_BODY=$(gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
    -H @- "$DASHBOARD_URL/api/ticket/$1" 2>/dev/null || true)
  case "$CLAIM_BODY" in *'"ticket":"'*) ;; *) return 1 ;; esac
  parse_claim_body "$CLAIM_BODY" || return 1
  [ "$CLAIM_CMD" = "$cmd" ] || return 1
  ticket_allowed "$CLAIM_AGENT" "$CLAIM_SESSION" || return 1
  return 0
}

# BI2: the lockbox file is written by this script and by the poller. A read,
# rewrite and rename without a lock lets two claims both take the same line,
# and lets the poller's cleanup move back a copy taken before a claim. The
# lock lives in the root-only 0700 run dir; `flock` (util-linux, present on
# every systemd host) is required to claim at all — without it, fail closed
# and let the command go through JIT instead.
tickets_lock() {
  if [ ! -d "$ESHU_RUN_DIR" ]; then
    install -d -m 0700 "$ESHU_RUN_DIR" 2>/dev/null || return 1
  fi
  command -v flock >/dev/null 2>&1 || return 1
  exec 9>>"$TICKETS_LOCK" || return 1
  flock -w 5 9 || { exec 9>&-; return 1; }
  return 0
}

tickets_unlock() {
  flock -u 9 2>/dev/null || true
  # BK4: close the descriptor too, so a command run after the claim cannot
  # inherit the lock (the nonce helper does the same).
  exec 9>&-
}

# BI1: a lockbox line is one line. A CR inside it means the dashboard tried to
# put a line break into the file; a stamp more than a few seconds ahead is not
# a real ticket (and a negative age would never expire).
ticket_line_valid() {   # $1 = ts
  local now
  now=$(date +%s)
  case "$1" in ''|*[!0-9]*) return 1 ;; esac
  [ "$1" -le $(( now + 5 )) ] && [ $(( now - $1 )) -le 90 ]
}

claim_lockbox_ticket() {
  [ -f "$TICKETS_FILE" ] || return 1

  local now tmp matched="no" line
  now=$(date +%s)
  # BI2: the temp file is created in the same root-only directory as the real
  # one, so `mv` is a rename on the same filesystem, not a copy out of /tmp.
  tmp=$(mktemp -p "$ESHU_RUN_DIR" eshu-tickets.XXXXXX) || return 1
  if ! tickets_lock; then
    rm -f "$tmp"
    logger -t eshu-gateway "LOCKBOX LOCK UNAVAILABLE — refusing the claim (command will go through JIT)"
    return 1
  fi
  while IFS= read -r line || [ -n "${line:-}" ]; do
    [ -z "${line:-}" ] && continue
    case "$line" in *$'\r'*) continue ;; esac
    parse_ticket_line "$line"
    ticket_line_valid "$T_TS" || continue
    if [ "$matched" = "no" ] && [ "$T_CMD" = "$cmd" ] && ticket_allowed "$T_AGENT" "$T_SESSION"; then
      matched="yes"
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$TICKETS_FILE"

  mv -f "$tmp" "$TICKETS_FILE"
  chmod 600 "$TICKETS_FILE"
  tickets_unlock

  [ "$matched" = "yes" ]
}


if claim_lockbox_ticket; then
  logger -t eshu-gateway "JIT TICKET CLAIMED AND BURNED FOR: $cmd"
  RESULT_PATH="lockbox"
  run_sanitized bash -c "$cmd"
fi

# ============================================================
# 6. DISPATCH PENDING REQUEST (JIT WITH AUTO-POLL)
# ============================================================
enc_req=$(echo -n "$cmd" | base64 -w 0)
# Safe concatenation: TARGET_IP and the ESHU_* prefix values are validated
# (startup/prefix checks), and enc_req is base64. Any new field must be
# validated to a safe charset or JSON-escaped before it is added here.
# BM2: the body carries the signed check token, so it goes on stdin, never in
# argv (BH3's rule for every credential). The header uses a process
# substitution for the same reason.
REQ_BODY='{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_req"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'","force_jit":'"$([ "$ESHU_FORCE_JIT" = "1" ] && echo true || echo false)"',"run_limits":"'"$RUN_LIMITS_CAP"'","check_token":"'"${ESHU_CHECK_TOKEN_VERIFIED:-}"'"}'
RESPONSE=$(printf '%s' "$REQ_BODY" | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -w "\n%{http_code}" -X POST "$DASHBOARD_URL/api/request" \
     -H @<(gateway_header) \
     -H "Content-Type: application/json" \
     --data-binary @- 2>&1 || echo "CURL_FAILED")

HTTP_STATUS=$(echo "$RESPONSE" | tail -n1)
if [ "${HTTP_STATUS:-}" = "429" ]; then
  logger -t eshu-gateway "Rate limited by dashboard (429) for JIT request: $cmd"
  say "[LOCKED] ERROR: Rate limited by dashboard — too many requests. Try again in a moment."
  finish refused 1
fi
if [ "${HTTP_STATUS:-}" = "403" ]; then
  ENC_MSG=$(echo "$RESPONSE" | head -n1 | python3 -I -c "import sys, json; print(json.load(sys.stdin).get('detail', 'Encoded payload blocked.'))" 2>/dev/null || echo "Encoded payload blocked.")
  logger -t eshu-gateway "ENCODED PAYLOAD BLOCKED: $ENC_MSG"
  say "[LOCKED] $ENC_MSG [Gateway $GATEWAY_VERSION]"
  finish refused 1
fi
# BK2: the dashboard refuses a malformed request (a line break in the command)
# before it creates a row. Show its reason instead of "unreachable".
if [ "${HTTP_STATUS:-}" = "400" ]; then
  REQ_MSG=$(echo "$RESPONSE" | head -n1 | python3 -I -c "import sys, json; d=json.load(sys.stdin).get('detail', 'Request refused.'); print(d if isinstance(d, str) else d.get('message', 'Request refused.'))" 2>/dev/null || echo "Request refused.")
  logger -t eshu-gateway "JIT REQUEST REFUSED: $REQ_MSG"
  say "[LOCKED] $REQ_MSG [Gateway $GATEWAY_VERSION]"
  finish refused 1
fi
if [ "${HTTP_STATUS:-}" != "200" ]; then
  logger -t eshu-gateway "Failed to deliver JIT request to Dashboard. Status: ${HTTP_STATUS:-unknown}"
  say "[LOCKED] ERROR: Command blocked, and JIT Dashboard is unreachable."
  finish refused 1
fi

# The API returns `request_id` (int) and `id` (zero-padded display) — prefer the
# integer, fall back to the old padded id for older dashboards.
REQUEST_ID=$(echo "$RESPONSE" | head -n1 | python3 -I -c "import sys, json; d=json.load(sys.stdin); print(d.get('request_id', d.get('id', 'unknown')))" 2>/dev/null || echo "unknown")
REQUEST_ID_INT=$(printf '%s' "$REQUEST_ID" | sed 's/^0*//')
case "$REQUEST_ID_INT" in
  ''|*[!0-9]*) DISPLAY_ID="$REQUEST_ID"; REQUEST_HINT="" ;;
  *) DISPLAY_ID=$(printf '%06d' "$REQUEST_ID_INT"); REQUEST_HINT=" Poll check_request($REQUEST_ID_INT) if this client disconnects." ;;
esac
# F19/L2: the first limit seen applies to the whole call; an absent field
# leaves it as it was. A present but broken one refuses the call.
if ! parse_run_limit "$(printf '%s' "$RESPONSE" | head -n1)"; then
  say "[LOCKED] Invalid run_limit from the dashboard — refusing to run."
  finish refused 1
fi

if [ "$ESHU_MCP" = "1" ]; then
  # F19/L3: an approved unattended row is claimed here, in the same round
  # trip, so it runs bounded instead of being left for the poller (which never
  # receives these rows) or a re-run. The limit comes beside the ticket.
  if response_is_approved "$(printf '%s' "$RESPONSE" | head -n1)" \
     && claim_ticket_by_id "$REQUEST_ID_INT"; then
    if ! parse_run_limit "$CLAIM_BODY"; then
      say "[LOCKED] Invalid run_limit from the dashboard — refusing to run."
      finish refused 1
    fi
    logger -t eshu-gateway "JIT UNATTENDED CLAIMED IN ONE ROUND TRIP: $cmd"
    RESULT_PATH="jit"
    RESULT_REQUEST_ID="$REQUEST_ID_INT"
    run_sanitized bash -c "$cmd"
  fi
  # §7: an MCP call cannot hang for minutes. The dashboard polls its own row
  # (wait=true) and calls ssh_exec again once approved; the ticket is waiting.
  say "JIT request #$DISPLAY_ID sent to Dashboard. Poll check_request($REQUEST_ID_INT) and call ssh_exec again once approved."
  finish pending 0
fi
say "[LOCKED] Command blocked. JIT request #$DISPLAY_ID sent to Dashboard.$REQUEST_HINT [Gateway $GATEWAY_VERSION]"
say "   Auto-polling for approval (up to ${JIT_WAIT_SECONDS}s)..."

POLL_START=$(date +%s)
TICKET_FOUND="no"
while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - POLL_START))
  if [ "$ELAPSED" -ge "$JIT_WAIT_SECONDS" ]; then
    say "   [TIMEOUT] No approval within ${JIT_WAIT_SECONDS}s. Retry after approval."
    break
  fi

  # Check if operator denied or approved this request (single status API call)
  STATUS_RESP=$(gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H @- "$DASHBOARD_URL/api/request_status/$REQUEST_ID" 2>/dev/null || echo "")
  REQ_STATUS=$(echo "$STATUS_RESP" | python3 -I -c "import sys, json; print(json.load(sys.stdin).get('status','pending'))" 2>/dev/null || echo "pending")
  if [ "$REQ_STATUS" = "denied" ]; then
    say "   [DENIED] JIT request #$DISPLAY_ID was denied by operator."
    finish refused 1
  fi
  if [ "$REQ_STATUS" = "approved" ]; then
    # F19: a re-run can carry the limit on the status response as well.
    if ! parse_run_limit "$STATUS_RESP"; then
      say "[LOCKED] Invalid run_limit from the dashboard — refusing to run."
      finish refused 1
    fi
    # Claim the ticket BEFORE executing. If the claim fails (slow link, 4xx),
    # executing anyway would leave the request "approved" with no jit_consumed
    # audit — exactly how #2393 ran unrecorded. Retry on the next poll instead.
    CLAIM_BODY=$(gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
      -H @- "$DASHBOARD_URL/api/ticket/$REQUEST_ID" 2>/dev/null || true)
    case "$CLAIM_BODY" in
      *'"ticket":"'*)
        # The ticket is "<created_at>|<command>" (db/requests.py). Require the
        # command part to equal the command we just asked approval for; a
        # mismatch is fatal and must never fall through to execution. Parse
        # with the python this path already starts.
        CLAIM_PARSE_OK="no"
        # G5: parse the tag, then compare the agent/session and the command
        # separately. The command is the last field, so its own `|` survives.
        if parse_claim_body "$CLAIM_BODY"; then CLAIM_PARSE_OK="yes"; fi
        if [ "$CLAIM_PARSE_OK" != "yes" ]; then
          logger -t eshu-gateway "JIT CLAIM FAILED for #$REQUEST_ID — malformed ticket, will retry"
        elif ! ticket_allowed "$CLAIM_AGENT" "$CLAIM_SESSION"; then
          logger -t eshu-gateway "JIT CLAIM REFUSED for #$REQUEST_ID — ticket belongs to another agent or session"
          say "[LOCKED] The approved ticket is bound to another agent or session — refusing to run."
          finish refused 1
        elif [ "$CLAIM_CMD" != "$cmd" ]; then
          logger -t eshu-gateway "JIT COMMAND MISMATCH for #$REQUEST_ID — refusing to run"
          say "[LOCKED] Approved command does not match the requested command — refusing to run."
          finish refused 1
        elif ! parse_run_limit "$CLAIM_BODY"; then
          # AF1: the claim response carries the limit. An unattended row
          # without a usable one is refused here, exactly as on the MCP path.
          say "[LOCKED] Invalid run_limit from the dashboard — refusing to run."
          finish refused 1
        else
          TICKET_FOUND="yes"
          break
        fi
        ;;
      *)
        logger -t eshu-gateway "JIT CLAIM FAILED for #$REQUEST_ID — will retry"
        ;;
    esac
  fi
  if [ "$REQ_STATUS" = "consumed" ]; then
    # The poller already consumed it and deposited "<ts>|<cmd>" in the lockbox.
    # Reuse the stage-5 claim-and-burn: execute only if an exact, live line is
    # there; otherwise this is a timeout, never an execution.
    if claim_lockbox_ticket; then
      logger -t eshu-gateway "JIT CONSUMED TICKET CLAIMED FROM LOCKBOX: $cmd"
      TICKET_FOUND="yes"
      break
    fi
    logger -t eshu-gateway "JIT CONSUMED WITHOUT A LOCKBOX TICKET for #$REQUEST_ID"
    say "   [TIMEOUT] JIT request #$DISPLAY_ID is consumed but no matching lockbox ticket was found. Retry after approval."
    finish refused 1
  fi
  if [ "$REQ_STATUS" = "expired" ] || [ "$REQ_STATUS" = "abandoned" ]; then
    say "   [EXPIRED] JIT request #$DISPLAY_ID is $REQ_STATUS. Retry to request a new approval."
    finish refused 1
  fi
  sleep 3
done

if [ "$TICKET_FOUND" = "yes" ]; then
  say "   [OK] Approved! Executing command now..."
  logger -t eshu-gateway "JIT AUTO-POLL: Ticket approved, executing: $cmd"
  RESULT_PATH="jit"
  RESULT_REQUEST_ID="$REQUEST_ID_INT"
  run_sanitized bash -c "$cmd"
else
  exit 1
fi
