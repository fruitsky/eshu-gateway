# Eshu Gateway — Developer Guide

## Feature flags and delivery

Features are delivered to gateways through a **flag → script → sync** pipeline that decouples feature activation from core script updates. The gateway script never changes when features are toggled — it loads feature scripts from disk at runtime.

> **Note:** Approved Windows used to be the example feature flag. It is now a
> **core / always-on** feature — `fetch_policy` delivers the active windows by
> data (no flag), and the poller syncs `approved_windows.sh` when window data is
> present. The feature-flag system below remains for *new* features.

### Flow

```
  Dashboard                     Gateway (poll cycle ~2s)
  ─────────                     ─────────────────────────
  1. Operator toggles flag    ──▶ 2. Poller fetches /api/policy/{ip}
     in Settings                     └─ Policy includes <feature> data
                                       └─ data + feature_scripts list
                                    3. Poller writes <feature> cache
                                    4. Poller downloads feature scripts
                                       to /etc/eshu/features.d/*.sh
                                    5. Gateway sources features.d
                                       at Stage 4.5 on every SSH
```

### Adding a new feature

1. **Create a feature script** in `dashboard/static/features/my_feature.sh`
   - Must be valid bash (check with `bash -n`)
   - Runs inline at Stage 4.5 — has access to `$cmd`, `$ESHU_WINDOW_TOKEN`,
     `$DASHBOARD_URL`, `$TARGET_IP`, `$GATEWAY_TOKEN`, and helpers
     `log_window_reject`, `log_auto_approve`, `run_sanitized`
   - No shebang needed (sourced, not executed)

2. **Add a flag row** in `dashboard/db/misc.py`:
   ```python
   cursor.execute("INSERT OR IGNORE INTO feature_flags "
       "(flag_name, enabled, description, scope) "
       "VALUES ('my_feature', 0, 'Description', 'dev')")
   ```

3. **Wire the flag in policy** (`dashboard/main.py`, `fetch_policy`):
   - If the feature needs dev-only testing: `if gw_mode == 'dev' and flag:`
   - If the feature is ready for prod: just `if flag:`
   - Add the feature data to `policies` dict so the poller syncs it

4. **Update the poller** (`dashboard/eshu-poller.sh`, policy-sync section):
   - Add logic to download the feature script when the flag is on
   - Must use atomic tmpfile + `bash -n` validation before deploying:
     ```bash
     curl -s -o "$FEATURES_DIR/my_feature.sh.tmp" "$DASHBOARD_URL/static/features/my_feature.sh"
     if [ -s "$FEATURES_DIR/my_feature.sh.tmp" ] && bash -n "$FEATURES_DIR/my_feature.sh.tmp"; then
       mv "$FEATURES_DIR/my_feature.sh.tmp" "$FEATURES_DIR/my_feature.sh"
     else
       logger -t eshu-poller "Feature script validation failed"
       rm -f "$FEATURES_DIR/my_feature.sh.tmp"
     fi
     ```

5. **Regenerate installers**: `python3 dashboard/gen_installer.py`

### Rolling to production

1. Test thoroughly on dev mode gateways
2. In `main.py`, remove the `gw_mode == 'dev'` guard from the policy condition
   (the feature becomes available to all gateways when the flag is on)
3. Update the flag's scope in `db/misc.py` from `'dev'` to `'prod'`
4. Deploy via the Development & Deployment pipeline: Seed Edge → Push to Dev
   Gateways → verify → Deploy to Fleet (pushes new poller + core scripts)
5. Subsequently: just flip the flag in Settings — no update needed

### Pre-commit hook

Run `bash scripts/setup-git-hooks.sh` once. After that, `git commit` automatically runs:
- `bash -n` on all shell scripts
- `python3 -m py_compile` on Python files
- `node --check` on JS files

### Pulling latest changes on the dashboard host

The generated installers (`dashboard/static/eshu-gateway-install.sh` and
`dashboard/static/dev/eshu-gateway-install.sh`) are git-tracked, and the
deploy pipeline (Seed Edge / Deploy to Fleet) writes to them at runtime. That
dirties the working tree, so reset them before pulling — otherwise `git pull`
refuses to overwrite the runtime-modified files:

```bash
git checkout -- dashboard/static/ && git pull && sudo systemctl restart eshu-dashboard
```

This is safe: the deploy pipeline regenerates those installers from the Build
at runtime, so discarding local changes never loses anything.

### Installer privilege checks

The installer (`eshu-installer-template.sh`, regenerated via
`python3 gen_installer.py` → commit all three generated installers) requires
**root + systemd** and gives non-root users actionable guidance instead of a
terse "run as root":

- Root → proceeds (TrueNAS SCALE console is root with no `sudo` — run the
  one-liner directly).
- Non-root + `sudo` → prints "re-run the one-liner prefixed with sudo".
- Non-root, no `sudo` → platform-aware message (TrueNAS vs HA OS / rootless).
- No `systemctl` → clear "requires systemd" message.

All probes are `if`/`command -v` guarded so `set -euo pipefail` never aborts on
a non-root environment.

### Token self-heal

The poller self-heals a missing `GATEWAY_TOKEN` by re-registering with the
dashboard. It is **cooldown-gated (60s)** rather than once-per-boot so a
gateway that loses its token (e.g. re-enrolled on an un-rebooted host) can
always recover — the old once-per-boot marker (`/var/run/eshu.self_heal_done`)
was never cleared on reinstall, leaving such gateways permanently token-less.
The installer clears the guard files (`self_heal_done`, `self_heal_ts`) on
install/reinstall, and the uninstaller removes them too. The poller change
ships to gateways via the deploy pipeline (Seed Edge → Push to Dev → Deploy to
Fleet).

### Running tests

Full test suite (289 tests, ~35s):

```bash
# Install runtime + test dependencies (fresh clone):
pip install -r requirements-dev.txt

bash tests/run.sh
# Or directly:
python3 -m pytest tests/ -v
```

`requirements-dev.txt` (at the repo root) pulls in the runtime deps plus
`pytest` + `httpx` — the test-suite dependencies. Tests use an ephemeral SQLite
database — no production data is touched. Run this before deploying changes to
the dashboard host or merging to master.

### What each test file checks

| File | Tests | What it checks, in plain English |
|------|-------|----------------------------------|
| `test_api.py` | 30 | Full JIT flow over HTTP, auth, policies, windows, public + gated endpoints |
| `test_auth.py` | 7 | Set/change the dashboard password, password is mandatory/non-removable, logout clears the session |
| `test_cmd_risk.py` | 17 | "What could go wrong" risk hints + dry-run suggestions |
| `test_db_audit.py` | 4 | Record events and search them later, newest-first |
| `test_db_concurrency.py` | 2 | Concurrent DB writes never lock (regression for the "database is locked" outage) |
| `test_db_gateways.py` | 10 | Register gateways, preserve API tokens, look up by token, deregister |
| `test_db_requests.py` | 18 | Create, approve, and claim JIT requests; poller sweep |
| `test_db_windows.py` | 11 | Unique window tokens, execution counter, expired windows rejected |
| `test_enrollment.py` | 11 | Enrollment tokens, install-script generation, single-key flow |
| `test_fleet.py` | 36 | Fleet Run compose/dispatch/results, gateways-by-token, fleet delivery |
| `test_freeze.py` | 12 | Emergency Freeze trigger, poller rejects while frozen, unfreeze |
| `test_golden.py` | 26 | Build → Edge → Fleet pipeline: seed/promote/rollback, hashes, dev-gateways auth |
| `test_installer.py` | 5 | Installer template: scoped token sed, no approver key, arg shift |
| `test_learning.py` | 13 | Background gap scanner finds repeated JIT approvals, new/seen states |
| `test_override.py` | 18 | Override Mode auto-approves JIT, Zero-Trust exclusivity, audit events |
| `test_policies.py` | 11 | Save/commit policies, `policies/test` action + FATAL tier, `policies/check` gated |
| `test_policy_rollback.py` | 6 | Roll a policy back to a prior version from its change history |
| `test_settings.py` | 16 | Dev-tools toggle, notify/webhook config, Discord format/UA, feature-flags gated |
| `test_stats.py` | 10 | Extended statistics: heatmaps, automation trend, window summaries, gateway health |
| `test_uninstall.py` | 14 | Trigger uninstall, cleanup DB, gateway self-deregistration |
| `test_window_read.py` | 12 | Approved-window read endpoints, opaque retrieval keys, atomic consumption |

Known/parked issues are tracked in [`docs/KNOWN_ISSUES.md`](KNOWN_ISSUES.md).
Planned features and their priority are in [`docs/FEATURE_ROADMAP.md`](FEATURE_ROADMAP.md).


### File structure

```
dashboard/eshu-gateway.sh          # core gateway script — sources features.d
dashboard/eshu-poller.sh           # poller — syncs features.d + windows cache
dashboard/eshu-logger.sh           # health heartbeat — independent
dashboard/eshu-installer-template.sh  # installer template with markers
dashboard/gen_installer.py         # generates self-contained installers
dashboard/main.py                  # FastAPI app — all HTTP routes
dashboard/database.py             # re-exports the db/ modules (single import surface)
dashboard/core/                    # shared logic: session/auth, notify, risk, cmd_blocklist, utils
dashboard/db/                      # data layer: one module per domain (requests, gateways, windows, fleet, ...)
dashboard/static/                  # dashboard UI (index.html, app.js, style.css)
dashboard/static/features/         # feature script files (served to gateways)
scripts/pre-commit.sh              # syntax checks
scripts/setup-git-hooks.sh         # one-time hook installer
```

### Development workflow

```bash
# Edit source files
vim dashboard/eshu-gateway.sh
python3 dashboard/gen_installer.py  # regenerate installers
bash scripts/pre-commit.sh          # check syntax
git add -A && git commit -m "..."   # hook runs automatically
git push
```

### Key principles

- Core scripts change rarely (infrastructure updates only)
- Feature delivery is a flag toggle + file sync — no script change needed
- All feature scripts must pass `bash -n` before the poller deploys them
- The operator always controls the flag; scope metadata is for documentation
