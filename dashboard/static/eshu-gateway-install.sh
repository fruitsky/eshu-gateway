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
REGISTER_RESPONSE=""
if [ "$mode" != "upgrade" ]; then
  echo "Registering Gateway with Dashboard..."
  REGISTER_RESPONSE=$(curl --connect-timeout 3 -m 15 -s -X POST "$DASHBOARD_URL/api/register" \
       -H "Content-Type: application/json" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "X-Enrollment-Token: ${ESHU_ENROLL_TOKEN:-}" \
       -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$GATEWAY_VERSION"'"}')
  GATEWAY_TOKEN=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); t=d.get('gateway_token'); print(t if t and t!='None' else '')" 2>/dev/null || echo "")
  if [ -n "$GATEWAY_TOKEN" ]; then
    echo "   Gateway API token received (v15+ auth)"
  fi
fi

# Always re-register on update to report new version (and get token if missing)
if [ "$mode" = "upgrade" ] && [ -n "$DASHBOARD_URL" ]; then
  REGISTER_RESPONSE=$(curl --connect-timeout 3 -m 15 -s -X POST "$DASHBOARD_URL/api/register" \
       -H "Content-Type: application/json" \
       -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
       -H "X-Enrollment-Token: ${ESHU_ENROLL_TOKEN:-}" \
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
ESHU_RUN_OWNER="${ESHU_RUN_OWNER:-0}"
ESHU_CAPTURE_FILE="${ESHU_CAPTURE_FILE:-/etc/eshu-capture}"
ESHU_OUT_TAIL=""
RESULT_REQUEST_ID=""
RESULT_PATH="allowlist"

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

# F5: pass every line through to the agent AND keep the last 16 KiB of it for
# the execution result. One process, bounded memory (≈ 16 K + one line).
#
# This used to be `tee` into a FIFO with a second `tail` reader. The reader is
# a child the script must wait for, and on some hosts (GitHub's CI runners,
# reproducibly) that reader never saw EOF and the call hangs — a gateway call
# that never returns is a root process that never returns. Fusing the "keep the
# tail" step into the stream itself removes the second process, the FIFO and
# the wait; the capture file is written by END, in the same process.
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
capture_tail() {   # $1 = capture file
  # C1: the capture is best effort. If the file cannot be written (tmpfs full,
  # the path replaced), awk exits non-zero at END — after the agent already got
  # every line — and no diagnostic is pushed into the agent's stream.
  LC_ALL=C awk -v cap="$1" '
    BEGIN { head = 1 }
    { print; fflush()
      n++
      slot = n % 20000
      line[slot] = $0
      size[slot] = length($0) + 1
      total += size[slot]
      while (n - head >= 1 && total - size[head % 20000] >= 16385) {
        # C5: free the slot. Advancing `head` alone kept up to 20000 old lines
        # alive (395 MB resident for 200 MB of output).
        slot = head % 20000
        total -= size[slot]
        delete line[slot]
        delete size[slot]
        head++
      } }
    END {
      for (i = head; i <= n; i++) {
        s = line[i % 20000]
        if (i == head && total > 16385) s = substr(s, total - 16385 + 1)
        printf "%s\n", s > cap
      }
      close(cap) }' 2>/dev/null \
    || logger -t eshu-gateway "CAPTURE FAILED: cannot write the capture file"
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
  if [ "$mode" = "tail" ] && [ -n "$ESHU_OUT_TAIL" ] && [ -f "$ESHU_OUT_TAIL" ] \
     && [ -s "$ESHU_OUT_TAIL" ]; then
    if [ "$(stat -c '%s' "$ESHU_OUT_TAIL")" -gt 16384 ]; then
      truncated="true"
      enc_out=$(tail -c 16384 "$ESHU_OUT_TAIL" | base64 -w 0) || enc_out=""
    else
      enc_out=$(base64 -w 0 < "$ESHU_OUT_TAIL") || enc_out=""
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
  body='{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'","path":"'"$path"'","exit_code":'"$rc"',"duration_ms":'"$duration_ms"',"encoded_output":"'"$enc_out"'","output_truncated":'"$truncated"''"$rid"'}'
  # D2: the body (up to ~22 KB) goes on stdin, never into argv.
  { printf '%s' "$body" | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
         -X POST "$DASHBOARD_URL/api/exec-result" \
         -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
         -H "Content-Type: application/json" \
         --data-binary @- ; } >/dev/null 2>&1 &
}

run_sanitized() {
  local rc=0 mode start_ns end_ns duration_ms path
  mode=$(capture_mode)
  path="${RESULT_PATH:-allowlist}"
  case "$path" in allowlist|window|lockbox|jit) ;; *) path="allowlist" ;; esac
  ESHU_OUT_TAIL=""
  if [ "$mode" = "tail" ]; then
    # § 2.7 / D7: only the poller's private 0700 root-owned work dir; refuse a
    # missing, symlinked, foreign-mode or foreign-owner one instead of writing
    # capture files somewhere else. Never create the dir here.
    if [ ! -d "$ESHU_RUN_DIR" ] || [ -L "$ESHU_RUN_DIR" ] \
       || [ "$(stat -c '%a' "$ESHU_RUN_DIR")" != "700" ] \
       || [ "$(stat -c '%u' "$ESHU_RUN_DIR")" != "$ESHU_RUN_OWNER" ]; then
      logger -t eshu-gateway "CAPTURE DISABLED: unsafe $ESHU_RUN_DIR"
      mode="exit-only"
    else
      ESHU_OUT_TAIL=$(mktemp -p "$ESHU_RUN_DIR" eshu-out.XXXXXX) || {
        logger -t eshu-gateway "CAPTURE DISABLED: mktemp failed in $ESHU_RUN_DIR"
        mode="exit-only"; ESHU_OUT_TAIL=""; }
      if [ -n "$ESHU_OUT_TAIL" ]; then
        # F5: the first EXIT trap in this script. A later one must extend this,
        # not replace it, or the capture file leaks. The capture is best
        # effort: a file already gone (or replaced) must not change the call's
        # exit status or write a diagnostic into the agent's stream.
        trap 'rm -f "$ESHU_OUT_TAIL" 2>/dev/null || true' EXIT
      else
        mode="exit-only"
      fi
    fi
  fi

  start_ns=$(date +%s%N)
  # C1: the command's status is the first pipeline element's, never the
  # filter's. `set -o pipefail` is right for a plain pipeline but wrong here:
  # under it an unwritable capture file (awk exits 2) turned rc 0 into 2 and
  # rc 3 into 2, i.e. the trimmer's failure was reported as the command's.
  # `set +e` keeps `set -e` from exiting before the sentinel and the post.
  set +e
  if [ "$mode" = "tail" ] && [ -n "$ESHU_OUT_TAIL" ]; then
    # E1: the capture rides in the stream, so an agent disconnect still ends
    # the command at its next write, and there is no reader to wait for.
    "$@" 2>&1 | sanitize | capture_tail "$ESHU_OUT_TAIL"
  else
    "$@" 2>&1 | sanitize
  fi
  rc=${PIPESTATUS[0]}
  set -e
  end_ns=$(date +%s%N)
  duration_ms=$(( (end_ns - start_ns) / 1000000 ))

  if [ "$rc" -ne 0 ]; then
    echo "[ESHU exit=$rc]"
  fi
  post_exec_result "$rc" "$duration_ms" "$mode" "$path"
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
    *"/usr/local/bin/eshu-"*|*"/etc/eshu"*|*"/var/run/eshu"*|*"/run/eshu"*|*"eshu.db"*|*"eshu.tickets"*|*"eshu-session-key"*|*"eshu/features.d"*|*"/etc/sudoers.d/eshu"*)
        self_access=1
        ;;
esac
if [ "$self_access" = "1" ]; then
    logger -t eshu-gateway "HARDCODED BLOCK: Eshu self-access attempt: $cmd"
    log_auto_approve "$cmd" "blocked"
    echo "[LOCKED] FATAL: Access to Eshu Gateway files is permanently blocked. [Gateway $GATEWAY_VERSION]"
    exit 1
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

if [ "$ENCODED_EXEC" = "0" ] && [ "$HAS_META" = "0" ] && [ "$SELF_JIT" = "0" ] && [ "$ZT_ACTIVE" != "1" ] && [ -f /etc/eshu-exact.txt ] && grep -qFx "$cmd" /etc/eshu-exact.txt; then
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
    if [ "$ENCODED_EXEC" = "0" ] && [ "$HAS_META" = "0" ] && [ "$SELF_JIT" = "0" ] && [ "$ZT_ACTIVE" != "1" ] && [[ "$cmd" =~ $pattern ]]; then
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
if [ "$ENCODED_EXEC" = "0" ] && [ "$SELF_JIT" = "0" ] && [ -d "$FEATURES_DIR" ]; then
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
  RESULT_PATH="jit"
  RESULT_REQUEST_ID="$REQUEST_ID_INT"
  run_sanitized bash -c "$cmd"
else
  exit 1
fi
GWEOF

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

# Clear stale tickets from previous sessions
if [ -f /var/run/eshu.tickets ]; then
  rm -f /var/run/eshu.tickets
fi

while true; do
  POLICY_FILE=$(mktemp -p "$ESHU_WORK" policy.XXXXXX)
  # Clean expired lockbox tickets (older than 90s)
  if [ -f /var/run/eshu.tickets ] && [ -s /var/run/eshu.tickets ]; then
    NOW=$(date +%s)
    awk -F'|' -v now="$NOW" '($1+90) >= now { print }' /var/run/eshu.tickets > /var/run/eshu.tickets.tmp
    mv -f /var/run/eshu.tickets.tmp /var/run/eshu.tickets
    chmod 600 /var/run/eshu.tickets
  fi

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
  WC=$(grep -c . /etc/eshu-windows.txt 2>/dev/null || echo 0)
  TICKET_FILE=$(mktemp -p "$ESHU_WORK" ticket.XXXXXX)
  curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H "X-Gateway-Token: $GATEWAY_TOKEN" "$DASHBOARD_URL/api/poll/$TARGET_IP?wc=$WC" > "$TICKET_FILE"
  # Only start python when the response actually carries a ticket string. The
  # endpoint always returns {"ticket":null,...}, and parsing that every cycle
  # would keep one unnecessary interpreter start on the loop.
  if [ -s "$TICKET_FILE" ] && grep -qE '"ticket"[[:space:]]*:[[:space:]]*"' "$TICKET_FILE"; then
    TICKET=$(python3 -c "import sys, json; print(json.load(sys.stdin).get('ticket') or '')" < "$TICKET_FILE" 2>/dev/null)
    if [ -n "$TICKET" ] && [ "$TICKET" != "null" ]; then
      echo "$TICKET" >> /var/run/eshu.tickets
      chmod 600 /var/run/eshu.tickets
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