#!/bin/bash
# Full local verification gate: syntax checks + the whole pytest suite.
#
# CI runs the same suite on every push and PR. Run this before merging to
# master or deploying the dashboard host; it is the single command whose
# result should be quoted before a deploy.
#
# Usage: bash scripts/verify.sh [extra pytest args...]
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/pre-commit.sh

if ! python3 -c 'import pytest' >/dev/null 2>&1; then
  echo "❌ pytest is not installed."
  echo "   Install the dev dependencies: pip install -r requirements-dev.txt"
  exit 1
fi

python3 -m pytest tests/ -q "$@"
