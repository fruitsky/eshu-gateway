#!/bin/bash
# Pre-commit syntax checks — run before every commit.
# Usage: bash scripts/pre-commit.sh
#
# These checks catch what causes real outages in this project:
#   * bash -n on standalone scripts (gateway.sh, poller.sh, logger.sh)
#   * bash -n on generated installers (if the template has issues)
#   * python -m py_compile on all .py files
#   * node --check on all .js files

set -euo pipefail
cd "$(dirname "$0")/.."
errors=0

check() {
  local label="$1"; shift
  echo -n "  $label... "
  if "$@" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAIL"
    errors=$((errors+1))
  fi
}

# If node isn't available, skip JS checks gracefully
if command -v node &>/dev/null || command -v node.exe &>/dev/null; then
  NODE_CMD=$(command -v node 2>/dev/null || command -v node.exe 2>/dev/null)
  check "app.js" "$NODE_CMD" --check dashboard/static/app.js
else
  echo "  app.js... SKIP (node not found)"
fi

check "gateway.sh"     bash -n dashboard/eshu-gateway.sh
check "poller.sh"      bash -n dashboard/eshu-poller.sh
check "logger.sh"      bash -n dashboard/eshu-logger.sh
check "fleet-runner"   bash -n dashboard/static/fleet-runner.sh
check "features"      bash -n dashboard/static/features/*.sh
check "template"       bash -n dashboard/eshu-installer-template.sh
check "prod-install"   bash -n dashboard/eshu-gateway-install.sh
check "dev-install"    bash -n dashboard/static/dev/eshu-gateway-install.sh
check "main.py"        python3 -m py_compile dashboard/main.py
check "database.py"    python3 -m py_compile dashboard/database.py

echo ""
if [ "$errors" -eq 0 ]; then
  echo "✅ All checks passed"
else
  echo "❌ $errors check(s) failed — fix before committing"
  exit 1
fi
