#!/bin/bash
# Eshu Gateway Dashboard — Universal Launcher
# Works on Windows (Git Bash), WSL, and Linux.
# Auto-creates venv + installs dependencies on first run.
# For production deployment with systemd, use bootstrap.sh instead.

set -euo pipefail

cd "$(dirname "$0")"

# ── Detect platform and Python path ──────────────────────────────────────

if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]] || [[ "$(uname -s)" == CYGWIN* ]]; then
  # Windows (Git Bash / MSYS2 / Cygwin)
  PYTHON="$(pwd)/venv/Scripts/python"
  PIP="$(pwd)/venv/Scripts/pip"
  SYSTEM_PYTHON="python"
else
  # Linux / WSL / macOS
  PYTHON="$(pwd)/venv/bin/python3"
  PIP="$(pwd)/venv/bin/pip"
  SYSTEM_PYTHON="python3"
fi

# ── Auto-create venv if missing ──────────────────────────────────────────

if [ ! -f "$PYTHON" ] && [ ! -f "$PYTHON.exe" ]; then
  echo ""
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     🐍  First run — setting up Python environment   ║"
  echo "╚══════════════════════════════════════════════════════╝"
  echo ""
  echo "📦  Creating virtual environment..."
  $SYSTEM_PYTHON -m venv venv
  echo "📦  Installing dependencies..."
  "$PIP" install -q -r requirements.txt
  echo "✅  Environment ready."
  echo ""
fi

# ── Banner ───────────────────────────────────────────────────────────────

VERSION=$(grep -oP 'DASHBOARD_VERSION = "\K[^"]+' main.py 2>/dev/null || echo "unknown")
MODIFIED=$(stat -c %y main.py 2>/dev/null | cut -d'.' -f1 || stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" main.py 2>/dev/null || echo "unknown")

echo "╔══════════════════════════════════════════╗"
echo "║     Eshu Gateway Dashboard ${VERSION}          ║"
echo "║     Last modified: ${MODIFIED}   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  🌐  http://localhost:8000"
echo ""

exec "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8000