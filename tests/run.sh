#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Use existing venv or create one
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif python3 -m venv .venv 2>/dev/null; then
    source .venv/bin/activate
else
    # Fall back to --break-system-packages (Ubuntu/Debian restrictions)
    pip install --break-system-packages -q -r requirements-test.txt ../dashboard/requirements.txt
    exec pytest -v "$@"
fi

pip install -q -r requirements-test.txt ../dashboard/requirements.txt
pytest -v "$@"
