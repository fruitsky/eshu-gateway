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
  # A pull can replace this very file. Bash reads scripts incrementally, so the
  # remaining lines would be read from shifted offsets (which silently ran the
  # *old* verification block after an update). Re-exec when the helper changed.
  before="$(sha1sum "$0" 2>/dev/null | cut -d' ' -f1 || true)"
  git pull --ff-only
  git checkout -- dashboard/static/ 2>/dev/null || true
  after="$(sha1sum "$0" 2>/dev/null | cut -d' ' -f1 || true)"
  if [ -n "$before" ] && [ "$before" != "$after" ]; then
    echo "== deploy helper updated — re-executing the new version =="
    exec bash "$0" --no-pull
  fi
fi

mainpid() { systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0; }
port_owner() {
  # ss is the fast path; lsof covers hosts where ss cannot read socket owners.
  local pid
  pid="$(ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $NF}' | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
  if [ -z "$pid" ] && command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  fi
  printf '%s' "$pid"
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

# Wait for the *new* process to own the port, not just for the unit to say
# "active": with Restart=always, systemd reports active again before uvicorn
# has bound the socket, so a single check ~1s in reported owner=none on every
# deploy even though the service came up fine a moment later.
state="$(systemctl is-active "$SERVICE" || true)"
new="$(mainpid)"
owner="$(port_owner)"
for _ in $(seq 1 20); do
  sleep 1
  state="$(systemctl is-active "$SERVICE" || true)"
  new="$(mainpid)"
  owner="$(port_owner)"
  # An `if`, not `[ … ] && break`: under `set -e` a short-circuiting AND-list is
  # the loop body's last command, so the loop exits on the first "not yet"
  # instead of retrying.
  if [ "$state" = "active" ] && [ -n "$owner" ] && [ "$owner" != "0" ] && [ "$owner" = "$new" ]; then
    break
  fi
done

echo "unit: $state   MainPID: $new   :$PORT owner: ${owner:-none}"
if [ "$state" = "active" ] && [ "$owner" = "$new" ]; then
  echo "✅ $SERVICE is active and owns :$PORT"
else
  echo "⚠️  verification failed (state=$state owner=${owner:-none} MainPID=$new)"
  exit 1
fi
