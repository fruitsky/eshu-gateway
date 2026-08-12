#!/bin/bash
set -eo pipefail
IFS=$'\n\t'

export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/eshu-gateway
export LANG=C.UTF-8

for v in $(env | awk -F= '{print $1}'); do
  case "$v" in PATH|HOME|LANG|SSH_CONNECTION|SSH_ORIGINAL_COMMAND|USER) ;; *) unset "$v" ;; esac
done

TARGET_IP="__TARGET_IP__"
DASHBOARD_URL="__DASHBOARD_URL__"
GATEWAY_VERSION="__GATEWAY_VERSION__"
GATEWAY_TOKEN="__GATEWAY_TOKEN__"
TICKETS_FILE="/var/run/eshu.tickets"

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
  curl -m 2 -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"'"$status"'"}' >/dev/null 2>&1 &
}

log_window_reject() {
  local reason="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  curl -m 2 -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"window-rejected","reason":"'"$reason"'","token":"'"$ESHU_WINDOW_TOKEN"'"}' >/dev/null 2>&1 &
}

run_sanitized() { "$@" 2>&1 | sanitize; exit $?; }

logger -t eshu-gateway "from=${SSH_CONNECTION:-unknown} cmd=${SSH_ORIGINAL_COMMAND:-none}"
if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then exit 1; fi
cmd="$SSH_ORIGINAL_COMMAND"

# Parse ESHU_WINDOW_TOKEN from command prefix (env var can't cross forced-command SSH)
case "$cmd" in
  ESHU_WINDOW_TOKEN=*' '*)
    ESHU_WINDOW_TOKEN="${cmd%% *}"; ESHU_WINDOW_TOKEN="${ESHU_WINDOW_TOKEN#ESHU_WINDOW_TOKEN=}"
    cmd="${cmd#* }" ;;
esac

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
# 1. HARDCODED CATASTROPHIC BLOCKLIST (CANNOT BE DISABLED)
# ============================================================
case "$cmd" in
    *"rm -rf"*|*"rm  -rf"*|*"rm   -rf"*|*"rm -fr"*|*"rm -r -f"*|*"rm -f -r"*)
        logger -t eshu-gateway "HARDCODED BLOCK: rm -rf variant detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command contains destructive recursive force delete. Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"/bin/rm"*"-rf"*|*"/bin/rm"*"-fr"*)
        logger -t eshu-gateway "HARDCODED BLOCK: /bin/rm -rf detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command contains destructive recursive force delete (/bin/rm). Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"mkfs"*)
        logger -t eshu-gateway "HARDCODED BLOCK: mkfs detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command contains filesystem format operation (mkfs). Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"dd if="*|*"dd  if="*|*"dd of="*)
        logger -t eshu-gateway "HARDCODED BLOCK: dd raw device access detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command contains raw disk operation (dd). Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"/bin/dd "*)
        logger -t eshu-gateway "HARDCODED BLOCK: /bin/dd detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command contains raw disk operation (/bin/dd). Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"iptables -F"*|*"iptables --flush"*|*"iptables -X"*|*"iptables --delete-chain"*)
        logger -t eshu-gateway "HARDCODED BLOCK: iptables manipulation detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command manipulates firewall rules. Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"ip6tables -F"*|*"ip6tables --flush"*|*"ip6tables -X"*)
        logger -t eshu-gateway "HARDCODED BLOCK: ip6tables manipulation detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command manipulates IPv6 firewall rules. Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"nft flush"*)
        logger -t eshu-gateway "HARDCODED BLOCK: nftables flush detected in: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: Command flushes nftables ruleset. Permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"reboot"*|*"shutdown"*|*"poweroff"*|*"halt"*|*"init 0"*|*"init 6"*|*"telinit 0"*|*"telinit 6"*)
        logger -t eshu-gateway "HARDCODED BLOCK: System power command: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: System power control commands are permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"systemctl reboot"*|*"systemctl poweroff"*|*"systemctl halt"*|*"systemctl isolate reboot"*|*"systemctl isolate poweroff"*|*"systemctl isolate halt"*)
        logger -t eshu-gateway "HARDCODED BLOCK: systemctl power command: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: System power control via systemctl is permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
    *"busybox reboot"*|*"busybox poweroff"*|*"busybox halt"*|*"busybox shutdown"*)
        logger -t eshu-gateway "HARDCODED BLOCK: busybox power command: $cmd"
        log_auto_approve "$cmd" "blocked"
        echo "[LOCKED] FATAL: System power control via busybox is permanently blocked. [Gateway $GATEWAY_VERSION]"
        exit 1
        ;;
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
# 3. EXACT MATCH WHITELIST
# ============================================================
# Zero-Trust gateways skip auto-run — allowlisted commands route to JIT instead.
# Content-based (mirrors /etc/eshu-freeze): the poller always creates the marker
# file (empty when off), so existence alone is NOT enough — only "1" means active.
ZT_ACTIVE="0"
if [ -f /etc/eshu-zero-trust ] && [ "$(cat /etc/eshu-zero-trust 2>/dev/null || echo '')" = "1" ]; then
  ZT_ACTIVE="1"
fi

if [ "$ZT_ACTIVE" != "1" ] && [ -f /etc/eshu-exact.txt ] && grep -qFx "$cmd" /etc/eshu-exact.txt; then
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
    if [ "$ZT_ACTIVE" != "1" ] && [[ "$cmd" =~ $pattern ]]; then
      log_auto_approve "$cmd" "auto-approved"
      run_sanitized bash -c "$cmd"
    fi
  done < /etc/eshu-rwhite.txt
fi

# ============================================================
# 4.5 FEATURE SCRIPTS (downloaded by poller when flag enabled)
# ============================================================
FEATURES_DIR="/etc/eshu/features.d"
if [ -d "$FEATURES_DIR" ]; then
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
if [ -f "$TICKETS_FILE" ]; then
  now=$(date +%s)
  tmp=$(mktemp)
  matched="no"
  while IFS='|' read -r ticket_ts claimed_cmd || [ -n "${claimed_cmd:-}" ]; do
    if [ -z "${ticket_ts:-}" ]; then continue; fi
    if (( now - ticket_ts <= 90 )); then
      if [ "$matched" = "no" ] && [ "$cmd" = "$claimed_cmd" ]; then
        matched="yes"
        logger -t eshu-gateway "JIT TICKET CLAIMED AND BURNED FOR: $cmd"
      else
        echo "$ticket_ts|$claimed_cmd" >> "$tmp"
      fi
    fi
  done < "$TICKETS_FILE"

  mv -f "$tmp" "$TICKETS_FILE"
  chmod 600 "$TICKETS_FILE"

  if [ "$matched" = "yes" ]; then
    run_sanitized bash -c "$cmd"
  fi
fi

# ============================================================
# 6. DISPATCH PENDING REQUEST (JIT WITH AUTO-POLL)
# ============================================================
enc_req=$(echo -n "$cmd" | base64 -w 0)
RESPONSE=$(curl -m 3 -s -w "\n%{http_code}" -X POST "$DASHBOARD_URL/api/request" \
     -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
     -H "Content-Type: application/json" \
     -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_req"'"}' 2>&1 || echo "CURL_FAILED")

HTTP_STATUS=$(echo "$RESPONSE" | tail -n1)
if [ "${HTTP_STATUS:-}" = "429" ]; then
  logger -t eshu-gateway "Rate limited by dashboard (429) for JIT request: $cmd"
  echo "[LOCKED] ERROR: Rate limited by dashboard — too many requests. Try again in a moment."
  exit 1
fi
if [ "${HTTP_STATUS:-}" != "200" ]; then
  logger -t eshu-gateway "Failed to deliver JIT request to Dashboard. Status: ${HTTP_STATUS:-unknown}"
  echo "[LOCKED] ERROR: Command blocked, and JIT Dashboard is unreachable."
  exit 1
fi

REQUEST_ID=$(echo "$RESPONSE" | head -n1 | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', 'unknown'))" 2>/dev/null || echo "unknown")
echo "[LOCKED] Command blocked. JIT Approval #$REQUEST_ID sent to Dashboard. [Gateway $GATEWAY_VERSION]"
echo "   Auto-polling for approval (up to 90s)..."

POLL_START=$(date +%s)
TICKET_FOUND="no"
while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - POLL_START))
  if [ $ELAPSED -ge 90 ]; then
    echo "   [TIMEOUT] No approval within 90s. Retry after approval."
    break
  fi

  # Check if operator denied or approved this request (single status API call)
  STATUS_RESP=$(curl -m 2 -s -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/request_status/$REQUEST_ID" 2>/dev/null || echo "")
  REQ_STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status','pending'))" 2>/dev/null || echo "pending")
  if [ "$REQ_STATUS" = "denied" ]; then
    echo "   [DENIED] JIT Approval #$REQUEST_ID was denied by operator."
    exit 1
  fi
  if [ "$REQ_STATUS" = "approved" ]; then
    # Consume ticket now so poller doesn't write a stale lockbox entry
    curl -m 2 -s -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/ticket/$REQUEST_ID" >/dev/null 2>&1 || true
    TICKET_FOUND="yes"
    break
  fi
  if [ "$REQ_STATUS" = "consumed" ]; then
    # Poller already consumed it; lockbox may have the ticket
    TICKET_FOUND="yes"
    break
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
