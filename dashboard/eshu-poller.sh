#!/bin/bash
TRIGGER_FILE="/var/run/eshu.last_trigger"
DASHBOARD_URL="__DASHBOARD_URL__"
TARGET_IP="__TARGET_IP__"
HOST_NAME="__HOST_NAME__"
GATEWAY_TOKEN="__GATEWAY_TOKEN__"

# BE3: token headers never go into curl's argv (readable in /proc by any
# local user). printf is a builtin and curl reads the header lines from stdin.
gateway_header() { printf 'X-Gateway-Token: %s\n' "${GATEWAY_TOKEN:-}"; }
register_headers() {
  printf 'X-Gateway-Token: %s\n' "${GATEWAY_TOKEN:-}"
  printf 'X-Enrollment-Token: %s\n' "${ESHU_ENROLL_TOKEN:-}"
}
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
  HTTP_CODE=$(gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -w "%{http_code}" -o "$POLICY_FILE" \
       -H @- \
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
        # BJ2: no token in the unit's ExecStart (world-readable through
        # `systemctl show`); the runner reads /etc/eshu-token as root.
        if systemd-run --unit="eshu-fleet-$FLEET_CMD_ID" --collect \
            bash "$FLEET_RUNNER" "$DASHBOARD_URL" "$TARGET_IP" "$FLEET_CMD_ID" "$FLEET_CMD_TIMEOUT" "$FLEET_CMD_FILE" "$FLEET_OUT_FILE" >/dev/null 2>&1; then
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
        gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/uninstall-started/$TARGET_IP" \
             -H @- >/dev/null 2>&1 || true
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
        REG_RESP=$(register_headers | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/register" \
             -H @- \
             -H "Content-Type: application/json" \
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
        REG_RESP=$(register_headers | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -X POST "$DASHBOARD_URL/api/register" \
             -H @- \
             -H "Content-Type: application/json" \
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
  gateway_header | curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s -H @- "$DASHBOARD_URL/api/poll/$TARGET_IP?wc=$WC" > "$TICKET_FILE"
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
