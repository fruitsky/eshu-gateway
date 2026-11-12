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
GATEWAY_SSH_MODE="direct+mcp"

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
  # Extract the gateway API token so the uninstall progress reports authenticate.
  # The gateway script is removed later in this branch, so read it now.
  GATEWAY_TOKEN=$(grep -oP '^GATEWAY_TOKEN="\K[^"]+' /usr/local/bin/eshu-gateway.sh 2>/dev/null || true)
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
        -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
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
  # F12: the MAC key material must not outlive the gateway it belongs to.
  rm -f /etc/eshu-token
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

  # Report completion BEFORE deregistering. The `complete` step is what removes
  # the gateway row and clears the progress record server-side. Sending an
  # explicit DELETE first would invalidate our gateway token, so the following
  # "deregistering"/"complete" posts would 401 and the dashboard modal would
  # poll forever at the last step.
  if [ -n "$DASHBOARD_URL" ]; then
    echo "Deregistering from dashboard..."
    _report "deregistering" "Finalizing dashboard deregistration"
    sleep 1
  fi

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
    AK_CLEAN=$(mktemp)
    grep -v "hermes-diag" /root/.ssh/authorized_keys > "$AK_CLEAN" 2>/dev/null || true
    mv -f "$AK_CLEAN" /root/.ssh/authorized_keys 2>/dev/null || true
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

TARGET_IP=$(hostname -I | awk '{print $1}')
HOST_NAME=$(hostname)

# F12: the host key pin and the dashboard's service key.
# The pin is the gateway's own public host key line, sent with the
# token-authenticated registration. The dashboard key is fetched from
# /api/ssh-key (a public key) so the dashboard can open one SSH connection per
# MCP command without any agent holding a key.
HOST_KEY_LINE=""
for _hk in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub /etc/ssh/ssh_host_*_key.pub; do
  if [ -s "$_hk" ]; then HOST_KEY_LINE=$(head -n1 "$_hk"); break; fi
done
DASHBOARD_SSH_PUB=""
if [ -n "$DASHBOARD_URL" ]; then
  DASHBOARD_SSH_PUB=$(curl --connect-timeout 3 -m 15 -s "$DASHBOARD_URL/api/ssh-key" 2>/dev/null \
    | python3 -c "import sys, json; print(json.load(sys.stdin).get('public_key',''))" 2>/dev/null || echo "")
fi

# G4: the nonce store must survive a reboot; /run does not. Root-only 0700.
install -d -m 700 -o root -g root /var/lib/eshu 2>/dev/null || true

# G7: every entry in ALLOWED_SSH_FROM may use the dashboard key, so a list
# wider than the dashboard's own address is worth saying out loud.
if [ -n "$ALLOWED_SSH_FROM" ] && [ -n "$DASHBOARD_SSH_PUB" ]; then
  echo "   WARNING: ALLOWED_SSH_FROM='$ALLOWED_SSH_FROM' — every host in that"
  echo "            list may use the dashboard key, not only the dashboard."
fi

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
  GATEWAY_VERSION=$(curl --connect-timeout 3 -m 15 -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','v0.1.0'))" 2>/dev/null || echo "v0.1.0")
else
  GATEWAY_VERSION="v0.1.0"
fi

# Register gateway with correct version
# S1: a gateway with no /etc/eshu-token cannot sign an agent prefix (a
# self-update that never re-installed leaves the file absent), so it must not
# advertise the agent format. The dashboard then refuses with a clear
# `ssh_not_ready` instead of a `bad signature` at call time. A fresh
# install/reinstall writes the token below, so it reports both.
# F19: report whether this host can enforce a run limit (GNU timeout signals
# the process group). Display only: the dashboard decides per request.
RUN_LIMITS_CAP=""
TIMEOUT_VERSION=$(timeout --version 2>/dev/null || true)
if printf '%s' "$TIMEOUT_VERSION" | grep -q 'GNU coreutils'; then RUN_LIMITS_CAP="v1"; fi
SSH_REG_CAPS='"ssh_host_key":"'"$HOST_KEY_LINE"'","ssh_ticket_format":"v2","run_limits":"'"$RUN_LIMITS_CAP"'"'
if [ "$mode" != "upgrade" ] || [ -f /etc/eshu-token ]; then
  SSH_REG_CAPS='"ssh_host_key":"'"$HOST_KEY_LINE"'","ssh_agent_format":"eshu-ssh-agent-v1","ssh_ticket_format":"v2","run_limits":"'"$RUN_LIMITS_CAP"'"'
fi
# RR1: the existing token is loaded BEFORE registering. A gateway the
# dashboard already knows must present its own X-Gateway-Token to
# re-register; the old order sent an empty one, got 401 on every update and
# reinstall, and so never reported its host-key pin or run_limits (ssh_exec
# then refused it as ssh_host_unpinned). Same source as before (the installed
# gateway script), then /etc/eshu-token. "None" is the v15.0 migration bug and
# "__GATEWAY_TOKEN__" an unsubstituted template; neither is a token.
_valid_token() {
  case "${1:-}" in ""|None|__GATEWAY_TOKEN__) return 1 ;; esac
  printf '%s' "$1" | grep -qE '^[A-Za-z0-9_-]{16,128}$'
}
if ! _valid_token "${GATEWAY_TOKEN:-}"; then
  GATEWAY_TOKEN=""
  if [ -f "$GATEWAY" ]; then
    EXISTING_TOKEN=$(grep -oP '^GATEWAY_TOKEN="\K[^"]+' "$GATEWAY" 2>/dev/null | head -n1 || true)
    if _valid_token "$EXISTING_TOKEN"; then GATEWAY_TOKEN="$EXISTING_TOKEN"; fi
  fi
  if [ -z "$GATEWAY_TOKEN" ] && [ -f /etc/eshu-token ]; then
    EXISTING_TOKEN=$(head -n1 /etc/eshu-token 2>/dev/null | tr -d '[:space:]' || true)
    if _valid_token "$EXISTING_TOKEN"; then GATEWAY_TOKEN="$EXISTING_TOKEN"; fi
  fi
  EXISTING_TOKEN=""
  if [ -n "$GATEWAY_TOKEN" ]; then echo "   Reusing existing gateway API token"; fi
fi

# RR1: one registration call for install, reinstall and update. Sets
# REGISTER_STATUS (the HTTP code, 000 when unreachable) and REGISTER_RESPONSE.
register_gateway() {   # $1 = the X-Gateway-Token to present ('' for none)
  local out
  out=$(curl --connect-timeout 3 -m 15 -s -w '\n%{http_code}' -X POST "$DASHBOARD_URL/api/register" \
       -H "Content-Type: application/json" \
       -H "X-Gateway-Token: ${1:-}" \
       -H "X-Enrollment-Token: ${ESHU_ENROLL_TOKEN:-}" \
       -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$GATEWAY_VERSION"'",'"$SSH_REG_CAPS"'}' 2>/dev/null) \
    || out=""
  case "$out" in
    *$'\n'[0-9][0-9][0-9]) REGISTER_STATUS="${out##*$'\n'}"; REGISTER_RESPONSE="${out%$'\n'*}" ;;
    *) REGISTER_STATUS="000"; REGISTER_RESPONSE="" ;;
  esac
}

REGISTER_RESPONSE=""
REGISTER_STATUS=""
REGISTER_OK=""
if [ -n "$DASHBOARD_URL" ]; then
  if [ "$mode" != "upgrade" ]; then echo "Registering Gateway with Dashboard..."; fi
  register_gateway "${GATEWAY_TOKEN:-}"
  # A token left behind by an earlier install whose gateway was since removed
  # from the dashboard is no longer valid. With an enrollment token, register
  # once more as a new gateway; the dashboard decides, the installer never
  # guesses.
  if [ "$REGISTER_STATUS" = "401" ] && [ -n "${GATEWAY_TOKEN:-}" ] && [ -n "${ESHU_ENROLL_TOKEN:-}" ]; then
    register_gateway ""
  fi
  if [ "$REGISTER_STATUS" = "200" ]; then
    REGISTER_OK="1"
    NEW_TOKEN=$(printf '%s' "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); t=d.get('gateway_token'); print(t if t and t!='None' else '')" 2>/dev/null || echo "")
    if _valid_token "$NEW_TOKEN"; then
      GATEWAY_TOKEN="$NEW_TOKEN"
      echo "   Gateway API token received (v15+ auth)"
    fi
    NEW_TOKEN=""
    GATEWAY_SSH_MODE=$(printf '%s' "$REGISTER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ssh_mode','direct+mcp'))" 2>/dev/null || echo "direct+mcp")
  else
    # Keep the token we have: a failed call must never blank it.
    echo "   WARNING: the dashboard refused or did not answer the registration (HTTP $REGISTER_STATUS)."
    echo "            Its host-key pin and capabilities were not updated; ssh_exec"
    echo "            may refuse this gateway until a registration succeeds."
  fi
  case "$GATEWAY_SSH_MODE" in mcp_only) ;; *) GATEWAY_SSH_MODE="direct+mcp" ;; esac
fi

write_gateway() {
  # R1: the script carries GATEWAY_TOKEN, which is also the MAC key material.
  # Create it (and its temporary backup) root-only from the first byte, and
  # finish 0700 root:root in every mode — install, reinstall and update.
  (
    umask 077
    cp -f "$GATEWAY" "${GATEWAY}.bak" 2>/dev/null || true
    cat > "$GATEWAY" <<'GWEOF'
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
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/log" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "Content-Type: application/json" \
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"'"$status"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'"}' >/dev/null 2>&1 &
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
       -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","status":"window-rejected","reason":"'"$reason"'","token":"'"$ESHU_WINDOW_TOKEN"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'"}' >/dev/null 2>&1 &
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
  # D2: the body (up to ~22 KB) goes on stdin, never into argv.
  { printf '%s' "$body" | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
         -X POST "$DASHBOARD_URL/api/exec-result" \
         -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
         -H "Content-Type: application/json" \
         --data-binary @- ; } >/dev/null 2>&1 &
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
  CLAIM_BODY=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
    -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/ticket/$1" 2>/dev/null || true)
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
  flock -w 5 9 || return 1
  return 0
}

tickets_unlock() {
  flock -u 9 2>/dev/null || true
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

remove_claimed_ticket() {
  local tmp removed="no" line
  [ -f "$TICKETS_FILE" ] || return 0
  tmp=$(mktemp -p "$ESHU_RUN_DIR" eshu-tickets.XXXXXX) || return 0
  if ! tickets_lock; then
    rm -f "$tmp"
    return 0
  fi
  while IFS= read -r line || [ -n "${line:-}" ]; do
    [ -z "${line:-}" ] && continue
    case "$line" in *$'\r'*) continue ;; esac
    parse_ticket_line "$line"
    if [ "$removed" = "no" ] && [ "$T_CMD" = "$cmd" ] && ticket_allowed "$T_AGENT" "$T_SESSION"; then
      removed="yes"
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$TICKETS_FILE"
  mv -f "$tmp" "$TICKETS_FILE"
  chmod 600 "$TICKETS_FILE"
  tickets_unlock
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
RESPONSE=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -w "\n%{http_code}" -X POST "$DASHBOARD_URL/api/request" \
     -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
     -H "Content-Type: application/json" \
     -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_req"'","session_id":"'"${ESHU_SESSION_ID:-}"'","execution_id":"'"${ESHU_EXECUTION_ID:-}"'","agent":"'"${ESHU_VERIFIED_AGENT:-}"'","force_jit":'"$([ "$ESHU_FORCE_JIT" = "1" ] && echo true || echo false)"',"run_limits":"'"$RUN_LIMITS_CAP"'","check_token":"'"${ESHU_CHECK_TOKEN_VERIFIED:-}"'"}' 2>&1 || echo "CURL_FAILED")

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
  STATUS_RESP=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/request_status/$REQUEST_ID" 2>/dev/null || echo "")
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
    CLAIM_BODY=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
      -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" "$DASHBOARD_URL/api/ticket/$REQUEST_ID" 2>/dev/null || true)
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
  # Remove consumed ticket from lockbox (awk-based, no nested loop)
  remove_claimed_ticket
  RESULT_PATH="jit"
  RESULT_REQUEST_ID="$REQUEST_ID_INT"
  run_sanitized bash -c "$cmd"
else
  exit 1
fi
GWEOF
  )

  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$GATEWAY"
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$GATEWAY"
  sed -i "s|__GATEWAY_VERSION__|$GATEWAY_VERSION|g" "$GATEWAY"
  # Scope the token replacement to the header assignment ONLY — an unanchored or
  # global replace could corrupt other lines that reference GATEWAY_TOKEN.
  sed -i "s|^GATEWAY_TOKEN=\"__GATEWAY_TOKEN__\"|GATEWAY_TOKEN=\"${GATEWAY_TOKEN:-}\"|" "$GATEWAY"

  # Validate syntax before deploying — prevent broken templates from reaching
  # gateways. mktemp (not a fixed /tmp name) so another local user cannot
  # pre-create or redirect the log.
  SYNTAX_LOG=$(mktemp)
  if ! bash -n "$GATEWAY" 2>"$SYNTAX_LOG"; then
    echo "FATAL: Gateway script has syntax errors. Restoring previous script."
    echo "Syntax check output:"
    cat "$SYNTAX_LOG"
    rm -f "$SYNTAX_LOG"
    mv -f "${GATEWAY}.bak" "$GATEWAY"
    exit 1
  fi
  rm -f "$SYNTAX_LOG"
  rm -f "${GATEWAY}.bak"
  chown root:root "$GATEWAY"; chmod 700 "$GATEWAY"
}

write_sudoers() {
  cat > "$SUDOERS" <<'EOF'
Defaults:eshu-gateway env_keep += "SSH_ORIGINAL_COMMAND"
eshu-gateway ALL=(root) NOPASSWD: /usr/local/bin/eshu-gateway.sh "", /usr/local/bin/eshu-gateway.sh --dashboard-key
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

# Runtime config. The installer bakes a URL, but /etc/eshu/dashboard_url wins so
# an operator can move the dashboard (e.g. LAN HTTP for a slow/ARM gateway)
# without reinstalling. Timeouts are generous enough for TLS on ARMv6; the
# previous 2s values made every policy call fail on a Pi 1.
if [ -s /etc/eshu/dashboard_url ]; then
  DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || echo "$DASHBOARD_URL")
fi
HTTP_TIMEOUT="${ESHU_HTTP_TIMEOUT:-10}"
HTTP_CONNECT_TIMEOUT="${ESHU_HTTP_CONNECT_TIMEOUT:-3}"

# Root must never parse or execute a fixed path in a world-writable directory.
# Every scratch file and download lives in this private root-owned 0700 dir.
# Refuse to start if it is a symlink, the wrong owner, or too permissive
# (systemd restarts the service, so the operator sees it in the journal).
ESHU_WORK="/run/eshu"
if [ -L "$ESHU_WORK" ]; then
  logger -t eshu-poller "ESHU_WORK $ESHU_WORK is a symlink — refusing to start"
  exit 1
fi
if [ ! -d "$ESHU_WORK" ]; then
  install -d -m 0700 -o root -g root "$ESHU_WORK" 2>/dev/null || {
    logger -t eshu-poller "Cannot create ESHU_WORK $ESHU_WORK — refusing to start"
    exit 1
  }
fi
WORK_UID=$(stat -c '%u' "$ESHU_WORK" 2>/dev/null || echo "unknown")
WORK_MODE=$(stat -c '%a' "$ESHU_WORK" 2>/dev/null || echo "unknown")
if [ "$WORK_UID" != "0" ] || [ "$WORK_MODE" != "700" ]; then
  logger -t eshu-poller "ESHU_WORK $ESHU_WORK must be root-owned mode 0700 (uid=$WORK_UID mode=$WORK_MODE) — refusing to start"
  exit 1
fi
umask 077

# BI2: the lockbox file is shared with the gateway. Every read-modify-write
# and the append hold flock on one file in this root-only 0700 directory, so
# a cleanup cannot move back a copy taken before a claim, and two writers
# cannot interleave. A host without flock skips lockbox writes (fail closed,
# no double runs) instead of writing unlocked.
TICKETS_LOCK="$ESHU_WORK/eshu.tickets.lock"
tickets_lock() {
  command -v flock >/dev/null 2>&1 || {
    logger -t eshu-poller "flock missing — refusing lockbox writes"
    return 1
  }
  exec 9>>"$TICKETS_LOCK" || return 1
  flock -w 5 9 || return 1
  return 0
}
tickets_unlock() {
  flock -u 9 2>/dev/null || true
}

# Clean expired lockbox tickets (older than 90s). BI1: a CR anywhere, a
# non-numeric stamp and a stamp more than a few seconds in the future are not
# tickets. BI2: the file is read while the lock is held, not before it — a
# copy taken outside the lock could resurrect a ticket the gateway claimed.
clean_tickets() {
  [ -f /var/run/eshu.tickets ] && [ -s /var/run/eshu.tickets ] || return 0
  local now
  now=$(date +%s)
  tickets_lock || return 0
  # F12 G5: a v2 line is `v2|ts|agent|session|cmd`; the timestamp is $2.
  # Reading $1 as the timestamp would delete every v2 ticket as stale.
  awk -F'|' -v now="$now" '
    index($0, "\r") { next }
    $1 == "v2" { if ($2 ~ /^[0-9]+$/ && $2 <= now + 5 && ($2 + 90) >= now) print; next }
    { if ($1 ~ /^[0-9]+$/ && $1 <= now + 5 && ($1 + 90) >= now) print }
  ' /var/run/eshu.tickets > "$ESHU_WORK/eshu.tickets.clean.$$"
  mv -f "$ESHU_WORK/eshu.tickets.clean.$$" /var/run/eshu.tickets
  chmod 600 /var/run/eshu.tickets
  tickets_unlock
}

# Policy sync stamp. When the dashboard returns the exact same policy body and
# every synced file is still present, skip the parse/write block entirely. That
# replaces ~16 short-lived python3 starts per cycle (roughly a third of a Pi 1
# core) with one sha256sum. Bump POLICY_STAMP_VERSION whenever the set of files
# written below changes, so upgraded pollers re-sync once instead of skipping.
POLICY_STAMP="/var/run/eshu.policy.sha256"
POLICY_STAMP_VERSION="1"

# Files the sync block always writes on a healthy cycle. The session key is
# intentionally excluded: it is only written when the dashboard supplies one,
# and any transition that needs it (strict sessions on, key rotation) changes
# the policy body and therefore forces a re-sync.
policy_files_ok() {
  [ -f /etc/eshu-exact.txt ] &&
  [ -f /etc/eshu-rwhite.txt ] &&
  [ -f /etc/eshu-rblack.txt ] &&
  [ -f /etc/eshu-freeze ] &&
  [ -f /etc/eshu-zero-trust ] &&
  [ -f /etc/eshu-require-session ] &&
  [ -f /etc/eshu-windows.txt ]
}

policy_stamp_matches() {
  local stamp
  stamp=$(cat "$POLICY_STAMP" 2>/dev/null || true)
  [ -n "$stamp" ] && [ "$stamp" = "$POLICY_STAMP_VERSION:$1" ]
}

# Feature scripts are refreshed on every healthy cycle, independent of the
# policy hash: the dashboard's static files can change without a policy edit
# (DASHBOARD_VERSION is a static string), so a stale window handler must not
# linger. The download is bounded and syntax-checked before replacing the live
# file. Runs only when the dashboard answered, matching the previous behavior.
sync_feature_scripts() {
  local features_dir="/etc/eshu/features.d"
  mkdir -p "$features_dir"
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -o "$features_dir/approved_windows.sh.tmp" \
       "$DASHBOARD_URL/static/features/approved_windows.sh" 2>/dev/null || true
  if [ -s "$features_dir/approved_windows.sh.tmp" ] && bash -n "$features_dir/approved_windows.sh.tmp" 2>/dev/null; then
    if cmp -s "$features_dir/approved_windows.sh.tmp" "$features_dir/approved_windows.sh"; then
      rm -f "$features_dir/approved_windows.sh.tmp"
    else
      mv -f "$features_dir/approved_windows.sh.tmp" "$features_dir/approved_windows.sh"
    fi
  else
    logger -t eshu-poller "Feature script download/validation failed — keeping previous version"
    rm -f "$features_dir/approved_windows.sh.tmp"
  fi
}

# Persist last processed trigger ID across restarts
LAST_TRIGGER_ID="0"
if [ -f "$TRIGGER_FILE" ]; then
  LAST_TRIGGER_ID=$(cat "$TRIGGER_FILE" 2>/dev/null || echo "0")
fi

# Clear stale tickets from previous sessions (under the lock: a gateway may be
# claiming from the same file right now)
if [ -f /var/run/eshu.tickets ]; then
  if tickets_lock; then
    rm -f /var/run/eshu.tickets
    tickets_unlock
  fi
fi

while true; do
  POLICY_FILE=$(mktemp -p "$ESHU_WORK" policy.XXXXXX)
  clean_tickets

  # 1. Sync Central Policies
  HTTP_CODE=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -w "%{http_code}" -o "$POLICY_FILE" \
       -H "X-Gateway-Token: $GATEWAY_TOKEN" \
       "$DASHBOARD_URL/api/policy/$TARGET_IP")
  POLICY_HASH=""
  POLICY_STALE="1"
  if [ "$HTTP_CODE" = "200" ] && [ -s "$POLICY_FILE" ]; then
    POLICY_HASH=$(sha256sum "$POLICY_FILE" 2>/dev/null | awk '{print $1}')
    if [ -n "$POLICY_HASH" ] && policy_stamp_matches "$POLICY_HASH" && policy_files_ok; then
      POLICY_STALE="0"
    fi
    # Independent of the policy hash: keep the window handler current.
    sync_feature_scripts
  fi
  if [ "$POLICY_STALE" = "1" ] && [ "$HTTP_CODE" = "200" ] && [ -s "$POLICY_FILE" ]; then
    POLICY_JSON_OK="0"
    if python3 -c "import sys, json; d=json.load(sys.stdin); open('/etc/eshu-exact.txt.tmp','w').write(d.get('exact_whitelist','')); open('/etc/eshu-rwhite.txt.tmp','w').write(d.get('regex_whitelist','')); open('/etc/eshu-rblack.txt.tmp','w').write(d.get('regex_blacklist',''))" < "$POLICY_FILE" 2>/dev/null; then
      POLICY_JSON_OK="1"
    fi
    mv -f /etc/eshu-exact.txt.tmp /etc/eshu-exact.txt 2>/dev/null || true
    mv -f /etc/eshu-rwhite.txt.tmp /etc/eshu-rwhite.txt 2>/dev/null || true
    mv -f /etc/eshu-rblack.txt.tmp /etc/eshu-rblack.txt 2>/dev/null || true

    # Sync emergency freeze flag — write '1' or empty atomically (tmp + mv)
    FREEZE_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_freeze', False))" < "$POLICY_FILE" 2>/dev/null || echo "False")
    if [ "$FREEZE_FLAG" = "True" ]; then
      echo "1" > /etc/eshu-freeze.tmp
    else
      : > /etc/eshu-freeze.tmp
    fi
    mv -f /etc/eshu-freeze.tmp /etc/eshu-freeze 2>/dev/null || true

    # Sync zero-trust flag — allowlisted commands must go through JIT on this gateway
    ZT_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('zero_trust', 0))" < "$POLICY_FILE" 2>/dev/null || echo "0")
    if [ "$ZT_FLAG" = "1" ]; then
      echo "1" > /etc/eshu-zero-trust.tmp
    else
      : > /etc/eshu-zero-trust.tmp
    fi
    mv -f /etc/eshu-zero-trust.tmp /etc/eshu-zero-trust 2>/dev/null || true

    # F5: per-gateway output capture mode (off | exit-only | tail). Absent or
    # garbage means exit-only — capture must never switch itself on.
    CAPTURE_MODE=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('capture_output','exit-only'))" < "$POLICY_FILE" 2>/dev/null || echo "exit-only")
    case "$CAPTURE_MODE" in
      off|exit-only|tail) printf '%s' "$CAPTURE_MODE" > /etc/eshu-capture.tmp ;;
      *) printf 'exit-only' > /etc/eshu-capture.tmp ;;
    esac
    mv -f /etc/eshu-capture.tmp /etc/eshu-capture 2>/dev/null || true

    # Sync the session signing key + enforcement flag. The gateway verifies the
    # HMAC locally, so it needs the key before the flag is switched on.
    SESSION_KEY=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('session_signing_key',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    SESSION_KEY_PREV=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('session_signing_key_prev',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    if [ -n "$SESSION_KEY" ]; then
      ( umask 077; printf '%s' "$SESSION_KEY" > /etc/eshu-session-key.tmp )
      if chown root:eshu-gateway /etc/eshu-session-key.tmp 2>/dev/null; then
        chmod 640 /etc/eshu-session-key.tmp
      else
        chmod 644 /etc/eshu-session-key.tmp
      fi
      mv -f /etc/eshu-session-key.tmp /etc/eshu-session-key
      if [ -n "$SESSION_KEY_PREV" ]; then
        ( umask 077; printf '%s' "$SESSION_KEY_PREV" > /etc/eshu-session-key.prev.tmp )
        if chown root:eshu-gateway /etc/eshu-session-key.prev.tmp 2>/dev/null; then
          chmod 640 /etc/eshu-session-key.prev.tmp
        else
          chmod 644 /etc/eshu-session-key.prev.tmp
        fi
        mv -f /etc/eshu-session-key.prev.tmp /etc/eshu-session-key.prev
      else
        : > /etc/eshu-session-key.prev
        chmod 640 /etc/eshu-session-key.prev 2>/dev/null || true
      fi
    fi
    # F12: direct+mcp | mcp_only. Only mcp_only is written; anything else is
    # the default, so a garbage value can never retire the shared key.
    SSH_MODE=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('ssh_mode','direct+mcp'))" < "$POLICY_FILE" 2>/dev/null || echo "direct+mcp")
    case "$SSH_MODE" in
      mcp_only) printf '%s' "mcp_only" > /etc/eshu-ssh-mode.tmp ;;
      *) printf '%s' "direct+mcp" > /etc/eshu-ssh-mode.tmp ;;
    esac
    mv -f /etc/eshu-ssh-mode.tmp /etc/eshu-ssh-mode 2>/dev/null || true

    SESSION_REQUIRED=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('require_sessions',0))" < "$POLICY_FILE" 2>/dev/null || echo "0")
    if [ "$SESSION_REQUIRED" = "1" ]; then
      echo "1" > /etc/eshu-require-session.tmp
    else
      : > /etc/eshu-require-session.tmp
    fi
    mv -f /etc/eshu-require-session.tmp /etc/eshu-require-session 2>/dev/null || true

    # Fleet Run — dispatch approved fleet commands detached via systemd-run.
    # The runner posts running/success/failed/timeout back to the dashboard.
    FLEET_CMD_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd_id',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    FLEET_CMD=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    FLEET_CMD_TIMEOUT=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('pending_fleet_cmd_timeout','180'))" < "$POLICY_FILE" 2>/dev/null || echo "180")
    case "$FLEET_CMD_ID" in
      ''|*[!0-9]*) FLEET_CMD_ID="" ;;
    esac
    LAST_FLEET_CMD_ID=$(cat /var/run/eshu.last_fleet_cmd 2>/dev/null || echo "0")
    if [ -n "$FLEET_CMD_ID" ] && [ "$FLEET_CMD_ID" != "0" ] && [ "$FLEET_CMD_ID" != "$LAST_FLEET_CMD_ID" ] && [ -n "$FLEET_CMD" ]; then
      logger -t eshu-poller "Fleet command #$FLEET_CMD_ID received. Downloading runner..."
      FLEET_RUNNER_TMP=$(mktemp -p "$ESHU_WORK" fleet-runner.XXXXXX)
      FLEET_RUNNER="$ESHU_WORK/eshu-fleet-runner.sh"
      FLEET_CMD_FILE="$ESHU_WORK/eshu-fleet-$FLEET_CMD_ID.sh"
      FLEET_OUT_FILE="$ESHU_WORK/eshu-fleet-out-$FLEET_CMD_ID.txt"
      curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m 60 -s -o "$FLEET_RUNNER_TMP" "$DASHBOARD_URL/static/fleet-runner.sh" 2>/dev/null || true
      if [ -s "$FLEET_RUNNER_TMP" ] && bash -n "$FLEET_RUNNER_TMP" 2>/dev/null; then
        chmod +x "$FLEET_RUNNER_TMP"
        mv -f "$FLEET_RUNNER_TMP" "$FLEET_RUNNER"
        { echo "#!/bin/bash"; printf '%s\n' "$FLEET_CMD"; } > "$FLEET_CMD_FILE" 2>/dev/null
        # Persist the ID only on a successful launch. The runner pings 'running'
        # immediately, which stops the dashboard re-injecting — so a crash before
        # this echo can never cause a double execution.
        if systemd-run --unit="eshu-fleet-$FLEET_CMD_ID" --collect \
            bash "$FLEET_RUNNER" "$DASHBOARD_URL" "$GATEWAY_TOKEN" "$TARGET_IP" "$FLEET_CMD_ID" "$FLEET_CMD_TIMEOUT" "$FLEET_CMD_FILE" "$FLEET_OUT_FILE" >/dev/null 2>&1; then
          echo "$FLEET_CMD_ID" > /var/run/eshu.last_fleet_cmd
        else
          logger -t eshu-poller "Fleet command #$FLEET_CMD_ID launch failed — will retry"
          rm -f "$FLEET_CMD_FILE"
        fi
      else
        logger -t eshu-poller "Fleet runner download/validation failed"
        rm -f "$FLEET_RUNNER_TMP"
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
      < "$POLICY_FILE" 2>/dev/null || true
    mv -f /etc/eshu-windows.txt.tmp /etc/eshu-windows.txt 2>/dev/null || true

    # Check for uninstall trigger (must check before update, since uninstall is terminal)
    UNINSTALL_FLAG=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_uninstall', False))" < "$POLICY_FILE" 2>/dev/null || echo "False")
    if [ "$UNINSTALL_FLAG" = "True" ]; then
      logger -t eshu-poller "Uninstall triggered. Downloading uninstaller..."
      UNINSTALL_FILE=$(mktemp -p "$ESHU_WORK" uninstall.XXXXXX)
      curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m 60 -s -o "$UNINSTALL_FILE" "$DASHBOARD_URL/api/gateway-script" 2>/dev/null
      if [ -s "$UNINSTALL_FILE" ] && head -1 "$UNINSTALL_FILE" | grep -q '^#!/bin/bash'; then
        chmod +x "$UNINSTALL_FILE"
        # Launch as a transient systemd service in its own cgroup.
        # This survives the poller exiting — unlike nohup/disown which
        # get killed when systemd cleans up the poller's cgroup.
        systemd-run --unit=eshu-uninstall --collect \
          bash "$UNINSTALL_FILE" --uninstall --yes "$DASHBOARD_URL" >/dev/null 2>&1 || true
        # Clear trigger so the restarted poller doesn't re-spawn uninstalls
        curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/uninstall-started/$TARGET_IP" \
             -H "X-Gateway-Token: $GATEWAY_TOKEN" >/dev/null 2>&1 || true
        rm -f "$UNINSTALL_FILE"
      fi
      exit 0
    fi

    # Check for triggered gateway update (uses unique trigger ID, not version)
    TRIGGER_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_update_version',''))" < "$POLICY_FILE" 2>/dev/null)
    DASH_VER=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('dashboard_version',''))" < "$POLICY_FILE" 2>/dev/null)
    if [ -n "$TRIGGER_ID" ] && [ "$TRIGGER_ID" != "0" ] && [ "$TRIGGER_ID" != "$LAST_TRIGGER_ID" ] && [ "$TRIGGER_ID" != "" ]; then
      logger -t eshu-poller "Update triggered (trigger $TRIGGER_ID, target version $DASH_VER). Downloading installer..."
      UPDATE_FILE=$(mktemp -p "$ESHU_WORK" update.XXXXXX)
      curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m 60 -s -o "$UPDATE_FILE" "$DASHBOARD_URL/api/gateway-script" 2>/dev/null
      if [ -s "$UPDATE_FILE" ] && head -1 "$UPDATE_FILE" | grep -q '^#!/bin/bash'; then
        chmod +x "$UPDATE_FILE"
        # Save trigger ID BEFORE running update (persists across poller restarts)
        echo "$TRIGGER_ID" > "$TRIGGER_FILE"
        LAST_TRIGGER_ID="$TRIGGER_ID"
        sudo bash "$UPDATE_FILE" --update 2>&1 | logger -t eshu-poller
        # Register with the version from the updated installer
        NEW_VER=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','$DASH_VER'))" 2>/dev/null || echo "$DASH_VER")
        REG_RESP=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/register" \
             -H "Content-Type: application/json" \
             -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
             -H "X-Enrollment-Token: ${ESHU_ENROLL_TOKEN:-}" \
             -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$NEW_VER"'"}' 2>/dev/null || echo "")
        NEW_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_token',''))" 2>/dev/null || echo "")
        if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "None" ]; then
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-poller.sh
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-gateway.sh
          # F12: the MAC helper reads the token from here, not from argv.
          # R1: umask 077 so the temp file is never readable before chmod.
          ( umask 077; printf '%s\n' "$NEW_TOKEN" > /etc/eshu-token.tmp ) && chmod 600 /etc/eshu-token.tmp && mv -f /etc/eshu-token.tmp /etc/eshu-token 2>/dev/null || true
          GATEWAY_TOKEN="$NEW_TOKEN"
        fi
        logger -t eshu-poller "Update to $NEW_VER completed (trigger $TRIGGER_ID)."
        rm -f "$UPDATE_FILE"
      fi
    fi

    # Check for rollback trigger (separate from update — downloads backup installer)
    ROLLBACK_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_rollback','0'))" < "$POLICY_FILE" 2>/dev/null || echo "0")
    LAST_ROLLBACK_ID=$(cat /var/run/eshu.last_rollback_trigger 2>/dev/null || echo "0")
    if [ -n "$ROLLBACK_ID" ] && [ "$ROLLBACK_ID" != "0" ] && [ "$ROLLBACK_ID" != "$LAST_ROLLBACK_ID" ]; then
      logger -t eshu-poller "Rollback triggered (rollback $ROLLBACK_ID). Downloading rollback installer..."
      ROLLBACK_FILE=$(mktemp -p "$ESHU_WORK" rollback.XXXXXX)
      curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m 60 -s -o "$ROLLBACK_FILE" "$DASHBOARD_URL/api/gateway-script-rollback" 2>/dev/null
      if [ -s "$ROLLBACK_FILE" ] && head -1 "$ROLLBACK_FILE" | grep -q '^#!/bin/bash'; then
        chmod +x "$ROLLBACK_FILE"
        echo "$ROLLBACK_ID" > /var/run/eshu.last_rollback_trigger
        # Also update the update trigger file so we don't re-update after rollback
        echo "$ROLLBACK_ID" > "$TRIGGER_FILE"
        LAST_TRIGGER_ID="$ROLLBACK_ID"
        sudo bash "$ROLLBACK_FILE" --update 2>&1 | logger -t eshu-poller
        logger -t eshu-poller "Rollback completed (rollback $ROLLBACK_ID)."
        rm -f "$ROLLBACK_FILE"
      fi
    fi

    # Check for dev update trigger (dev-mode gateways only)
    DEV_UPDATE_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('trigger_dev_update',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    DEV_INSTALLER_URL=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('dev_installer_url',''))" < "$POLICY_FILE" 2>/dev/null || echo "")
    LAST_DEV_UPDATE_ID=$(cat /var/run/eshu.last_dev_update_trigger 2>/dev/null || echo "0")
    if [ -n "$DEV_UPDATE_ID" ] && [ "$DEV_UPDATE_ID" != "" ] && [ "$DEV_UPDATE_ID" != "$LAST_DEV_UPDATE_ID" ] && [ -n "$DEV_INSTALLER_URL" ]; then
      logger -t eshu-poller "Dev update triggered (trigger $DEV_UPDATE_ID). Downloading dev installer from $DEV_INSTALLER_URL..."
      FULL_DEV_URL="$DASHBOARD_URL$DEV_INSTALLER_URL"
      DEV_UPDATE_FILE=$(mktemp -p "$ESHU_WORK" dev-update.XXXXXX)
      curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m 60 -s -o "$DEV_UPDATE_FILE" "$FULL_DEV_URL" 2>/dev/null
      if [ -s "$DEV_UPDATE_FILE" ] && head -1 "$DEV_UPDATE_FILE" | grep -q '^#!/bin/bash'; then
        chmod +x "$DEV_UPDATE_FILE"
        echo "$DEV_UPDATE_ID" > /var/run/eshu.last_dev_update_trigger
        sudo bash "$DEV_UPDATE_FILE" --update 2>&1 | logger -t eshu-poller
        logger -t eshu-poller "Dev update to trigger $DEV_UPDATE_ID completed."
        rm -f "$DEV_UPDATE_FILE"
        # Re-register with updated version
        NEW_VER=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s "$DASHBOARD_URL/api/version" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('version','$DASH_VER'))" 2>/dev/null || echo "$DASH_VER")
        REG_RESP=$(curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/register" \
             -H "Content-Type: application/json" \
             -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
             -H "X-Enrollment-Token: ${ESHU_ENROLL_TOKEN:-}" \
             -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$NEW_VER"'"}' 2>/dev/null || echo "")
        NEW_TOKEN=$(echo "$REG_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('gateway_token',''))" 2>/dev/null || echo "")
        if [ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "None" ]; then
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-poller.sh
          sed -i "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=\"$NEW_TOKEN\"|" /usr/local/bin/eshu-gateway.sh
          # F12: the MAC helper reads the token from here, not from argv.
          # R1: umask 077 so the temp file is never readable before chmod.
          ( umask 077; printf '%s\n' "$NEW_TOKEN" > /etc/eshu-token.tmp ) && chmod 600 /etc/eshu-token.tmp && mv -f /etc/eshu-token.tmp /etc/eshu-token 2>/dev/null || true
          GATEWAY_TOKEN="$NEW_TOKEN"
        fi
      else
        logger -t eshu-poller "Dev update failed: could not download script from $FULL_DEV_URL"
        rm -f "$DEV_UPDATE_FILE"
      fi
    fi

    # Remember this policy body only after a successful parse and once every
    # expected file is in place. A malformed body or a partial write must retry
    # next cycle, so the stamp is never advanced on a failed sync.
    if [ "$POLICY_JSON_OK" = "1" ] && [ -n "$POLICY_HASH" ] && policy_files_ok; then
      printf '%s' "$POLICY_STAMP_VERSION:$POLICY_HASH" > "$POLICY_STAMP.tmp" 2>/dev/null &&
        mv -f "$POLICY_STAMP.tmp" "$POLICY_STAMP" 2>/dev/null || true
    fi
  fi

  rm -f "$POLICY_FILE"

  # 2. Poll for JIT Tickets
  # `grep -c` prints 0 AND exits 1 on an empty file, so the old
  # `|| echo 0` made WC "0<newline>0": curl refused the URL (exit 3) and no
  # approved ticket was ever fetched. Take grep's count alone, digits only.
  WC=$(grep -c . /etc/eshu-windows.txt 2>/dev/null) || true
  case "$WC" in ''|*[!0-9]*) WC=0 ;; esac
  TICKET_FILE=$(mktemp -p "$ESHU_WORK" ticket.XXXXXX)
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H "X-Gateway-Token: $GATEWAY_TOKEN" "$DASHBOARD_URL/api/poll/$TARGET_IP?wc=$WC" > "$TICKET_FILE"
  # Only start python when the response actually carries a ticket string. The
  # endpoint always returns {"ticket":null,...}, and parsing that every cycle
  # would keep one unnecessary interpreter start on the loop.
  if [ -s "$TICKET_FILE" ] && grep -qE '"ticket"[[:space:]]*:[[:space:]]*"' "$TICKET_FILE"; then
    TICKET=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('ticket') or '')" < "$TICKET_FILE" 2>/dev/null)
    if [ -n "$TICKET" ] && [ "$TICKET" != "null" ]; then
      # BI1: a ticket is one line. The dashboard refuses these now; this is
      # the second check, so a malformed or hostile response cannot add
      # several lines to the lockbox either.
      case "$TICKET" in
        *$'\n'*|*$'\r'*)
          logger -t eshu-poller "refused a lockbox ticket containing a line break"
          ;;
        *)
          if tickets_lock; then
            printf '%s\n' "$TICKET" >> /var/run/eshu.tickets
            chmod 600 /var/run/eshu.tickets
            tickets_unlock
          fi
          ;;
      esac
    fi
  fi
  rm -f "$TICKET_FILE"
  sleep "$POLL_INTERVAL"
done
POLLEREOF
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$POLLER_SCRIPT"
  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$POLLER_SCRIPT"
  sed -i "s|__HOST_NAME__|$HOST_NAME|g" "$POLLER_SCRIPT"
  # Scope the token replacement to the header assignment ONLY — an unanchored or
  # global replace could corrupt other lines that reference GATEWAY_TOKEN.
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
# Survives all gateway/poller script updates. The gateway API token is read from
# the gateway script at runtime (not templated in) so it always tracks the
# current token without coupling this file to installer substitutions.

set -eo pipefail

DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || echo "")
if [ -z "$DASHBOARD_URL" ]; then
    logger -t eshu-logger "No /etc/eshu/dashboard_url — exiting. Set DASHBOARD_URL and rerun."
    exit 1
fi

TARGET_IP=$(hostname -I | awk '{print $1}')
HOST_NAME=$(hostname)
INTERVAL=30
HTTP_TIMEOUT="${ESHU_HTTP_TIMEOUT:-10}"
HTTP_CONNECT_TIMEOUT="${ESHU_HTTP_CONNECT_TIMEOUT:-3}"

while true; do
    POLLER_OK=0; GATEWAY_OK=0; CAN_REACH=0

    # Read the current gateway API token each cycle (tracks self-heal/updates).
    GATEWAY_TOKEN=$(grep -oP '^GATEWAY_TOKEN="\K[^"]+' /usr/local/bin/eshu-gateway.sh 2>/dev/null || true)

    systemctl is-active --quiet eshu-poller.service 2>/dev/null && POLLER_OK=1
    [ -f /usr/local/bin/eshu-gateway.sh ] && bash -n /usr/local/bin/eshu-gateway.sh >/dev/null 2>&1 && GATEWAY_OK=1
    curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s "$DASHBOARD_URL/api/version" >/dev/null 2>&1 && CAN_REACH=1

    logger -t eshu-logger "Heartbeat: poller=$POLLER_OK gw=$GATEWAY_OK reach=$CAN_REACH"

    curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/gateway-heartbeat" \
        -H "Content-Type: application/json" \
        -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
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

# F12: the MAC key material. The gateway reads this file itself, so the key
# never appears on argv. Root-only; rewritten on every install and update.
# R1: umask 077 means the temp file is never readable, not even for the
# instant before `chmod`.
if [ -n "${GATEWAY_TOKEN:-}" ] && [ "$GATEWAY_TOKEN" != "None" ]; then
  ( umask 077; printf '%s\n' "$GATEWAY_TOKEN" > /etc/eshu-token.tmp )
  chmod 600 /etc/eshu-token.tmp
  chown root:root /etc/eshu-token.tmp
  mv -f /etc/eshu-token.tmp /etc/eshu-token
fi

write_gateway; write_logger; write_sudoers; write_poller

# SSH key setup only for install/reinstall (skip on update).
# BE1: and only after a registration that returned 200. ssh_mode comes from
# that answer; without it the installer cannot know whether the shared key
# line is allowed (an mcp_only gateway must not get it back because the
# dashboard was unreachable for a moment), so authorized_keys is left exactly
# as it is. A fresh install then has no keys at all: it fails closed.
if { [ "$mode" = "install" ] || [ "$mode" = "reinstall" ]; } && [ "${REGISTER_OK:-}" != "1" ]; then
  echo ""
  echo "   WARNING: registration did not succeed (HTTP ${REGISTER_STATUS:-none}), so the"
  echo "            SSH keys were NOT changed. Re-run the installer once the"
  echo "            dashboard accepts the registration."
fi
if { [ "$mode" = "install" ] || [ "$mode" = "reinstall" ]; } && [ "${REGISTER_OK:-}" = "1" ]; then
  echo ""
  if [ -z "$GATEWAY_PUB_KEY" ]; then read -r -p "Enter SSH pubkey for ESHU GATEWAY: " GATEWAY_PUB_KEY; fi

  if [ -z "$GATEWAY_PUB_KEY" ]; then
    echo "ERROR: The Eshu Gateway SSH key is required. Aborting."; exit 1
  fi

  mkdir -p "/home/$USER/.ssh"; chmod 700 "/home/$USER/.ssh"
  # F12: two lines, each with its own comment (eshu-gateway-key, eshu-dashboard),
  # so a re-run replaces them by comment instead of appending duplicates. The
  # dashboard line's forced command carries the one fixed --dashboard-key
  # argument; sshd sets it, so a shared-key caller cannot (G6).
  AK='command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,restrict'
  if [ -n "$ALLOWED_SSH_FROM" ]; then AK='from="'"$ALLOWED_SSH_FROM"'",'"$AK"; fi
  AK_DASH='command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh --dashboard-key",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,restrict'
  if [ -n "$ALLOWED_SSH_FROM" ]; then AK_DASH='from="'"$ALLOWED_SSH_FROM"'",'"$AK_DASH"; fi
  AK_FILE="/home/$USER/.ssh/authorized_keys"
  {
    # Keep keys this installer does not own; drop every Eshu gateway line,
    # including a pre-F12 one without a comment, then write ours back.
    if [ -f "$AK_FILE" ]; then
      grep -v 'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh' "$AK_FILE" || true
    fi
    if [ "${GATEWAY_SSH_MODE:-direct+mcp}" != "mcp_only" ]; then
      printf '%s %s eshu-gateway-key\n' "$AK" "$GATEWAY_PUB_KEY"
    fi
    if [ -n "$DASHBOARD_SSH_PUB" ]; then
      printf '%s %s eshu-dashboard\n' "$AK_DASH" "$DASHBOARD_SSH_PUB"
    fi
  } > "$AK_FILE.tmp"
  mv -f "$AK_FILE.tmp" "$AK_FILE"
  chown -R "$USER":"$USER" "/home/$USER/.ssh"; chmod 600 "$AK_FILE"
fi

echo ""
echo "✅ Gateway $GATEWAY_VERSION deployed."
echo "   - Hardcoded catastrophic blocklist active"
echo "   - JIT auto-poll enabled (waits up to 90s for approval)"
echo "   - Trigger-based updates enabled"
exit 0