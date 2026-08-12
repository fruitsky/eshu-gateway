#!/bin/bash
# Eshu Gateway Dashboard — one-shot bootstrap
# Usage: bash bootstrap.sh
# This script sets up a Python venv, installs dependencies, and creates
# a systemd service so the dashboard starts automatically on boot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASH_DIR="$SCRIPT_DIR/dashboard"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       Eshu Gateway Dashboard — Bootstrap            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Check system dependencies ────────────────────────────────────────────
for cmd in python3 systemctl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "❌  Missing required command: $cmd"
    echo "   Install it first, then re-run this script."
    exit 1
  fi
done

# ── Hermes → Eshu Migration ──────────────────────────────────────────────
echo ""
echo "🔍  Checking for legacy Hermes installation..."
HERMES_FOUND=""
if systemctl list-unit-files hermes-dashboard.service 2>/dev/null | grep -q hermes-dashboard; then
  HERMES_FOUND="yes"
  echo "   📋  Found: hermes-dashboard.service"
  if systemctl is-active --quiet hermes-dashboard 2>/dev/null; then
    echo "   ⏹   Stopping hermes-dashboard.service..."
    sudo systemctl stop hermes-dashboard
  fi
  echo "   🚫  Disabling hermes-dashboard.service..."
  sudo systemctl disable hermes-dashboard 2>/dev/null || true
  echo "   📝  Old unit file kept. Delete manually after verifying:"
  echo "       sudo rm /etc/systemd/system/hermes-dashboard.service && sudo systemctl daemon-reload"
  echo ""
fi
if [ -z "$HERMES_FOUND" ]; then
  echo "   ✅  No legacy Hermes installation found."
fi

# ── Detect Python version ─────────────────────────────────────────────────
PY_VER=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
echo "🐍  Python $PY_VER detected"

# ── Create venv ───────────────────────────────────────────────────────────
if [ -d "$DASH_DIR/venv" ]; then
  echo "♻️   Virtual environment already exists — skipping"
else
  echo "📦  Creating Python virtual environment..."
  python3 -m venv "$DASH_DIR/venv"
fi

# ── Install Python dependencies ───────────────────────────────────────────
echo "📦  Installing Python dependencies..."
"$DASH_DIR/venv/bin/pip" install -q -r "$DASH_DIR/requirements.txt"

# ── Port selection ─────────────────────────────────────────────────────────
read -r -p "🔌  Dashboard port [8000]: " PORT
PORT=${PORT:-8000}

# ── Detect user ────────────────────────────────────────────────────────────
SERVICE_USER="${SUDO_USER:-$USER}"

# ── Generate systemd unit ─────────────────────────────────────────────────
UNIT_FILE="/etc/systemd/system/eshu-dashboard.service"

if [ ! -f "$UNIT_FILE" ]; then
  echo "📝  Creating systemd service..."
  sudo tee "$UNIT_FILE" > /dev/null <<EOF
[Unit]
Description=Eshu Gateway Dashboard
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$DASH_DIR
ExecStart=$DASH_DIR/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
else
  echo "♻️   Systemd unit already exists — updating port and paths"
  sudo sed -i "s|^ExecStart=.*|ExecStart=$DASH_DIR/venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port $PORT|" "$UNIT_FILE"
fi

sudo systemctl daemon-reload
sudo systemctl enable eshu-dashboard
sudo systemctl restart eshu-dashboard

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║     ✅  Eshu Dashboard is running!                   ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  URL:      http://$(hostname -I | awk '{print $1}'):$PORT           ║"
echo "║                                                      ║"
echo "║  Commands:                                           ║"
echo "║    sudo systemctl status eshu-dashboard              ║"
echo "║    sudo journalctl -u eshu-dashboard -f              ║"
echo "║    sudo systemctl restart eshu-dashboard             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""