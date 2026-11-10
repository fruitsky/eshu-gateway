# Release 1 — `feature-roadmap` → `master`

Status: **preconditions met (2026-11-10); release not started.** Each step needs the operator's
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
* F14 inventory.

Plus the CI fix (`fix/ci-environment`), once it is approved and merged (see
"Preconditions").

Not in this release: F10, F11, F12, F15, F16, F17 and F19.

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
   sections), the session view, Inventory and the runbook list. Run one
   approved JIT command through any gateway and confirm it still works on the
   *old* gateway script.
5. **Update one gateway** (a low-traffic one) by re-running its installer.
   Check: an allowlisted command, a JIT command (approve it), a blocked
   command, and a freeze. Leave capture at `exit-only`.
6. **Optional, on that gateway only:** set capture to `tail`, run a command
   with large output (for example `journalctl -n 20000`), and confirm the
   result appears in the request's sidebar and the call returns promptly. Then
   set it back, or keep it if wanted.
   Known limit until a later fix (buffering S1 in the F5 notes): with `tail`
   capture on a mawk host (the Debian/Ubuntu default), a slow command can keep
   running for a while after the caller disconnects. `exit-only` is not
   affected.
7. **Update the remaining gateways**, a few at a time, with the step 5 check
   on each batch.

## Rollback

* **Dashboard:** `git checkout <commit from pre-release1-commit.txt>`, then
  `bash scripts/deploy-dashboard.sh --no-pull`. Restoring the DB is not needed
  (the migrations are additive). Restore the step 2 backup only if data was
  damaged.
* **Gateway:** re-run the previous installer (`~/eshu-backups/golden-installer-*.sh`
  holds a known-good one), or install from the rolled-back dashboard.
