#!/bin/bash
# Eshu dashboard deploy helper — run ON the dashboard host.
#
# Pulls the repo (unless --no-pull) and restarts the eshu-dashboard unit,
# preferring `systemctl restart` and falling back to a clean SIGTERM of the
# unit's MainPID when passwordless sudo is unavailable (the unit uses
# Restart=always, so systemd brings it straight back).
#
# It refuses to start a second instance when the port is held by an unmanaged
# process — that is the exact "port-conflict dance" this helper exists to avoid.
#
# Usage: bash scripts/deploy-dashboard.sh [--no-pull]
set -euo pipefail

SERVICE=eshu-dashboard
PORT="${ESHU_PORT:-8000}"
cd "$(dirname "$0")/.."

if [ "${1:-}" != "--no-pull" ]; then
  echo "== git pull --ff-only =="
  git pull --ff-only
  git checkout -- dashboard/static/ 2>/dev/null || true
fi

mainpid() { systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0; }
port_owner() {
  ss -ltnp 2>/dev/null \
    | awk -v p=":$PORT" '$4 ~ p {print $NF}' \
    | grep -oP 'pid=\K[0-9]+' | head -1 || true
}

cur="$(mainpid)"
owner="$(port_owner)"
if [ -n "$owner" ] && [ "$owner" != "0" ] && [ "$owner" != "$cur" ]; then
  echo "❌ Port $PORT is held by PID $owner, not the $SERVICE unit (MainPID $cur)."
  echo "   Refusing to start a duplicate. Inspect: ps -o pid,cmd -p $owner"
  exit 1
fi

if sudo -n systemctl restart "$SERVICE" 2>/dev/null; then
  echo "restarted via systemctl"
elif [ -n "$cur" ] && [ "$cur" != "0" ]; then
  echo "no passwordless sudo — signalling MainPID $cur for a clean systemd restart"
  kill -TERM "$cur"
else
  echo "❌ Cannot restart: no passwordless sudo and the unit is not running."
  echo "   Run: sudo systemctl restart $SERVICE"
  exit 1
fi

for _ in $(seq 1 15); do
  sleep 1
  [ "$(systemctl is-active "$SERVICE")" = "active" ] && break
done

new="$(mainpid)"
owner="$(port_owner)"
state="$(systemctl is-active "$SERVICE")"
echo "unit: $state   MainPID: $new   :$PORT owner: ${owner:-none}"
if [ "$state" = "active" ] && [ "$owner" = "$new" ]; then
  echo "✅ $SERVICE is active and owns :$PORT"
else
  echo "⚠️  verification failed (state=$state owner=${owner:-none} MainPID=$new)"
  exit 1
fi
