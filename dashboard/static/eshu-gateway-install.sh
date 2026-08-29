#!/bin/bash
# Eshu Gateway — Zero-Trust JIT Access Gateway (Centralized Dashboard, Policies, Auto-Update & JIT Auto-Poll)

set -euo pipefail
IFS=$'\n\t'

USER="eshu-gateway"
GATEWAY="/usr/local/bin/eshu-gateway.sh"
SUDOERS="/etc/sudoers.d/eshu-gateway"
ALLOWED_SSH_FROM="${ALLOWED_SSH_FROM:-}"
GATEWAY_VERSION=""

REINSTALL="no"
UPDATE="no"
UNINSTALL="no"
YES_UNINSTALL="no"
MIGRATE_HERMES="no"
GATEWAY_PUB_KEY=""
DASHBOARD_URL=""

if [[ "${1:-}" == "--reinstall" ]]; then REINSTALL="yes"; shift || true; fi
if [[ "${1:-}" == "--update" ]]; then UPDATE="yes"; shift || true; fi
if [[ "${1:-}" == "--uninstall" ]]; then UNINSTALL="yes"; shift || true; fi
if [[ "${1:-}" == "--yes" ]]; then YES_UNINSTALL="yes"; shift || true; fi
if [[ "${1:-}" == "--migrate-from-hermes" ]]; then MIGRATE_HERMES="yes"; shift || true; fi
if [[ "${1:-}" != "" ]]; then GATEWAY_PUB_KEY="$1"; fi
if [[ "${2:-}" != "" ]]; then DASHBOARD_URL="$2"; fi

# ── Privilege + init check (all modes: install / update / uninstall / migrate) ──
# Eshu requires root. No auto-sudo here: the installer is often piped from stdin
# (curl | bash), so it cannot reliably re-exec itself. Non-root users get
# actionable guidance instead. All probes are wrapped in `if`/`command -v` so
# `set -euo pipefail` never aborts on a non-root environment.
if [ "$EUID" -ne 0 ]; then
  echo ""
  echo "❌ Eshu Gateway must be installed as root."
  if command -v sudo >/dev/null 2>&1; then
    echo "   Re-run the one-liner prefixed with sudo, e.g.:"
    if [ -n "$DASHBOARD_URL" ]; then
      echo "     curl -sL '$DASHBOARD_URL/api/enroll?token=<token>' | sudo bash"
    else
      echo "     curl -sL '<dashboard-url>/api/enroll?token=<token>' | sudo bash"
    fi
    echo "   (sudo may prompt for your password — that is expected.)"
  else
    echo "   No root or sudo available on this host."
    echo "   Eshu Gateway requires root + systemd."
    echo "   - TrueNAS SCALE / root shells: run the one-liner DIRECTLY (you are already root — no sudo needed)."
    echo "   - Home Assistant OS / other rootless or immutable systems: not supported here."
  fi
  echo ""
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo ""
  echo "❌ Eshu Gateway requires systemd (systemctl), which is missing on this host."
  echo "   Supported: Linux with root + systemd (e.g. TrueNAS SCALE, Debian/Ubuntu)."
  echo "   Home Assistant OS and other non-systemd hosts are not supported."
  echo ""
  exit 1
fi

# Handle --uninstall immediately before any other logic
if [ "$UNINSTALL" = "yes" ]; then
  # Extract DASHBOARD_URL from existing gateway script if not provided
  if [ -z "$DASHBOARD_URL" ] && [ -f /usr/local/bin/eshu-gateway.sh ]; then
    DASHBOARD_URL=$(grep -oP 'DASHBOARD_URL="\K[^"]+' /usr/local/bin/eshu-gateway.sh 2>/dev/null || true)
  fi
  TARGET_IP=$(hostname -I | awk '{print $1}')
  
  echo "🗑 Eshu Gateway Uninstaller"
  echo "============================="
  echo ""
  echo "This will completely remove Eshu Gateway from this host."
  echo "  - User: eshu-gateway"
  echo "  - Gateway script: /usr/local/bin/eshu-gateway.sh"
  echo "  - Poller: /usr/local/bin/eshu-poller.sh + systemd service"
  echo "  - Sudoers: /etc/sudoers.d/eshu-gateway"
  echo "  - Policy files: /etc/eshu-*.txt"
  echo "  - Runtime files: /var/run/eshu.*"
  echo "  - SSH key entries from authorized_keys"
  echo ""
  if [ "$YES_UNINSTALL" != "yes" ]; then
    read -r -p "Type 'yes' to confirm uninstall: " confirm
    if [ "$confirm" != "yes" ]; then
      echo "Uninstall cancelled."
      exit 0
    fi
  else
    echo "Non-interactive mode: proceeding without confirmation."
  fi
  
  # Helper: report progress to dashboard
  _report() {
    local step="$1"
    local message="${2:-}"
    if [ -n "$DASHBOARD_URL" ]; then
      curl -m 3 -s -X POST "$DASHBOARD_URL/api/uninstall-progress" \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$TARGET_IP\",\"step\":\"$step\",\"message\":\"$message\"}" >/dev/null 2>&1 || true
    fi
  }
  
  _report "started" "Uninstall initiated"
  
  echo ""
  echo "Stopping and removing services..."
  systemctl stop eshu-poller 2>/dev/null || true
  systemctl disable eshu-poller 2>/dev/null || true
  rm -f /etc/systemd/system/eshu-poller.service
  systemctl daemon-reload 2>/dev/null || true
  _report "stopping_poller" "Poller service stopped and disabled"
  sleep 1

  echo "Removing binaries..."
  rm -f /usr/local/bin/eshu-gateway.sh
  rm -f /usr/local/bin/eshu-poller.sh
  rm -f /usr/local/bin/eshu-logger.sh
  _report "removing_binaries" "Gateway, poller, and logger scripts removed"
  sleep 1
  
  echo "Removing sudoers..."
  rm -f /etc/sudoers.d/eshu-gateway
  _report "removing_sudoers" "Sudoers file removed"
  sleep 1
  
  echo "Removing policy files..."
  rm -f /etc/eshu-exact.txt /etc/eshu-rwhite.txt /etc/eshu-rblack.txt
  _report "removing_policies" "Local policy files removed"
  sleep 1
  
  echo "Removing runtime files..."
  rm -f /var/run/eshu.tickets /var/run/eshu.last_trigger
  rm -f /var/run/eshu.self_heal_done /var/run/eshu.self_heal_ts
  _report "removing_runtime" "Runtime ticket files removed"
  sleep 1

  echo "Removing logger service..."
  systemctl stop eshu-logger 2>/dev/null || true
  systemctl disable eshu-logger 2>/dev/null || true
  rm -f /etc/systemd/system/eshu-logger.service
  rm -f /usr/local/bin/eshu-logger.sh
  rm -f /etc/eshu/dashboard_url
  rmdir /etc/eshu 2>/dev/null || true
  _report "removing_logger" "Logger service and config removed"
  sleep 1

  echo "Removing user..."
  if id -u eshu-gateway &>/dev/null; then
    userdel -r eshu-gateway 2>/dev/null || true
    _report "removing_user" "User eshu-gateway deleted"
  else
    _report "removing_user" "User eshu-gateway not present (skipped)"
  fi
  sleep 1
  
  echo "Cleaning Eshu SSH keys..."
  if [ -f /root/.ssh/authorized_keys ]; then
    grep -v "eshu-gateway" /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp 2>/dev/null || true
    mv -f /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
  fi
  _report "cleaning_keys" "Eshu SSH authorized_keys cleaned"
  sleep 1

  # ── Legacy Hermes Cleanup (pre-v13 rename) ──────────────
  echo "Cleaning legacy Hermes components..."

  systemctl stop hermes-poller 2>/dev/null || true
  systemctl disable hermes-poller 2>/dev/null || true
  rm -f /etc/systemd/system/hermes-poller.service
  systemctl daemon-reload 2>/dev/null || true

  rm -f /usr/local/bin/hermes-diag.sh
  rm -f /usr/local/bin/hermes-gateway.sh
  rm -f /usr/local/bin/hermes-poller.sh
  rm -f /etc/sudoers.d/hermes-diag

  if id -u hermes-diag &>/dev/null; then
    userdel -r hermes-diag 2>/dev/null || true
  fi

  rm -f /etc/hermes-exact.txt /etc/hermes-rwhite.txt /etc/hermes-rblack.txt
  rm -f /var/run/hermes.tickets /var/run/hermes.last_trigger

  if [ -f /root/.ssh/authorized_keys ]; then
    grep -v "hermes-diag" /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp 2>/dev/null || true
    mv -f /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
  fi

  _report "cleaning_legacy" "Legacy Hermes files removed"
  sleep 1

  # Self-deregister from dashboard
  if [ -n "$DASHBOARD_URL" ]; then
    echo "Deregistering from dashboard..."
    curl -m 3 -s -X DELETE "$DASHBOARD_URL/api/gateways/$TARGET_IP" -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" >/dev/null 2>&1 || true
    _report "deregistering" "Dashboard deregistration sent"
  fi
  sleep 1
  
  _report "complete" "All Eshu Gateway components removed"
  
  echo ""
  echo "✅ Eshu Gateway has been completely removed from this host."
  exit 0
fi

# ── Hermes → Eshu Migration (one-time conversion for legacy v6-v12 gateways) ──
if [ "$MIGRATE_HERMES" = "yes" ]; then
  echo "🔄 Hermes → Eshu Migration"
  echo "============================"
  echo ""

  TARGET_IP=$(hostname -I | awk '{print $1}')

  # Auto-detect DASHBOARD_URL from legacy Hermes files
  if [ -z "${DASHBOARD_URL:-}" ]; then
    for f in /usr/local/bin/hermes-diag.sh /usr/local/bin/hermes-gateway.sh /usr/local/bin/hermes-poller.sh; do
      if [ -f "$f" ]; then
        DASHBOARD_URL=$(grep -oP 'DASHBOARD_URL="\K[^"]+' "$f" 2>/dev/null || true)
        [ -n "$DASHBOARD_URL" ] && break
      fi
    done
  fi
  if [ -z "${DASHBOARD_URL:-}" ]; then
    read -r -p "Enter Dashboard URL (e.g., http://192.168.1.114:8000): " DASHBOARD_URL
  fi
  DASHBOARD_URL="${DASHBOARD_URL%/}"

  _migrate_report() {
    local step="$1" message="${2:-}"
    if [ -n "${DASHBOARD_URL:-}" ]; then
      curl -m 3 -s -X POST "$DASHBOARD_URL/api/uninstall-progress" \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$TARGET_IP\",\"step\":\"$step\",\"message\":\"$message\"}" >/dev/null 2>&1 || true
    fi
  }

  _migrate_report "started" "Hermes → Eshu migration initiated"

  echo "Cleaning legacy Hermes components..."

  # Stop/disable legacy poller
  systemctl stop hermes-poller 2>/dev/null || true
  systemctl disable hermes-poller 2>/dev/null || true
  rm -f /etc/systemd/system/hermes-poller.service
  systemctl daemon-reload 2>/dev/null || true
  _migrate_report "stopping_poller" "Legacy Hermes poller stopped"
  sleep 1

  # Remove legacy binaries
  rm -f /usr/local/bin/hermes-diag.sh
  rm -f /usr/local/bin/hermes-gateway.sh
  rm -f /usr/local/bin/hermes-poller.sh
  _migrate_report "removing_binaries" "Legacy Hermes binaries removed"
  sleep 1

  # Remove legacy sudoers
  rm -f /etc/sudoers.d/hermes-diag
  _migrate_report "removing_sudoers" "Legacy Hermes sudoers removed"
  sleep 1

  # Remove legacy policy files
  rm -f /etc/hermes-exact.txt /etc/hermes-rwhite.txt /etc/hermes-rblack.txt
  _migrate_report "removing_policies" "Legacy Hermes policy files removed"
  sleep 1

  # Remove legacy runtime files
  rm -f /var/run/hermes.tickets /var/run/hermes.last_trigger
  _migrate_report "removing_runtime" "Legacy Hermes runtime files removed"
  sleep 1

  # Remove legacy user
  if id -u hermes-diag &>/dev/null; then
    userdel -r hermes-diag 2>/dev/null || true
    _migrate_report "removing_user" "Legacy Hermes user deleted"
  else
    _migrate_report "removing_user" "Legacy Hermes user not present (skipped)"
  fi
  sleep 1

  # Clean legacy SSH keys
  if [ -f /root/.ssh/authorized_keys ]; then
    grep -v "hermes-diag" /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp 2>/dev/null || true
    mv -f /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
  fi
  _migrate_report "cleaning_keys" "Legacy Hermes SSH keys cleaned"
  sleep 1

  echo "✅ Legacy Hermes cleaned. Proceeding with Eshu installation..."
  echo ""

  # Fall through to normal Eshu install in reinstall mode
  REINSTALL="yes"
fi

# ── Auto-detect and clean legacy Hermes (silent — no prompt) ──
if id -u hermes-diag &>/dev/null || [ -f /usr/local/bin/hermes-diag.sh ] || [ -f /etc/systemd/system/hermes-poller.service ]; then
  echo "🔄  Detected legacy Hermes installation — migrating to Eshu..."
  systemctl stop hermes-poller 2>/dev/null || true
  systemctl disable hermes-poller 2>/dev/null || true
  rm -f /etc/systemd/system/hermes-poller.service
  systemctl daemon-reload 2>/dev/null || true
  rm -f /usr/local/bin/hermes-diag.sh /usr/local/bin/hermes-gateway.sh /usr/local/bin/hermes-poller.sh
  rm -f /etc/sudoers.d/hermes-diag
  if id -u hermes-diag &>/dev/null; then
    userdel -r hermes-diag 2>/dev/null || true
  fi
  rm -f /etc/hermes-exact.txt /etc/hermes-rwhite.txt /etc/hermes-rblack.txt
  rm -f /var/run/hermes.tickets /var/run/hermes.last_trigger
  if [ -f /root/.ssh/authorized_keys ]; then
    grep -v "hermes-diag" /root/.ssh/authorized_keys > /tmp/eshu-ak-clean.tmp 2>/dev/null || true
    mv -f /tmp/eshu-ak-clean.tmp /root/.ssh/authorized_keys 2>/dev/null || true
  fi
  echo "✅  Legacy Hermes cleaned. Proceeding with Eshu install..."
fi

user_exists="no"; gateway_exists="no"; sudoers_exists="no"; authkeys_exists="no"
if id -u "$USER" &>/dev/null; then user_exists="yes"; fi
if [ -f "$GATEWAY" ]; then gateway_exists="yes"; fi
if [ -f "$SUDOERS" ]; then sudoers_exists="yes"; fi
if [ -f "/home/$USER/.ssh/authorized_keys" ]; then authkeys_exists="yes"; fi

installed="no"
if [ "$user_exists" = "yes" ] && [ "$gateway_exists" = "yes" ] && [ "$sudoers_exists" = "yes" ] && [ "$authkeys_exists" = "yes" ]; then
  installed="yes"
fi

echo "🚀 Eshu Gateway Installer"

if [ "$UPDATE" = "yes" ]; then
  mode="upgrade"
  if [ -z "$DASHBOARD_URL" ] && [ -f /etc/eshu/dashboard_url ]; then
    DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || true)
  fi
  if [ -z "$DASHBOARD_URL" ] && [ -f "$GATEWAY" ]; then
    DASHBOARD_URL=$(grep -oP 'DASHBOARD_URL="\K[^"]+' "$GATEWAY" 2>/dev/null || true)
  fi
elif [ "$installed" = "yes" ] && [ "$REINSTALL" = "no" ]; then
  read -r -p "Gateway found. Upgrade architecture in-place? [y/N]: " ans
  case "$ans" in [yY]*|"") mode="upgrade" ;; *) exit 0 ;; esac
else
  if [ "$REINSTALL" = "yes" ]; then mode="reinstall"; else mode="install"; fi
fi

if [ "$mode" != "upgrade" ] && [ "$user_exists" = "no" ]; then useradd -m -s /bin/bash "$USER"; fi

# Fresh install/reinstall: clear stale token self-heal guards so a gateway on a
# long-lived host (un-rebooted since a previous install) can self-heal a missing
# token instead of being permanently stuck (marker is once-per-boot in /var/run).
if [ "$mode" != "upgrade" ]; then
  rm -f /var/run/eshu.self_heal_done /var/run/eshu.self_heal_ts
fi

TARGET_IP=$(hostname -I | awk '{print $1}')
HOST_NAME=$(hostname)

# Only prompt for DASHBOARD_URL during install/reinstall, not update
if [ "$mode" != "upgrade" ]; then
  if [ -z "$DASHBOARD_URL" ]; then
    read -r -p "Enter JIT Web Dashboard URL (e.g., http://192.168.1.100:8000): " DASHBOARD_URL
  fi
  DASHBOARD_URL="${DASHBOARD_URL%/}"
fi

# Save dashboard URL to shared config file (for recovery during future updates)
if [ -n "$DASHBOARD_URL" ]; then
  mkdir -p /etc/eshu
  echo "$DASHBOARD_URL" > /etc/eshu/dashboard_url
  chmod 644 /etc/eshu/dashboard_url
fi

# Fetch gateway version from dashboard API BEFORE registering (single source of truth)
if [ -n "$DASHBOARD_URL" ]; then
  GATEWAY_VERSION=$(curl -m 3 -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','v0.1.0'))" 2>/dev/null || echo "v0.1.0")
else
  GATEWAY_VERSION="v0.1.0"
fi

# Register gateway with correct version
REGISTER_RESPONSE=""
if [ "$mode" != "upgrade" ]; then
  echo "Registering Gateway with Dashboard..."
  REGISTER_RESPONSE=$(curl -m 3 -s -X POST "$DASHBOARD_URL/api/register" \
       -H "Content-Type: application/json" \
       -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$GATEWAY_VERSION"'"}')
  GATEWAY_TOKEN=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); t=d.get('gateway_token'); print(t if t and t!='None' else '')" 2>/dev/null || echo "")
  if [ -n "$GATEWAY_TOKEN" ]; then
    echo "   Gateway API token received (v15+ auth)"
  fi
fi

# Always re-register on update to report new version (and get token if missing)
if [ "$mode" = "upgrade" ] && [ -n "$DASHBOARD_URL" ]; then
  REGISTER_RESPONSE=$(curl -m 3 -s -X POST "$DASHBOARD_URL/api/register" \
       -H "Content-Type: application/json" \
       -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$GATEWAY_VERSION"'"}')
  GATEWAY_TOKEN=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); t=d.get('gateway_token'); print(t if t and t!='None' else '')" 2>/dev/null || echo "")
  if [ -n "$GATEWAY_TOKEN" ]; then
    echo "   Gateway API token received (v15+ auth)"
  fi
fi

# Check for existing token in current gateway script (survives upgrades)
# Explicitly reject "None" — the literal string from the v15.0 DEFAULT None migration bug
if [ -z "${GATEWAY_TOKEN:-}" ] || [ "${GATEWAY_TOKEN:-}" = "None" ]; then
  if [ -f "$GATEWAY" ]; then
    EXISTING_TOKEN=$(grep -oP 'GATEWAY_TOKEN="\K[^"]+' "$GATEWAY" 2>/dev/null || echo "")
    if [ -n "$EXISTING_TOKEN" ] && [ "$EXISTING_TOKEN" != "None" ]; then
      GATEWAY_TOKEN="$EXISTING_TOKEN"
      echo "   Reusing existing gateway API token"
    fi
  fi
fi

write_gateway() {
  cp -f "$GATEWAY" "${GATEWAY}.bak" 2>/dev/null || true
  cat > "$GATEWAY" <<'GWEOF'
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
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"'"$status"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' >/dev/null 2>&1 &
}

log_window_reject() {
  local reason="$2"
  local enc_cmd
  enc_cmd=$(echo -n "$1" | base64 -w 0)
  curl -m 2 -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"window-rejected","reason":"'"$reason"'","token":"'"$ESHU_WINDOW_TOKEN"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' >/dev/null 2>&1 &
}

run_sanitized() { "$@" 2>&1 | sanitize; exit $?; }

logger -t eshu-gateway "from=${SSH_CONNECTION:-unknown} cmd=${SSH_ORIGINAL_COMMAND:-none}"
if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then exit 1; fi
cmd="$SSH_ORIGINAL_COMMAND"

# Parse ESHU_* metadata keys from the command prefix (env vars can't cross forced-command SSH).
# Iterative so keys coexist, e.g. "ESHU_WINDOW_TOKEN=abc ESHU_SESSION_ID=def <command>".
while :; do
  case "$cmd" in
    ESHU_WINDOW_TOKEN=*' '*) ESHU_WINDOW_TOKEN="${cmd%% *}"; ESHU_WINDOW_TOKEN="${ESHU_WINDOW_TOKEN#ESHU_WINDOW_TOKEN=}"; cmd="${cmd#* }" ;;
    ESHU_SESSION_ID=*' '*)   ESHU_SESSION_ID="${cmd%% *}";   ESHU_SESSION_ID="${ESHU_SESSION_ID#ESHU_SESSION_ID=}";         cmd="${cmd#* }" ;;
    ESHU_EXECUTION_ID=*' '*) ESHU_EXECUTION_ID="${cmd%% *}"; ESHU_EXECUTION_ID="${ESHU_EXECUTION_ID#ESHU_EXECUTION_ID=}";     cmd="${cmd#* }" ;;
    *) break ;;
  esac
done

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
     -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_req"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'"}' 2>&1 || echo "CURL_FAILED")

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
GWEOF

  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$GATEWAY"
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$GATEWAY"
  sed -i "s|__GATEWAY_VERSION__|$GATEWAY_VERSION|g" "$GATEWAY"
  # Scope the token replacement to the header assignment ONLY — a global replace
  # would also rewrite the self-heal placeholder check `[ "$GATEWAY_TOKEN" =
  # "__GATEWAY_TOKEN__" ]` into `[ = "<real-token>" ]` (always true), making every
  # gateway re-register every poll cycle.
  sed -i "s|^GATEWAY_TOKEN=\"__GATEWAY_TOKEN__\"|GATEWAY_TOKEN=\"${GATEWAY_TOKEN:-}\"|" "$GATEWAY"

  # Validate syntax before deploying — prevent broken templates from reaching gateways
  if ! bash -n "$GATEWAY" 2>/tmp/eshu-syntax-error.log; then
    echo "FATAL: Gateway script has syntax errors. Restoring previous script."
    echo "Syntax check output:"
    cat /tmp/eshu-syntax-error.log
    mv -f "${GATEWAY}.bak" "$GATEWAY"
    exit 1
  fi
  rm -f "${GATEWAY}.bak"
  chown root:root "$GATEWAY"; chmod 755 "$GATEWAY"
}

write_sudoers() {
  cat > "$SUDOERS" <<'EOF'
Defaults:eshu-gateway env_keep += "SSH_ORIGINAL_COMMAND"
eshu-gateway ALL=(root) NOPASSWD: /usr/local/bin/eshu-gateway.sh
EOF
  chmod 440 "$SUDOERS"; chown root:root "$SUDOERS"
  if ! visudo -cf "$SUDOERS"; then echo "Sudoers error."; exit 1; fi
}

write_poller() {
  POLLER_SCRIPT="/usr/local/bin/eshu-poller.sh"
  cat > "$POLLER_SCRIPT" <<'POLLEREOF'
#!/bin/bash
TRIGGER_FILE="/var/run/eshu.last_trigger"
DASHBOARD_URL="__DASHBOARD_URL__"
TARGET_IP="__TARGET_IP__"
HOST_NAME="__HOST_NAME__"
GATEWAY_TOKEN="__GATEWAY_TOKEN__"
POLL_INTERVAL=30

# Persist last processed trigger ID across restarts
LAST_TRIGGER_ID="0"
if [ -f "$TRIGGER_FILE" ]; then
  LAST_TRIGGER_ID=$(cat "$TRIGGER_FILE" 2>/dev/null || echo "0")
fi

# Clear stale tickets from previous sessions
if [ -f /var/run/eshu.tickets ]; then
  rm -f /var/run/eshu.tickets
fi

while true; do
  # Self-heal: if token is missing, register with dashboard to obtain one.
  # Cooldown-gated (60s) instead of once-per-boot: a gateway whose token went
  # missing (e.g. re-enrolled on an un-rebooted host) can always recover.
  if { [ -z "$GATEWAY_TOKEN" ] || [ "$GATEWAY_TOKEN" = "__GATEWAY_TOKEN__" ]; }; then
    NEXT_HEAL=$(cat /var/run/eshu.self_heal_ts 2>/dev/null || echo "0")
    if [ ! -f /var/run/eshu.self_heal_done ] || [ "${NEXT_HEAL:-0}" -lt "$(date +%s)" ]; then
      DASH_VER=$(curl -m 3 -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || echo "")
      REG_RESP=$(curl -m 3 -s -X POST "$DASHBOARD_URL/api/register" \
           -H "Content-Type: application/json" \
           -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"${DASH_VER:-unknown}"'"}' 2>/dev/null || echo "")
      NEW_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_token',''))" 2>/dev/null || echo "")
      if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "None" ]; then
        sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-poller.sh
        sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-gateway.sh
        GATEWAY_TOKEN="$NEW_TOKEN"
        touch /var/run/eshu.self_heal_done
        echo "$(( $(date +%s) + 60 ))" > /var/run/eshu.self_heal_ts
        logger -t eshu-poller "Self-healed: obtained new GATEWAY_TOKEN from dashboard"
      fi
    fi
  fi

  # Clean expired lockbox tickets (older than 90s)
  if [ -f /var/run/eshu.tickets ] && [ -s /var/run/eshu.tickets ]; then
    NOW=$(date +%s)
    awk -F'|' -v now="$NOW" '($1+90) >= now { print }' /var/run/eshu.tickets > /var/run/eshu.tickets.tmp
    mv -f /var/run/eshu.tickets.tmp /var/run/eshu.tickets
    chmod 600 /var/run/eshu.tickets
  fi

  # 1. Sync Central Policies
  HTTP_CODE=$(curl -m 2 -s -w "%{http_code}" -o /tmp/eshu-policy.json \
       -H "X-Gateway-Token: $GATEWAY_TOKEN" \
       "$DASHBOARD_URL/api/policy/$TARGET_IP")
  if [ "$HTTP_CODE" = "200" ] && [ -s /tmp/eshu-policy.json ]; then
    python3 -c "import sys, json; d=json.load(sys.stdin); open('/etc/eshu-exact.txt.tmp','w').write(d.get('exact_whitelist','')); open('/etc/eshu-rwhite.txt.tmp','w').write(d.get('regex_whitelist','')); open('/etc/eshu-rblack.txt.tmp','w').write(d.get('regex_blacklist',''))" < /tmp/eshu-policy.json 2>/dev/null || true
    mv -f /etc/eshu-exact.txt.tmp /etc/eshu-exact.txt 2>/dev/null || true
    mv -f /etc/eshu-rwhite.txt.tmp /etc/eshu-rwhite.txt 2>/dev/null || true
    mv -f /etc/eshu-rblack.txt.tmp /etc/eshu-rblack.txt 2>/dev/null || true

    # Sync emergency freeze flag — write '1' or empty atomically (tmp + mv)
    FREEZE_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_freeze', False))" < /tmp/eshu-policy.json 2>/dev/null || echo "False")
    if [ "$FREEZE_FLAG" = "True" ]; then
      echo "1" > /etc/eshu-freeze.tmp
    else
      : > /etc/eshu-freeze.tmp
    fi
    mv -f /etc/eshu-freeze.tmp /etc/eshu-freeze 2>/dev/null || true

    # Sync zero-trust flag — allowlisted commands must go through JIT on this gateway
    ZT_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('zero_trust', 0))" < /tmp/eshu-policy.json 2>/dev/null || echo "0")
    if [ "$ZT_FLAG" = "1" ]; then
      echo "1" > /etc/eshu-zero-trust.tmp
    else
      : > /etc/eshu-zero-trust.tmp
    fi
    mv -f /etc/eshu-zero-trust.tmp /etc/eshu-zero-trust 2>/dev/null || true

    # Fleet Run — dispatch approved fleet commands detached via systemd-run.
    # The runner posts running/success/failed/timeout back to the dashboard.
    FLEET_CMD_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd_id',''))" < /tmp/eshu-policy.json 2>/dev/null || echo "")
    FLEET_CMD=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd',''))" < /tmp/eshu-policy.json 2>/dev/null || echo "")
    FLEET_CMD_TIMEOUT=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd_timeout','180'))" < /tmp/eshu-policy.json 2>/dev/null || echo "180")
    LAST_FLEET_CMD_ID=$(cat /var/run/eshu.last_fleet_cmd 2>/dev/null || echo "0")
    if [ -n "$FLEET_CMD_ID" ] && [ "$FLEET_CMD_ID" != "0" ] && [ "$FLEET_CMD_ID" != "$LAST_FLEET_CMD_ID" ] && [ -n "$FLEET_CMD" ]; then
      logger -t eshu-poller "Fleet command #$FLEET_CMD_ID received. Downloading runner..."
      curl -s -o /tmp/eshu-fleet-runner.sh.tmp "$DASHBOARD_URL/static/fleet-runner.sh" 2>/dev/null || true
      if [ -s /tmp/eshu-fleet-runner.sh.tmp ] && bash -n /tmp/eshu-fleet-runner.sh.tmp 2>/dev/null; then
        chmod +x /tmp/eshu-fleet-runner.sh.tmp
        mv -f /tmp/eshu-fleet-runner.sh.tmp /tmp/eshu-fleet-runner.sh
        { echo "#!/bin/bash"; printf '%s\n' "$FLEET_CMD"; } > "/tmp/eshu-fleet-$FLEET_CMD_ID.sh" 2>/dev/null
        # Persist the ID only on a successful launch. The runner pings 'running'
        # immediately, which stops the dashboard re-injecting — so a crash before
        # this echo can never cause a double execution.
        if systemd-run --unit="eshu-fleet-$FLEET_CMD_ID" --collect \
            bash /tmp/eshu-fleet-runner.sh "$DASHBOARD_URL" "$GATEWAY_TOKEN" "$TARGET_IP" "$FLEET_CMD_ID" "$FLEET_CMD_TIMEOUT" "/tmp/eshu-fleet-$FLEET_CMD_ID.sh" >/dev/null 2>&1; then
          echo "$FLEET_CMD_ID" > /var/run/eshu.last_fleet_cmd
        else
          logger -t eshu-poller "Fleet command #$FLEET_CMD_ID launch failed — will retry"
          rm -f "/tmp/eshu-fleet-$FLEET_CMD_ID.sh"
        fi
      else
        logger -t eshu-poller "Fleet runner download/validation failed"
        rm -f /tmp/eshu-fleet-runner.sh.tmp
      fi
    fi

    # Sync approved windows cache (for dev-mode gateways)
    python3 -c "
import sys, json
d = json.load(sys.stdin)
wins = d.get('approved_windows', [])
with open('/etc/eshu-windows.txt.tmp', 'w') as f:
    for w in wins:
        f.write('{}|{}|{}|{}|{}|{}|{}|{}\n'.format(
            w.get('token',''), w.get('command',''),
            w.get('window_start','0'), w.get('window_end','0'),
            w.get('days_of_week','0'), w.get('execution_time','0'),
            w.get('expires_at','0') or '0', w.get('match_type','exact')))" \
      < /tmp/eshu-policy.json 2>/dev/null || true
    mv -f /etc/eshu-windows.txt.tmp /etc/eshu-windows.txt 2>/dev/null || true

    # Sync feature scripts — Approved Windows is core/always-on, so the handler is
    # always deployed (even when a host currently has no active windows); the
    # script hard-rejects any presented token that doesn't match a window.
    FEATURES_DIR="/etc/eshu/features.d"
    mkdir -p "$FEATURES_DIR"
    FEATURES_URL="$DASHBOARD_URL/static/features"
    curl -s -o "$FEATURES_DIR/approved_windows.sh.tmp" "$FEATURES_URL/approved_windows.sh" 2>/dev/null || true
    if [ -s "$FEATURES_DIR/approved_windows.sh.tmp" ] && bash -n "$FEATURES_DIR/approved_windows.sh.tmp" 2>/dev/null; then
      mv "$FEATURES_DIR/approved_windows.sh.tmp" "$FEATURES_DIR/approved_windows.sh"
    else
      logger -t eshu-poller "Feature script download/validation failed — keeping previous version"
      rm -f "$FEATURES_DIR/approved_windows.sh.tmp"
    fi

    # Check for uninstall trigger (must check before update, since uninstall is terminal)
    UNINSTALL_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_uninstall', False))" < /tmp/eshu-policy.json 2>/dev/null || echo "False")
    if [ "$UNINSTALL_FLAG" = "True" ]; then
      logger -t eshu-poller "Uninstall triggered. Downloading uninstaller..."
      curl -s -o /tmp/eshu-uninstall.sh "$DASHBOARD_URL/api/gateway-script" 2>/dev/null
      if [ -s /tmp/eshu-uninstall.sh ] && head -1 /tmp/eshu-uninstall.sh | grep -q '^#!/bin/bash'; then
        chmod +x /tmp/eshu-uninstall.sh
        # Launch as a transient systemd service in its own cgroup.
        # This survives the poller exiting — unlike nohup/disown which
        # get killed when systemd cleans up the poller's cgroup.
        systemd-run --unit=eshu-uninstall --collect \
          bash /tmp/eshu-uninstall.sh --uninstall --yes "$DASHBOARD_URL" >/dev/null 2>&1 || true
        # Clear trigger so the restarted poller doesn't re-spawn uninstalls
        curl -m 3 -s -X POST "$DASHBOARD_URL/api/uninstall-started/$TARGET_IP" \
             -H "X-Gateway-Token: $GATEWAY_TOKEN" >/dev/null 2>&1 || true
        rm -f /tmp/eshu-uninstall.sh
      fi
      exit 0
    fi

    # Check for triggered gateway update (uses unique trigger ID, not version)
    TRIGGER_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_update_version',''))" < /tmp/eshu-policy.json 2>/dev/null)
    DASH_VER=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('dashboard_version',''))" < /tmp/eshu-policy.json 2>/dev/null)
    if [ -n "$TRIGGER_ID" ] && [ "$TRIGGER_ID" != "0" ] && [ "$TRIGGER_ID" != "$LAST_TRIGGER_ID" ] && [ "$TRIGGER_ID" != "" ]; then
      logger -t eshu-poller "Update triggered (trigger $TRIGGER_ID, target version $DASH_VER). Downloading installer..."
      curl -s -o /tmp/eshu-update.sh "$DASHBOARD_URL/api/gateway-script" 2>/dev/null
      if [ -s /tmp/eshu-update.sh ] && head -1 /tmp/eshu-update.sh | grep -q '^#!/bin/bash'; then
        chmod +x /tmp/eshu-update.sh
        # Save trigger ID BEFORE running update (persists across poller restarts)
        echo "$TRIGGER_ID" > "$TRIGGER_FILE"
        LAST_TRIGGER_ID="$TRIGGER_ID"
        sudo bash /tmp/eshu-update.sh --update 2>&1 | logger -t eshu-poller
        # Register with the version from the updated installer
        NEW_VER=$(curl -m 3 -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','$DASH_VER'))" 2>/dev/null || echo "$DASH_VER")
        REG_RESP=$(curl -m 3 -s -X POST "$DASHBOARD_URL/api/register" \
             -H "Content-Type: application/json" \
             -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$NEW_VER"'"}' 2>/dev/null || echo "")
        NEW_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_token',''))" 2>/dev/null || echo "")
        if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "None" ]; then
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-poller.sh
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-gateway.sh
          GATEWAY_TOKEN="$NEW_TOKEN"
        fi
        logger -t eshu-poller "Update to $NEW_VER completed (trigger $TRIGGER_ID)."
        rm -f /tmp/eshu-update.sh
      fi
    fi

    # Check for rollback trigger (separate from update — downloads backup installer)
    ROLLBACK_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_rollback','0'))" < /tmp/eshu-policy.json 2>/dev/null || echo "0")
    LAST_ROLLBACK_ID=$(cat /var/run/eshu.last_rollback_trigger 2>/dev/null || echo "0")
    if [ -n "$ROLLBACK_ID" ] && [ "$ROLLBACK_ID" != "0" ] && [ "$ROLLBACK_ID" != "$LAST_ROLLBACK_ID" ]; then
      logger -t eshu-poller "Rollback triggered (rollback $ROLLBACK_ID). Downloading rollback installer..."
      curl -s -o /tmp/eshu-rollback.sh "$DASHBOARD_URL/api/gateway-script-rollback" 2>/dev/null
      if [ -s /tmp/eshu-rollback.sh ] && head -1 /tmp/eshu-rollback.sh | grep -q '^#!/bin/bash'; then
        chmod +x /tmp/eshu-rollback.sh
        echo "$ROLLBACK_ID" > /var/run/eshu.last_rollback_trigger
        # Also update the update trigger file so we don't re-update after rollback
        echo "$ROLLBACK_ID" > "$TRIGGER_FILE"
        LAST_TRIGGER_ID="$ROLLBACK_ID"
        sudo bash /tmp/eshu-rollback.sh --update 2>&1 | logger -t eshu-poller
        logger -t eshu-poller "Rollback completed (rollback $ROLLBACK_ID)."
        rm -f /tmp/eshu-rollback.sh
      fi
    fi

    # Check for dev update trigger (dev-mode gateways only)
    DEV_UPDATE_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_dev_update',''))" < /tmp/eshu-policy.json 2>/dev/null || echo "")
    DEV_INSTALLER_URL=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('dev_installer_url',''))" < /tmp/eshu-policy.json 2>/dev/null || echo "")
    LAST_DEV_UPDATE_ID=$(cat /var/run/eshu.last_dev_update_trigger 2>/dev/null || echo "0")
    if [ -n "$DEV_UPDATE_ID" ] && [ "$DEV_UPDATE_ID" != "" ] && [ "$DEV_UPDATE_ID" != "$LAST_DEV_UPDATE_ID" ] && [ -n "$DEV_INSTALLER_URL" ]; then
      logger -t eshu-poller "Dev update triggered (trigger $DEV_UPDATE_ID). Downloading dev installer from $DEV_INSTALLER_URL..."
      FULL_DEV_URL="$DASHBOARD_URL$DEV_INSTALLER_URL"
      curl -s -o /tmp/eshu-dev-update.sh "$FULL_DEV_URL" 2>/dev/null
      if [ -s /tmp/eshu-dev-update.sh ] && head -1 /tmp/eshu-dev-update.sh | grep -q '^#!/bin/bash'; then
        chmod +x /tmp/eshu-dev-update.sh
        echo "$DEV_UPDATE_ID" > /var/run/eshu.last_dev_update_trigger
        sudo bash /tmp/eshu-dev-update.sh --update 2>&1 | logger -t eshu-poller
        logger -t eshu-poller "Dev update to trigger $DEV_UPDATE_ID completed."
        rm -f /tmp/eshu-dev-update.sh
        # Re-register with updated version
        NEW_VER=$(curl -m 3 -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','$DASH_VER'))" 2>/dev/null || echo "$DASH_VER")
        REG_RESP=$(curl -m 3 -s -X POST "$DASHBOARD_URL/api/register" \
             -H "Content-Type: application/json" \
             -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$NEW_VER"'"}' 2>/dev/null || echo "")
        NEW_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_token',''))" 2>/dev/null || echo "")
        if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "None" ]; then
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-poller.sh
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-gateway.sh
          GATEWAY_TOKEN="$NEW_TOKEN"
        fi
      else
        logger -t eshu-poller "Dev update failed: could not download script from $FULL_DEV_URL"
        rm -f /tmp/eshu-dev-update.sh
      fi
    fi
  fi

  # 2. Poll for JIT Tickets
  WC=$(grep -c . /etc/eshu-windows.txt 2>/dev/null || echo 0)
  curl -m 2 -s -H "X-Gateway-Token: $GATEWAY_TOKEN" "$DASHBOARD_URL/api/poll/$TARGET_IP?wc=$WC" > /tmp/eshu-ticket.json
  if [ -s /tmp/eshu-ticket.json ]; then
    TICKET=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('ticket') or '')" < /tmp/eshu-ticket.json 2>/dev/null)
    if [ -n "$TICKET" ] && [ "$TICKET" != "null" ]; then
      echo "$TICKET" >> /var/run/eshu.tickets
      chmod 600 /var/run/eshu.tickets
    fi
  fi
  sleep "$POLL_INTERVAL"
done
POLLEREOF
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$POLLER_SCRIPT"
  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$POLLER_SCRIPT"
  sed -i "s|__HOST_NAME__|$HOST_NAME|g" "$POLLER_SCRIPT"
  # Scope the token replacement to the header assignment ONLY — a global replace
  # would also rewrite the self-heal placeholder check `[ "$GATEWAY_TOKEN" =
  # "__GATEWAY_TOKEN__" ]` into `[ = "<real-token>" ]` (always true), making every
  # gateway re-register every poll cycle.
  sed -i "s|^GATEWAY_TOKEN=\"__GATEWAY_TOKEN__\"|GATEWAY_TOKEN=\"${GATEWAY_TOKEN:-}\"|" "$POLLER_SCRIPT"
  chmod 700 "$POLLER_SCRIPT"

  cat > /etc/systemd/system/eshu-poller.service <<POLLERUNIT
[Unit]
Description=Eshu JIT Sync Poller
After=network.target

[Service]
ExecStart=$POLLER_SCRIPT
Restart=always
User=root

[Install]
WantedBy=multi-user.target
POLLERUNIT
  systemctl daemon-reload
  systemctl enable eshu-poller.service
  systemctl restart eshu-poller.service
}

write_logger() {
  cat > /usr/local/bin/eshu-logger.sh <<'LOGGEREOF'
#!/bin/bash
# eshu-logger — independent health heartbeat
# Reads DASHBOARD_URL from /etc/eshu/dashboard_url.
# Reports gateway health to the dashboard via POST /api/gateway-heartbeat.
# Intentionally simple: no sed replacements, no template variables.
# Survives all gateway/poller script updates.

set -eo pipefail

DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || echo "")
if [ -z "$DASHBOARD_URL" ]; then
    logger -t eshu-logger "No /etc/eshu/dashboard_url — exiting. Set DASHBOARD_URL and rerun."
    exit 1
fi

TARGET_IP=$(hostname -I | awk '{print $1}')
HOST_NAME=$(hostname)
INTERVAL=30

while true; do
    POLLER_OK=0; GATEWAY_OK=0; CAN_REACH=0

    systemctl is-active --quiet eshu-poller.service 2>/dev/null && POLLER_OK=1
    [ -f /usr/local/bin/eshu-gateway.sh ] && bash -n /usr/local/bin/eshu-gateway.sh >/dev/null 2>&1 && GATEWAY_OK=1
    curl -m 5 -s "$DASHBOARD_URL/api/version" >/dev/null 2>&1 && CAN_REACH=1

    logger -t eshu-logger "Heartbeat: poller=$POLLER_OK gw=$GATEWAY_OK reach=$CAN_REACH"

    curl -m 5 -s -X POST "$DASHBOARD_URL/api/gateway-heartbeat" \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$TARGET_IP\",\"hostname\":\"$HOST_NAME\",\"poller_ok\":$POLLER_OK,\"gateway_ok\":$GATEWAY_OK,\"can_reach\":$CAN_REACH}" \
        >/dev/null 2>&1 || true

    sleep "$INTERVAL"
done
LOGGEREOF
  chmod 755 /usr/local/bin/eshu-logger.sh

  cat > /etc/systemd/system/eshu-logger.service <<LOGGERUNIT
[Unit]
Description=Eshu Health Logger
After=network.target

[Service]
ExecStart=/usr/local/bin/eshu-logger.sh
Restart=always
RestartSec=30
User=root

[Install]
WantedBy=multi-user.target
LOGGERUNIT
  systemctl daemon-reload
  systemctl enable eshu-logger.service
  systemctl restart eshu-logger.service
}

write_gateway; write_logger; write_sudoers; write_poller

# SSH key setup only for install/reinstall (skip on update)
if [ "$mode" = "install" ] || [ "$mode" = "reinstall" ]; then
  echo ""
  if [ -z "$GATEWAY_PUB_KEY" ]; then read -r -p "Enter SSH pubkey for ESHU GATEWAY: " GATEWAY_PUB_KEY; fi

  if [ -z "$GATEWAY_PUB_KEY" ]; then
    echo "ERROR: The Eshu Gateway SSH key is required. Aborting."; exit 1
  fi

  mkdir -p "/home/$USER/.ssh"; chmod 700 "/home/$USER/.ssh"
  AK='command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,restrict'
  if [ -n "$ALLOWED_SSH_FROM" ]; then AK='from="'"$ALLOWED_SSH_FROM"'",'"$AK"; fi
  echo "$AK $GATEWAY_PUB_KEY" > "/home/$USER/.ssh/authorized_keys"
  chown -R "$USER":"$USER" "/home/$USER/.ssh"; chmod 600 "/home/$USER/.ssh/authorized_keys"
fi

echo ""
echo "✅ Gateway $GATEWAY_VERSION deployed."
echo "   - Hardcoded catastrophic blocklist active"
echo "   - JIT auto-poll enabled (waits up to 90s for approval)"
echo "   - Trigger-based updates enabled"
exit 0