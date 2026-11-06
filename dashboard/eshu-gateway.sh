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

# TARGET_IP is baked in by the installer and interpolated into JSON bodies.
# Refuse to run if it is not a bare IP (checked once, before any dashboard call).
if [[ ! "$TARGET_IP" =~ ^[0-9A-Fa-f:.]{1,64}$ ]]; then
  logger -t eshu-gateway "INVALID TARGET_IP (length=${#TARGET_IP}) — refusing to run"
  echo "[LOCKED] Invalid gateway TARGET_IP — refusing to run. [Gateway $GATEWAY_VERSION]"
  exit 1
fi

sanitize() {
  sed -E \
    -e 's/("?(API_KEY|PASSWORD|TOKEN|SECRET|KEY)"?[[:space:]]*[:=][[:space:]]*")[^"]+/\1[REDACTED]/gI' \
    -e 's/(API_KEY|PASSWORD|TOKEN|SECRET|KEY)=([^,[:space:]]+)/\1=[REDACTED]/gI' \
    -e 's/(--pass|--password|--key|-p)[=[:space:]][^[:space:]]+/\1=[REDACTED]/gI'
}

log_auto_approve() {
  local status="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  # Safe concatenation: TARGET_IP is checked at startup, the ESHU_* prefix
  # values are checked after parsing (both against strict charsets), status is
  # an internal literal and enc_cmd is base64. Any new field must be validated
  # to a safe charset or JSON-escaped before it is added here.
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"'"$status"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' >/dev/null 2>&1 &
}

log_window_reject() {
  local reason="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  # Safe concatenation: TARGET_IP and the ESHU_* prefix values are validated,
  # enc_cmd is base64, and reason is an internal literal supplied by a feature
  # script. Any new field (or a reason that ever becomes external input) must
  # be validated to a safe charset or JSON-escaped before it is added here.
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"window-rejected","reason":"'"$reason"'","token":"'"$ESHU_WINDOW_TOKEN"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' >/dev/null 2>&1 &
}

run_sanitized() {
  local rc=0
  # `set -e` would abort before the sentinel on a failed pipeline; the `||`
  # preserves the real status so the agent can see the outcome without
  # appending its own `echo RC=$?`.
  "$@" 2>&1 | sanitize || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[ESHU exit=$rc]"
  fi
  exit "$rc"
}

logger -t eshu-gateway "from=${SSH_CONNECTION:-unknown} cmd=${SSH_ORIGINAL_COMMAND:-none}"
if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then exit 1; fi
cmd="$SSH_ORIGINAL_COMMAND"

# Prefix metadata is concatenated into JSON bodies below, so a value that is
# set must match a strict charset (mirrored by core/utils.py on the dashboard).
# Refuse before freeze/blocklist handling and before any dashboard call. Log
# only the length — never echo the raw value into syslog.
validate_prefix() {
  local name="$1" value="$2" regex="$3" charset="$4"
  if [ -z "$value" ]; then
    return 0
  fi
  if [[ ! "$value" =~ $regex ]]; then
    logger -t eshu-gateway "INVALID PREFIX: $name length=${#value} (allowed: $charset)"
    echo "[LOCKED] Invalid ESHU_${name} prefix value — allowed: ${charset}. [Gateway $GATEWAY_VERSION]"
    exit 1
  fi
}

# Parse ESHU_* metadata keys from the command prefix (env vars can't cross forced-command SSH).
# Iterative so keys coexist, e.g. "ESHU_WINDOW_TOKEN=abc ESHU_SESSION_ID=def <command>".
while :; do
  case "$cmd" in
    ESHU_WINDOW_TOKEN=*' '*) ESHU_WINDOW_TOKEN="${cmd%% *}"; ESHU_WINDOW_TOKEN="${ESHU_WINDOW_TOKEN#ESHU_WINDOW_TOKEN=}"; cmd="${cmd#* }" ;;
    ESHU_SESSION_ID=*' '*)   ESHU_SESSION_ID="${cmd%% *}";   ESHU_SESSION_ID="${ESHU_SESSION_ID#ESHU_SESSION_ID=}";         cmd="${cmd#* }" ;;
    ESHU_SESSION_SIG=*' '*)  ESHU_SESSION_SIG="${cmd%% *}";  ESHU_SESSION_SIG="${ESHU_SESSION_SIG#ESHU_SESSION_SIG=}";      cmd="${cmd#* }" ;;
    ESHU_EXECUTION_ID=*' '*) ESHU_EXECUTION_ID="${cmd%% *}"; ESHU_EXECUTION_ID="${ESHU_EXECUTION_ID#ESHU_EXECUTION_ID=}";     cmd="${cmd#* }" ;;
    *) break ;;
  esac
done

validate_prefix "SESSION_ID" "${ESHU_SESSION_ID:-}" '^[A-Za-z0-9_-]{1,64}$' "A-Z a-z 0-9 _ -"
validate_prefix "SESSION_SIG" "${ESHU_SESSION_SIG:-}" '^[0-9a-f]{64}$' "64 lowercase hex characters"
validate_prefix "EXECUTION_ID" "${ESHU_EXECUTION_ID:-}" '^[A-Za-z0-9_.:-]{1,64}$' "A-Z a-z 0-9 _ . : -"
validate_prefix "WINDOW_TOKEN" "${ESHU_WINDOW_TOKEN:-}" '^[A-Za-z0-9]{1,64}$' "A-Z a-z 0-9"

# The returned ssh_prefix ends with a space. An agent/wrapper that joins with
# its own space leaves cmd starting with one — which misses the exact whitelist
# and every ^-anchored regex. The prefix must never change classification, so
# normalise surrounding whitespace before any policy check.
cmd="${cmd#"${cmd%%[![:space:]]*}"}"
cmd="${cmd%"${cmd##*[![:space:]]}"}"

# ============================================================
# 0. EMERGENCY FREEZE (global circuit breaker — absolute)
# ============================================================
if [ -f /etc/eshu-freeze ] && [ "$(cat /etc/eshu-freeze 2>/dev/null || echo '')" = "1" ]; then
    logger -t eshu-gateway "FLEET FROZEN — rejecting command: $cmd"
    log_auto_approve "$cmd" "frozen"
    echo "[LOCKED] The fleet is currently FROZEN — all commands are rejected (including whitelisted and window commands). Contact the operator to unfreeze."
    exit 1
fi

# ============================================================
# 1. HARDCODED CORE BLOCKLIST (NON-EDITABLE — SELF-PROTECTION + EVASION)
#    Command-safety patterns (rm -rf, mkfs, dd, firewall flush, power) are
#    shipped by default in the synced blocklist (/etc/eshu-rblack.txt) so a
#    human can relax them from the dashboard. Only the patterns below protect
#    Eshu itself and cannot be withdrawn.
# ============================================================
case "$cmd" in
    *"/usr/local/bin/eshu-"*|*"/etc/eshu-"*|*"/var/run/eshu."*|*"eshu.db"*|*"eshu.db-journal"*|*"eshu.db-wal"*)
        logger -t eshu-gateway "HARDCODED BLOCK: Eshu self-access attempt: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Access to Eshu Gateway files is permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *'$(which '*|*'`which '*)
        logger -t eshu-gateway "HARDCODED BLOCK: Command substitution evasion detected: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command substitution via which is permanently blocked (potential evasion attempt). [Gateway $GATEWAY_VERSION]"
        exit 1
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
      echo "[LOCKED] ERROR: Command blocked by Central Dashboard Policy (blacklist match: $pattern). [Gateway $GATEWAY_VERSION]"
      exit 1
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
    echo "[LOCKED] A minted Session is required. Call session_begin(goal=\"...\", session_id=\"...\") and prefix every command with the returned ssh_prefix (ESHU_SESSION_ID + ESHU_SESSION_SIG). [Gateway $GATEWAY_VERSION]"
    exit 1
  fi
  if [ -f /etc/eshu-session-key ]; then
    SESSION_KEY=$(cat /etc/eshu-session-key 2>/dev/null || echo "")
    SESSION_KEY_PREV=$(cat /etc/eshu-session-key.prev 2>/dev/null || echo "")
    if ! python3 - "$ESHU_SESSION_ID" "${ESHU_SESSION_SIG:-}" "$SESSION_KEY" "$SESSION_KEY_PREV" <<'PY'
import hashlib
import hmac
import sys

sid, sig, key, prev = sys.argv[1:5]
if not sid or not sig:
    sys.exit(1)
for candidate in (key, prev):
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
      echo "[LOCKED] Session id/signature is invalid or was not minted by this dashboard. Re-run session_begin and use the new ssh_prefix. [Gateway $GATEWAY_VERSION]"
      exit 1
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
# 3. EXACT MATCH WHITELIST
# ============================================================
# Zero-Trust gateways skip auto-run — allowlisted commands route to JIT instead.
# Content-based (mirrors /etc/eshu-freeze): the poller always creates the marker
# file (empty when off), so existence alone is NOT enough — only "1" means active.
ZT_ACTIVE="0"
if [ -f /etc/eshu-zero-trust ] && [ "$(cat /etc/eshu-zero-trust 2>/dev/null || echo '')" = "1" ]; then
  ZT_ACTIVE="1"
fi

if [ "$ENCODED_EXEC" = "0" ] && [ "$ZT_ACTIVE" != "1" ] && [ -f /etc/eshu-exact.txt ] && grep -qFx "$cmd" /etc/eshu-exact.txt; then
  log_auto_approve "$cmd" "auto-approved"
  run_sanitized bash -c "$cmd"
fi

# ============================================================
# 4. REGEX SMART WHITELIST
# ============================================================
if [ -f /etc/eshu-rwhite.txt ] && [ -s /etc/eshu-rwhite.txt ]; then
  while IFS= read -r pattern || [ -n "${pattern:-}" ]; do
    [ -z "${pattern:-}" ] && continue
    [[ "${pattern:-}" =~ ^[[:space:]]*# ]] && continue
    if [ "$ENCODED_EXEC" = "0" ] && [ "$ZT_ACTIVE" != "1" ] && [[ "$cmd" =~ $pattern ]]; then
      log_auto_approve "$cmd" "auto-approved"
      run_sanitized bash -c "$cmd"
    fi
  done < /etc/eshu-rwhite.txt
fi

# ============================================================
# 4.5 FEATURE SCRIPTS (downloaded by poller when flag enabled)
# ============================================================
FEATURES_DIR="/etc/eshu/features.d"
if [ "$ENCODED_EXEC" = "0" ] && [ -d "$FEATURES_DIR" ]; then
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
claim_lockbox_ticket() {
  [ -f "$TICKETS_FILE" ] || return 1

  local now tmp matched="no"
  now=$(date +%s)
  tmp=$(mktemp)
  while IFS='|' read -r ticket_ts claimed_cmd || [ -n "${claimed_cmd:-}" ]; do
    if [ -z "${ticket_ts:-}" ]; then continue; fi
    if (( now - ticket_ts <= 90 )); then
      if [ "$matched" = "no" ] && [ "$cmd" = "$claimed_cmd" ]; then
        matched="yes"
      else
        echo "$ticket_ts|$claimed_cmd" >> "$tmp"
      fi
    fi
  done < "$TICKETS_FILE"

  mv -f "$tmp" "$TICKETS_FILE"
  chmod 600 "$TICKETS_FILE"

  [ "$matched" = "yes" ]
}

if claim_lockbox_ticket; then
  logger -t eshu-gateway "JIT TICKET CLAIMED AND BURNED FOR: $cmd"
  run_sanitized bash -c "$cmd"
fi

# ============================================================
# 6. DISPATCH PENDING REQUEST (JIT WITH AUTO-POLL)
# ============================================================
enc_req=$(echo -n "$cmd" | base64 -w 0)
# Safe concatenation: TARGET_IP and the ESHU_* prefix values are validated
# (startup/prefix checks), and enc_req is base64. Any new field must be
# validated to a safe charset or JSON-escaped before it is added here.
RESPONSE=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -w "\n%{http_code}" -X POST "$DASHBOARD_URL/api/request" \
     -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
     -H "Content-Type: application/json" \
     -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_req"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' 2>&1 || echo "CURL_FAILED")

HTTP_STATUS=$(echo "$RESPONSE" | tail -n1)
if [ "${HTTP_STATUS:-}" = "429" ]; then
  logger -t eshu-gateway "Rate limited by dashboard (429) for JIT request: $cmd"
  echo "[LOCKED] ERROR: Rate limited by dashboard — too many requests. Try again in a moment."
  exit 1
fi
if [ "${HTTP_STATUS:-}" = "403" ]; then
  ENC_MSG=$(echo "$RESPONSE" | head -n1 | python3 -c "import sys, json; print(json.load(sys.stdin).get('detail', 'Encoded payload blocked.'))" 2>/dev/null || echo "Encoded payload blocked.")
  logger -t eshu-gateway "ENCODED PAYLOAD BLOCKED: $ENC_MSG"
  echo "[LOCKED] $ENC_MSG [Gateway $GATEWAY_VERSION]"
  exit 1
fi
if [ "${HTTP_STATUS:-}" != "200" ]; then
  logger -t eshu-gateway "Failed to deliver JIT request to Dashboard. Status: ${HTTP_STATUS:-unknown}"
  echo "[LOCKED] ERROR: Command blocked, and JIT Dashboard is unreachable."
  exit 1
fi

# The API returns `request_id` (int) and `id` (zero-padded display) — prefer the
# integer, fall back to the old padded id for older dashboards.
REQUEST_ID=$(echo "$RESPONSE" | head -n1 | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('request_id', d.get('id', 'unknown')))" 2>/dev/null || echo "unknown")
REQUEST_ID_INT=$(printf '%s' "$REQUEST_ID" | sed 's/^0*//')
case "$REQUEST_ID_INT" in
  ''|*[!0-9]*) DISPLAY_ID="$REQUEST_ID"; REQUEST_HINT="" ;;
  *) DISPLAY_ID=$(printf '%06d' "$REQUEST_ID_INT"); REQUEST_HINT=" Poll check_request($REQUEST_ID_INT) if this client disconnects." ;;
esac
echo "[LOCKED] Command blocked. JIT request #$DISPLAY_ID sent to Dashboard.$REQUEST_HINT [Gateway $GATEWAY_VERSION]"
echo "   Auto-polling for approval (up to ${JIT_WAIT_SECONDS}s)..."

POLL_START=$(date +%s)
TICKET_FOUND="no"
while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - POLL_START))
  if [ "$ELAPSED" -ge "$JIT_WAIT_SECONDS" ]; then
    echo "   [TIMEOUT] No approval within ${JIT_WAIT_SECONDS}s. Retry after approval."
    break
  fi

  # Check if operator denied or approved this request (single status API call)
  STATUS_RESP=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/request_status/$REQUEST_ID" 2>/dev/null || echo "")
  REQ_STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status','pending'))" 2>/dev/null || echo "pending")
  if [ "$REQ_STATUS" = "denied" ]; then
    echo "   [DENIED] JIT request #$DISPLAY_ID was denied by operator."
    exit 1
  fi
  if [ "$REQ_STATUS" = "approved" ]; then
    # Claim the ticket BEFORE executing. If the claim fails (slow link, 4xx),
    # executing anyway would leave the request "approved" with no jit_consumed
    # audit — exactly how #2393 ran unrecorded. Retry on the next poll instead.
    CLAIM_BODY=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
      -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/ticket/$REQUEST_ID" 2>/dev/null || true)
    case "$CLAIM_BODY" in
      *'"ticket":"'*)
        # The ticket is "<created_at>|<command>" (db/requests.py). Require the
        # command part to equal the command we just asked approval for; a
        # mismatch is fatal and must never fall through to execution. Parse
        # with the python this path already starts.
        CLAIM_PARSE_OK="no"
        CLAIM_CMD=$(printf '%s' "$CLAIM_BODY" | python3 -c '
import json
import sys

ticket = json.load(sys.stdin).get("ticket") or ""
if not isinstance(ticket, str) or "|" not in ticket:
    sys.exit(1)
sys.stdout.write(ticket.split("|", 1)[1])
' 2>/dev/null) && CLAIM_PARSE_OK="yes"
        if [ "$CLAIM_PARSE_OK" != "yes" ]; then
          logger -t eshu-gateway "JIT CLAIM FAILED for #$REQUEST_ID — malformed ticket, will retry"
        elif [ "$CLAIM_CMD" != "$cmd" ]; then
          logger -t eshu-gateway "JIT COMMAND MISMATCH for #$REQUEST_ID — refusing to run"
          echo "[LOCKED] Approved command does not match the requested command — refusing to run."
          exit 1
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
    echo "   [TIMEOUT] JIT request #$DISPLAY_ID is consumed but no matching lockbox ticket was found. Retry after approval."
    exit 1
  fi
  if [ "$REQ_STATUS" = "expired" ] || [ "$REQ_STATUS" = "abandoned" ]; then
    echo "   [EXPIRED] JIT request #$DISPLAY_ID is $REQ_STATUS. Retry to request a new approval."
    exit 1
  fi
  sleep 3
done

if [ "$TICKET_FOUND" = "yes" ]; then
  echo "   [OK] Approved! Executing command now..."
  logger -t eshu-gateway "JIT AUTO-POLL: Ticket approved, executing: $cmd"
  # Remove consumed ticket from lockbox (awk-based, no nested loop)
  if [ -f "$TICKETS_FILE" ]; then
    awk -F'|' -v cmd="$cmd" '$2 != cmd { print }' "$TICKETS_FILE" > "$TICKETS_FILE.tmp"
    mv -f "$TICKETS_FILE.tmp" "$TICKETS_FILE"
    chmod 600 "$TICKETS_FILE"
  fi
  run_sanitized bash -c "$cmd"
else
  exit 1
fi
