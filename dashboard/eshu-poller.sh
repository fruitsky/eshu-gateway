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
