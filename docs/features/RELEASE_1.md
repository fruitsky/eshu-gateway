# Release 1 — `feature-roadmap` → `master`

Status: **dashboard deployed 2026-11-10 17:14 UTC at `c8ffe2c`; gateways not
yet updated (steps 5–7 are the operator's).**

Done on the operator's go-ahead, 2026-11-10:
* step 1: `master` fast-forwarded `5c0045f..c8ffe2c` (full suite green at the
  F12 merge apart from one unrelated flaky NPM socket test that passed 5/5 on
  rerun; GitHub CI green on `c8ffe2c`);
* step 2: online backup `~/eshu-backups/eshu-pre-release1-20261110-171439.db`
  (`integrity_check` ok, mode 600) and `pre-release1-commit.txt` (`5c0045f`);
  the old installer saved as `golden-installer-5c0045f-pre-release1.sh`;
* step 3: `git checkout -- dashboard/static/dev/`, then the deploy helper (it
  pulled, re-executed itself as the new version and restarted the unit);
* step 4 (automated part): unit active and owning :8000, no traceback, both
  backfills done (1376 commands each), gateway heartbeats and policy fetches
  200, every parameterless GET route 200/401/422 except `/api/ssh-key`, which
  is the expected 503 `ssh_key_dir` until `/var/lib/eshu/ssh` exists (the
  installer treats that as "no dashboard key" and installs as before). MCP
  clients' stale sessions got the normal 404 after the restart. **The browser
  click-through in step 4 is still the operator's.**
* release 1.1 (F10), 18:07 UTC: CI green on `be5e87d`; `master`
  `c8ffe2c..be5e87d`; backup `eshu-pre-release1.1-20261110-180724.db`
  (`integrity_check` ok, mode 600) and `pre-release1.1-commit.txt`
  (`c8ffe2c`); deployed with the new helper (no manual reset needed); unit
  active, no traceback, routes as before; the HA integration has the five
  curated actions plus `ha_check_config` enabled and `ha_host_reboot`
  disabled. Each step needs the operator's
go-ahead. Nothing in this file has been run against the live system except
the read-only checks under "Checked before release".

## What ships

Chunks merged on `feature-roadmap` (§ 9 of `FEATURE_PLAN.md`):

* F1 command breakdown and F2 review card;
* F7 inner target;
* F3 session plans;
* F4 read lane (**shadow only**; `on` stays hard-locked);
* X1 sidebar actions;
* F5 execution results (capture defaults to `exit-only`);
* F18 request context;
* F6 session wrap-up;
* F13 session conclusions and runbooks;
* F8 test traffic;
* F9 HA Supervisor read tools;
* F14 inventory;
* F12 SSH through Eshu over MCP (`ssh_exec`), merged `c0954cc` with the
  security sign-off on the merged gateway diff. Dashboard side only until a
  gateway is reinstalled; see "F12 on the dashboard host" below.

Plus the CI fix (`fix/ci-environment`, merged `f2746ef`), the deploy-helper fix
(`afd5ea6`) and the buffering fix (`fix/stream-unbuffered`: `sanitize` is
line-buffered, so a slow command stops promptly when its caller disconnects;
signed off by the security reviewer).

**Release 1.1, same day:** F10 HA Supervisor actions (add-on restart/update,
backups, Core update, restart after a passing config check; every action
gated, the host reboot disabled by default), merged `5c62898` after the
security sign-off `f9e2c22`. Full suite 2659 passed; on the private live-DB
copy its migration only adds `session_plan_items.args_json` and `summary`, and
the Release 1 code (`c8ffe2c`) still starts on the migrated copy.

Not in this release: F11, F15, F16, F17 and F19.

## Preconditions

**Met on 2026-11-10:**
* 1: the CI fix was signed off by the security reviewer (D1) and merged in
  `f2746ef` (2360 passed). GitHub CI is green on the branch.
* 2: the deploy helper now resets `static/dev/` before it pulls; merged in
  `afd5ea6` (2362 passed).
* 3: full suite green at `afd5ea6`.

The buffering fix (`fix/stream-unbuffered`, B1–B4) is **not** a precondition:
slow commands outliving a disconnect is a known issue that predates this
release, and the security reviewer agreed it does not block (F5 Reviewer
notes, D1).

1. `fix/ci-environment` is approved by the feature reviewer (C1–C5), signed
   off by the security reviewer (gateway bash, § 2.6a), and merged. GitHub CI
   is green on the `feature-roadmap` tip.
2. **The deploy helper resets `dashboard/static/dev/` before it pulls.** The
   running dashboard rewrites `dashboard/static/dev/eshu-gateway-install.sh`,
   so the live checkout shows it as modified. This release changes that file,
   so `git pull --ff-only` refuses to run and the deploy stops (safely, with
   nothing restarted). Today `scripts/deploy-dashboard.sh` runs
   `git checkout -- dashboard/static/` only *after* the pull. Either move it
   before the pull (preferred, a one-line fix with a test), or run it by hand
   in step 3.
3. The full suite passes on the `feature-roadmap` tip: `bash scripts/verify.sh`.

## Checked before release (read-only, 2026-11-09)

Run against a private, local copy of the live DB (a `sqlite3 -readonly .dump`,
kept in the reviewer's scratchpad and never committed):

* **Merge:** `master` is an ancestor of `feature-roadmap`, so the release is a
  fast-forward with no conflicts.
* **Migrations:** `init_db()` on the copy took 0.05 s, is idempotent, drops no
  table and changes no row count. It adds the F3, F5, F13 and F14 tables, the
  `gateways` columns (`capture_output`, `read_lane`, `read_lane_since`) and the
  `requests` columns (`inner_target`, `command_sha`, `shape_sha`,
  `decided_at`, `excluded`). `integrity_check` is `ok`.
* **Startup backfills** (background threads): inner targets 10.6 s,
  fingerprints 10.7 s, both filling every row. Both are skipped on the next
  start.
* **New read paths on real data:** F18 `build_context` ran for every request,
  with 0 errors at about 5 ms each. The F4 shadow check ran over every
  distinct command with 0 errors; 43 of 1376 would be tagged read-lane
  (shadow only).
* **Rollback:** the current `master` code starts on the *migrated* DB and its
  reads work. Every migration is additive, so reverting the code is a valid
  rollback without restoring the DB.
* **Compatibility:** no API route is removed, and the ticket format is still
  `ts|cmd`. A new gateway's `/api/exec-result` post to an old dashboard gets
  a 404 and is ignored (it is posted in the background). The dashboard and
  gateways can therefore be updated in either order.
* **Live gateways:** all 16 are on capture `exit-only`, the path that never
  used the F5 FIFO.
* **Host:** SQLite 3.45.1 with FTS5 (needed by F13), Python 3.12. No
  requirement or service-file changes.

## Local smoke test (synthetic data, 2026-11-09)

The `feature-roadmap` dashboard was run locally on a fresh DB, with three
TEST-NET gateways (`192.0.2.10–12`) and synthetic requests sent through
`/api/request` with gateway tokens:

* Every parameterless `GET /api/*` route returns 200 as the operator, except five
  that need a token or a query parameter and correctly return 401/422.
* Gateway request → sidebar context (session, seen, like, waiting reason) →
  breakdown → decoded → approve → gateway status → ticket (`ts|cmd`) →
  `exec-result` → stored output: all 200, with the expected bodies.
* Deny works. Approving an already-denied request is refused with
  `409 not_pending`.
* Session detail and the Docker inner-target list work.

**Not covered: a real browser.** Headless Chrome on the reviewer's machine is
missing system libraries (`libnspr4`), so no click-through or JS-console check
was run. Step 4 below is that check, done by hand.

## Steps (each on the operator's go-ahead)

1. **Merge.** `git checkout master && git merge --ff-only feature-roadmap &&
   git push origin master`.
2. **Back up the live DB** on the dashboard host, with SQLite's online backup
   (safe while the dashboard runs):
   `python3 -c "import sqlite3; s=sqlite3.connect('dashboard/eshu.db'); d=sqlite3.connect('$HOME/eshu-backups/eshu-pre-release1-$(date +%Y%m%d-%H%M%S).db'); s.backup(d); d.close()"`
   Then record the deployed commit: `git rev-parse HEAD > ~/eshu-backups/pre-release1-commit.txt`.
3. **Deploy the dashboard.** `bash scripts/deploy-dashboard.sh`. If the helper
   is not fixed yet (precondition 2), run `git checkout -- dashboard/static/dev/`
   first.
4. **Check the dashboard.** Within 1 minute: the unit is active, the log has no
   traceback, and the log shows the two backfill threads finishing. Open the
   dashboard and check the pending list, one request's sidebar (context
   sections), the session view, Inventory, and a confirmed conclusion inside a session's view (there is no
   separate runbook list yet; that is X2). Run one
   approved JIT command through any gateway and confirm it still works on the
   *old* gateway script.
5. **Update one gateway** (a low-traffic one) by re-running its installer.
   Check: an allowlisted command, a JIT command (approve it), a blocked
   command, and a freeze. Leave capture at `exit-only`.
6. **Optional, on that gateway only:** set capture to `tail`, run a command
   with large output (for example `journalctl -n 20000`), and confirm the
   result appears in the request's sidebar and the call returns promptly. Then
   set it back, or keep it if wanted. **On a host whose `awk` is `mawk`**
   (`awk -W version`): `tail` capture puts `mawk` in the output stream, and it
   reads stdin in 4 KiB blocks, so a *slow* command can outlive an agent
   disconnect until 4 KiB accumulate (measured 10.7 s). `exit-only` has no
   `awk` in the path and is not affected. This is the accepted residual of the
   buffering fix, not a regression.
7. **Update the remaining gateways**, a few at a time, with the step 5 check
   on each batch.

## F12 on the dashboard host

Re-checked 2026-11-10 with F12 merged: `init_db()` on the private live-DB copy
is unchanged (0.05 s, no table dropped, no row count changed, `integrity_check`
ok), and the current `master` code still starts on the migrated copy.

`ssh_exec` keeps its service key in `/var/lib/eshu/ssh` (or
`ESHU_SSH_KEY_DIR`), which must be `0700` and owned by the service user. The
dashboard does not create `/var/lib/eshu` itself (it is root-owned), so until
the operator creates it, every `ssh_exec` call is refused cleanly with
`ssh_key_dir`; nothing else is affected. To try F12:
`sudo install -d -o dragon -g dragon -m 700 /var/lib/eshu /var/lib/eshu/ssh`,
then reinstall one gateway so it enrols the dashboard key.

## Rollback

* **Dashboard:** `git checkout <commit from pre-release1-commit.txt>`, then
  `bash scripts/deploy-dashboard.sh --no-pull`. Restoring the DB is not needed
  (the migrations are additive). Restore the step 2 backup only if data was
  damaged.
* **Gateway:** re-run the previous installer (`~/eshu-backups/golden-installer-*.sh`
  holds a known-good one), or install from the rolled-back dashboard.
