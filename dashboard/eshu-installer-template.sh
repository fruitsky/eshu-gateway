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

# X8: which installer this is, so the dashboard can say Fleet / Edge / Build.
# The official install, reinstall and update flows all download the file to a
# private temp and run it as a file, so "$0" is it. A `curl | bash` install has
# no file to hash: the value stays empty and the dashboard says "unknown".
INSTALLER_HASH=""
if [ -f "$0" ] && [ -r "$0" ]; then
  INSTALLER_HASH=$(sha256sum -- "$0" 2>/dev/null | cut -c1-12 || true)
fi
case "$INSTALLER_HASH" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) INSTALLER_HASH="" ;;
esac

# BE4: replace a file through a private temp in its own directory, refusing a
# planted symlink at the file. stdin is the new content; the owner and mode of
# an existing file are kept, and a new file takes the directory's owner.
eshu_safe_replace() {   # $1 = file
  local _f _dir _tmp _owner _mode
  _f="$1"; _dir=$(dirname "$_f")
  if [ -L "$_f" ]; then
    echo "REFUSED: $_f is a symlink; not changed."
    cat >/dev/null
    return 1
  fi
  [ -d "$_dir" ] || { cat >/dev/null; return 1; }
  if [ -e "$_f" ]; then
    _owner=$(stat -c '%u:%g' "$_f" 2>/dev/null) || _owner=""
    _mode=$(stat -c '%a' "$_f" 2>/dev/null) || _mode=""
  else
    _owner=$(stat -c '%u:%g' "$_dir" 2>/dev/null) || _owner=""
    _mode="600"
  fi
  _tmp=$(mktemp "$_dir/.$(basename "$_f").XXXXXX") || { cat >/dev/null; return 1; }
  if ! cat > "$_tmp"; then rm -f "$_tmp"; return 1; fi
  [ -n "$_owner" ] && chown "$_owner" "$_tmp" 2>/dev/null || true
  [ -n "$_mode" ] && chmod "$_mode" "$_tmp" 2>/dev/null || true
  mv -f "$_tmp" "$_f"
}

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

# BG1: every authenticated call now sends its token in a header read from
# stdin (`curl -H @-`, and a private process substitution for the result
# post). `-H @filename` needs curl 7.55. Older curl accepts the argument
# shape but silently drops the header, so the token never leaves the host and
# every registration/poll would 401 with no hint why. Checked once, in every
# mode that writes to the host, before anything is changed. Uninstall is
# exempt: removing a gateway must keep working on hosts we no longer install
# to.
check_curl_version() {
  local line ver major minor
  line=$(curl --version 2>/dev/null | head -n1 || true)
  ver=${line#curl }; ver=${ver%% *}
  major=${ver%%.*}
  minor=${ver#*.}; minor=${minor%%.*}
  if ! printf '%s' "$major" | grep -qE '^[0-9]+$' \
     || ! printf '%s' "$minor" | grep -qE '^[0-9]+$'; then
    echo ""
    echo "❌ Eshu Gateway requires curl 7.55 or newer, and curl was not found."
    echo "   Install curl (Debian/Ubuntu: apt-get install curl) and re-run."
    echo "   Nothing on this host has been changed."
    echo ""
    return 1
  fi
  if [ "$major" -lt 7 ] || { [ "$major" -eq 7 ] && [ "$minor" -lt 55 ]; }; then
    echo ""
    echo "❌ Eshu Gateway requires curl 7.55 or newer; this host has curl $ver."
    echo "   The gateway reads its token header from stdin (curl -H @-), which"
    echo "   older curl accepts but silently drops: every call would get 401."
    echo "   Upgrade curl (Debian/Ubuntu: apt-get update && apt-get install curl)"
    echo "   and re-run. Nothing on this host has been changed."
    echo ""
    return 1
  fi
  return 0
}

if [ "$UNINSTALL" != "yes" ]; then
  if ! check_curl_version; then exit 1; fi
fi

# Handle --uninstall immediately before any other logic
if [ "$UNINSTALL" = "yes" ]; then
  # Extract DASHBOARD_URL from existing gateway script if not provided
  if [ -z "$DASHBOARD_URL" ] && [ -f /usr/local/bin/eshu-gateway.sh ]; then
    DASHBOARD_URL=$(grep -oP 'DASHBOARD_URL="\K[^"]+' /usr/local/bin/eshu-gateway.sh 2>/dev/null || true)
  fi
  # Extract the gateway API token so the uninstall progress reports authenticate.
  # BQ4: it lives in /etc/eshu-token; an old baked script is the fallback.
  GATEWAY_TOKEN=$(head -n1 /etc/eshu-token 2>/dev/null | tr -d '[:space:]' || true)
  if [ -z "$GATEWAY_TOKEN" ]; then
    GATEWAY_TOKEN=$(grep -oP '^GATEWAY_TOKEN="\K[^"]+' /usr/local/bin/eshu-gateway.sh 2>/dev/null || true)
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
      { printf 'X-Gateway-Token: %s\n' "${GATEWAY_TOKEN:-}" \
          | curl -m 3 -s -X POST "$DASHBOARD_URL/api/uninstall-progress" \
        -H @- \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$TARGET_IP\",\"step\":\"$step\",\"message\":\"$message\"}" ; } >/dev/null 2>&1 || true
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
    { grep -v "eshu-gateway" /root/.ssh/authorized_keys 2>/dev/null || true; } \
      | eshu_safe_replace /root/.ssh/authorized_keys || true
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
    { grep -v "hermes-diag" /root/.ssh/authorized_keys 2>/dev/null || true; } \
      | eshu_safe_replace /root/.ssh/authorized_keys || true
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
    { grep -v "hermes-diag" /root/.ssh/authorized_keys 2>/dev/null || true; } \
      | eshu_safe_replace /root/.ssh/authorized_keys || true
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
    { grep -v "hermes-diag" /root/.ssh/authorized_keys 2>/dev/null || true; } \
      | eshu_safe_replace /root/.ssh/authorized_keys || true
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
# then refused it as ssh_host_unpinned). BQ4: the one source is
# /etc/eshu-token; an old baked script is the fallback for a gateway whose
# file was lost. "None" is the v15.0 migration bug; it is not a token.
_valid_token() {
  case "${1:-}" in ""|None|__GATEWAY_TOKEN__) return 1 ;; esac
  printf '%s' "$1" | grep -qE '^[A-Za-z0-9_-]{16,128}$'
}
if ! _valid_token "${GATEWAY_TOKEN:-}"; then
  GATEWAY_TOKEN=""
  if [ -f /etc/eshu-token ]; then
    EXISTING_TOKEN=$(head -n1 /etc/eshu-token 2>/dev/null | tr -d '[:space:]' || true)
    if _valid_token "$EXISTING_TOKEN"; then GATEWAY_TOKEN="$EXISTING_TOKEN"; fi
  fi
  # Compatibility: a gateway installed before BQ4 has no token file only if
  # something removed it; its baked script still names the token.
  if [ -z "$GATEWAY_TOKEN" ] && [ -f "$GATEWAY" ]; then
    EXISTING_TOKEN=$(grep -oP '^GATEWAY_TOKEN="\K[^"]+' "$GATEWAY" 2>/dev/null | head -n1 || true)
    if _valid_token "$EXISTING_TOKEN"; then GATEWAY_TOKEN="$EXISTING_TOKEN"; fi
  fi
  EXISTING_TOKEN=""
  if [ -n "$GATEWAY_TOKEN" ]; then echo "   Reusing existing gateway API token"; fi
fi

# RR1: one registration call for install, reinstall and update. Sets
# REGISTER_STATUS (the HTTP code, 000 when unreachable) and REGISTER_RESPONSE.
register_gateway() {   # $1 = the X-Gateway-Token to present ('' for none)
  local out
  out=$({ printf 'X-Gateway-Token: %s\n' "${1:-}"
           printf 'X-Enrollment-Token: %s\n' "${ESHU_ENROLL_TOKEN:-}" ; } \
         | curl --connect-timeout 3 -m 15 -s -w '\n%{http_code}' -X POST "$DASHBOARD_URL/api/register" \
       -H @- \
       -H "Content-Type: application/json" \
       -d '{"ip":"'"$TARGET_IP"'","hostname":"'"$HOST_NAME"'","version":"'"$GATEWAY_VERSION"'",'"$SSH_REG_CAPS"',"installer_hash":"'"$INSTALLER_HASH"'"}' 2>/dev/null) \
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
  # BQ4: the script carries no token; /etc/eshu-token does. The script is
  # still root-only (0700) because it is root-executed code. Create it (and
  # its temporary backup) root-only from the first byte, and finish
  # 0700 root:root in every mode — install, reinstall and update.
  (
    umask 077
    cp -f "$GATEWAY" "${GATEWAY}.bak" 2>/dev/null || true
    cat > "$GATEWAY" <<'GWEOF'
__GATEWAY_CONTENT__
GWEOF
  )

  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$GATEWAY"
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$GATEWAY"
  sed -i "s|__GATEWAY_VERSION__|$GATEWAY_VERSION|g" "$GATEWAY"
  # BQ4: no token substitution. The script reads /etc/eshu-token.

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
__POLLER_CONTENT__
POLLEREOF
  sed -i "s|__DASHBOARD_URL__|$DASHBOARD_URL|g" "$POLLER_SCRIPT"
  sed -i "s|__TARGET_IP__|$TARGET_IP|g" "$POLLER_SCRIPT"
  sed -i "s|__HOST_NAME__|$HOST_NAME|g" "$POLLER_SCRIPT"
  # BQ4: no token substitution. The poller reads /etc/eshu-token.
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
__LOGGER_CONTENT__
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

# X8: the installer hash the dashboard reports (and the logger re-sends). It is
# a hash of a public file, so 0644 root:root is fine: not secret, just not
# writable by the gateway user.
if [ -n "$INSTALLER_HASH" ]; then
  ( umask 022; printf '%s\n' "$INSTALLER_HASH" > /etc/eshu-installer-hash.tmp )
  chmod 644 /etc/eshu-installer-hash.tmp
  chown root:root /etc/eshu-installer-hash.tmp
  mv -f /etc/eshu-installer-hash.tmp /etc/eshu-installer-hash
fi

write_gateway; write_logger; write_sudoers; write_poller

# SSH key setup only for install/reinstall (skip on update).
# BE1: and only after a registration that returned 200. ssh_mode comes from
# that answer; without it the installer cannot know whether the shared key
# line is allowed (an mcp_only gateway must not get it back because the
# dashboard was unreachable for a moment), so authorized_keys is left exactly
# as it is. A fresh install then has no keys at all: it fails closed.
#
# BE4: an update now refreshes ONLY the `eshu-dashboard` line, and only after
# a 200 too. The shared gateway line is never touched on update.

# BE4: the `from=` of the existing Eshu lines, so a refreshed dashboard line
# keeps the restriction the operator set. Empty when there is none.
ak_from() {   # $1 = file  $2 = comment
  # `|| true`: with `set -o pipefail`, sed can see SIGPIPE when head stops
  # after the first match; that must not abort the installer.
  sed -n "s/^from=\"\([^\"]*\)\".* $2\$/\1/p" "$1" 2>/dev/null | head -n 1 || true
}

# BH2: run the user-side write as the gateway user. The checks above are the
# first line, but a symlink swapped in between the check and the write used to
# be followed by root: `mktemp`, `mv` and `chmod` could reach root's
# authorized_keys or a system directory. As the user, a swap can only reach
# what that user could already write.
ak_as_user() {   # $@ = command, run as $USER
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$USER" -- "$@"
  elif command -v setpriv >/dev/null 2>&1; then
    setpriv --reuid="$(id -u "$USER")" --regid="$(id -g "$USER")"             --init-groups "$@"
  else
    echo "   REFUSED: neither runuser nor setpriv is available; authorized_keys not changed."
    return 1
  fi
}

# BE4: replace authorized_keys through a private temp in the same directory.
# A planted symlink at the directory or the file is refused; the fixed
# `.tmp` name an attacker could pre-create is gone. BH2: the create and the
# rename happen as $USER, never as root.
ak_replace() {   # stdin = the new file
  local ak_home ak_dir ak_file ak_owner
  ak_home="/home/$USER"; ak_dir="$ak_home/.ssh"; ak_file="$ak_dir/authorized_keys"
  # BQ2: if the home itself is not the user's, the user-side create cannot be
  # trusted (and root must not "fix" it). Refuse and change nothing.
  ak_owner=$(stat -c '%u' "$ak_home" 2>/dev/null || echo "")
  if [ "$ak_owner" != "$(id -u "$USER" 2>/dev/null)" ]; then
    echo "   REFUSED: $ak_home is not owned by $USER; authorized_keys not changed."
    cat >/dev/null
    return 1
  fi
  if [ -L "$ak_dir" ] || { [ -e "$ak_file" ] && [ -L "$ak_file" ]; }; then
    echo "   REFUSED: $ak_dir or $ak_file is a symlink; authorized_keys not changed."
    cat >/dev/null
    return 1
  fi
  if ! ak_as_user sh -c '
      set -eu
      dir=$1
      mkdir -p "$dir" 2>/dev/null || exit 1
      [ ! -L "$dir" ] || exit 2
      [ ! -L "$dir/authorized_keys" ] || exit 2
      tmp=$(mktemp "$dir/.authorized_keys.XXXXXX") || exit 3
      cat > "$tmp" || { rm -f "$tmp"; exit 4; }
      chmod 600 "$tmp" || { rm -f "$tmp"; exit 5; }
      mv -f "$tmp" "$dir/authorized_keys" || { rm -f "$tmp"; exit 6; }
      chmod 700 "$dir" || exit 7
    ' sh "$ak_dir"; then
    cat >/dev/null
    echo "   REFUSED: could not replace $ak_file as $USER; it was not changed."
    return 1
  fi
}

if [ "$mode" = "update" ] && [ "${REGISTER_OK:-}" = "1" ]; then
  # BE4: one ssh-ed25519 line only; empty or malformed leaves the file alone.
  if [ -z "${DASHBOARD_SSH_PUB:-}" ]; then
    :
  elif ! [[ "$DASHBOARD_SSH_PUB" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [^[:cntrl:]]*)?$ ]]; then
    echo "   WARNING: the dashboard key is not a single ssh-ed25519 line;"
    echo "            authorized_keys was not changed."
  else
    AK_DIR="/home/$USER/.ssh"
    AK_FILE="$AK_DIR/authorized_keys"
    if [ -L "$AK_DIR" ] || { [ -e "$AK_FILE" ] && [ -L "$AK_FILE" ]; }; then
      echo "   REFUSED: $AK_DIR or $AK_FILE is a symlink; authorized_keys not changed."
    elif [ -e "$AK_FILE" ] && [ ! -r "$AK_FILE" ]; then
      echo "   REFUSED: $AK_FILE is not readable; authorized_keys not changed."
    else
      # BH1: an unreadable `from=` must not silently become an unrestricted
      # line. If an Eshu line carries a from= that ak_from cannot parse, or if
      # there is no Eshu line at all (an old mcp_only install), leave the file
      # alone: we cannot know the operator's intent, and a dashboard key open
      # to the internet is worse than a missing one.
      AK_ESH_LINE=""
      AK_DASH_FROM=""
      AK_BAD_FROM=""
      if [ -f "$AK_FILE" ]; then
        AK_ESH_LINE=$(grep -m1 '/usr/local/bin/eshu-gateway.sh' "$AK_FILE" 2>/dev/null || true)
        AK_DASH_FROM=$(ak_from "$AK_FILE" eshu-dashboard)
        [ -n "$AK_DASH_FROM" ] || AK_DASH_FROM=$(ak_from "$AK_FILE" eshu-gateway-key)
        # BJ1: look at every Eshu line, not only the first. One line whose
        # from= is not in the leading `from="...,` form means we cannot know
        # the operator's intent (for example, the gateway line is unrestricted
        # while a hand-edited dashboard line is restricted), so nothing is
        # written. `grep -q` is avoided on purpose: under `set -o pipefail` an
        # early exit gives the upstream grep SIGPIPE and the pipeline fails
        # even when the pattern matched.
        AK_BAD_LINES=$(grep '/usr/local/bin/eshu-gateway.sh' "$AK_FILE" 2>/dev/null \
                       | grep -v '^from="[^"]*",' || true)
        case "$AK_BAD_LINES" in
          *from=*) AK_BAD_FROM="1" ;;
        esac
      fi
      if [ -z "$AK_ESH_LINE" ]; then
        echo "   NOTE: no Eshu SSH line was found in $AK_FILE;"
        echo "         reinstall to add the dashboard key."
      elif [ -n "$AK_BAD_FROM" ]; then
        echo "   WARNING: an Eshu SSH line has a from= that could not be read;"
        echo "            authorized_keys was not changed."
      else
        AK_DASH='command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh --dashboard-key",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty,restrict'
        if [ -n "$AK_DASH_FROM" ]; then AK_DASH='from="'"$AK_DASH_FROM"'",'"$AK_DASH"; fi
        {
          if [ -f "$AK_FILE" ]; then
            grep -v ' eshu-dashboard$' "$AK_FILE" || true
          fi
          printf '%s %s eshu-dashboard\n' "$AK_DASH" "$DASHBOARD_SSH_PUB"
        } | ak_replace
      fi
    fi
  fi
fi

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

  if [ -L "/home/$USER/.ssh" ]; then
    echo "   REFUSED: /home/$USER/.ssh is a symlink; authorized_keys not changed."
  else
  # BQ2: no root mkdir/chmod here. The user-side script in ak_replace creates
  # .ssh and sets 700, so a fresh install gets a user-owned directory.
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
  } | ak_replace
  fi
fi

echo ""
echo "✅ Gateway $GATEWAY_VERSION deployed."
echo "   - Hardcoded catastrophic blocklist active"
echo "   - JIT auto-poll enabled (waits up to 90s for approval)"
echo "   - Trigger-based updates enabled"
exit 0