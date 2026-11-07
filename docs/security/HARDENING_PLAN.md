# Security Hardening Plan

Status: **active** · Branch: `security-hardening` · Baseline: `master@625995d`, **1162 tests passing** (`bash scripts/verify.sh`)

This plan turns the security review of 2026-11-06 into small, reviewable chunks.
One agent implements a chunk; a second agent reviews it against the acceptance
criteria below before it is merged. **Nothing reaches the dashboard LXC or any
gateway until the rollout gate at the end of this document is passed.**

---

## 0. Ground rules (read before every chunk)

### 0.1 Branching and workflow

1. `git fetch origin && git switch security-hardening && git pull --ff-only`
2. Create the chunk branch **from `security-hardening`**:
   `git switch -c sec/chunk-N-<slug>` (the slug is given in each chunk heading).
3. Implement **only that chunk**. If you find something out of scope, write it
   under "Implementer notes" at the bottom of the chunk — do not fix it.
4. Run the full gate: `bash scripts/verify.sh` → must be green, and the test
   count must be **≥ the previous count + the new tests you added**.
5. Commit with conventional messages, e.g. `fix(gateway): validate ESHU_* prefix values`.
   Small, logical commits are preferred over one big commit.
6. `git push -u origin sec/chunk-N-<slug>`. Do **not** merge it yourself, do
   **not** push to `master` or to `security-hardening`.
7. Fill in "Implementer notes" for the chunk (what you did, anything surprising,
   anything you deliberately did not do) in a final commit on your branch.

The reviewer merges approved chunks into `security-hardening` and updates the
status table (§ 9).

### 0.2 Source-of-truth files — never hand-edit generated copies

| Edit this | Never edit by hand |
|---|---|
| `dashboard/eshu-gateway.sh` | `dashboard/eshu-gateway-install.sh` |
| `dashboard/eshu-poller.sh` | `dashboard/static/eshu-gateway-install.sh` |
| `dashboard/eshu-logger.sh` | `dashboard/static/dev/eshu-gateway-install.sh` (managed by the Edge pipeline) |
| `dashboard/eshu-installer-template.sh` | |

After changing any of the left-hand files, regenerate:

```bash
cd dashboard && python3 gen_installer.py && cd ..
git diff --stat   # both generated installers must change identically
```

Commit the regenerated `dashboard/eshu-gateway-install.sh` and
`dashboard/static/eshu-gateway-install.sh` **in the same commit** as the source
change. Do not touch `dashboard/static/dev/`.

`dashboard/static/features/approved_windows.sh` and
`dashboard/static/fleet-runner.sh` are served directly — edit them in place.

### 0.3 Compatibility rule (critical for rollout)

The dashboard is deployed **first**; gateways update later, one by one, via
Build → Edge → Fleet. Therefore:

* **New dashboard ↔ old gateway must keep working.** Every request an
  un-updated gateway sends today must still be accepted (unless it is the
  attack itself). Every response field an old gateway reads must keep its shape.
* **New gateway ↔ new dashboard** must work. (New gateway ↔ old dashboard does
  not need to work; it will not happen.)
* Each chunk states its compat impact. If you think a change breaks this rule,
  stop and write it in the implementer notes.

### 0.4 Testing rules

* Tests must stay hermetic: **no root, no network, no real `/etc`, no real
  `/tmp` paths outside pytest's `tmp_path`.** Follow the existing harnesses:
  * `tests/test_gateway_script.py` — `GatewaySandbox` copies the real
    `eshu-gateway.sh`, relocates paths, stubs `curl`/`logger`. Extend the
    stub `curl` instead of writing a new harness.
  * `tests/test_poller.py` — same pattern for the poller.
  * `tests/conftest.py` — `client` / `auth_client` FastAPI fixtures.
* Every security fix gets at least one **regression test that reproduces the
  attack** and fails on `master`. Name them `test_sec_<chunk>_<what>`.
  Before committing the fix, confirm the test fails without it (mention this
  in the implementer notes).
* Do not weaken, skip or delete existing tests. If an existing test asserts the
  vulnerable behaviour, change it and explain why in the commit message.
* `bash scripts/pre-commit.sh` must pass (`bash -n` on every script, `py_compile`,
  `node --check app.js`).

### 0.5 Style

* Bash: keep `set -eo pipefail` semantics, quote every expansion, no new
  external dependencies beyond what gateways already need (`bash`, `curl`,
  `python3`, `coreutils`, `systemd`). Gateways include a Raspberry Pi 1 —
  avoid adding `python3` starts on the hot path where a pure-bash check works.
* Python: match the surrounding code; no new third-party packages unless the
  chunk says so.
* No drive-by refactors, renames or reformatting.

---

## Chunk 1 — Gateway metadata injection & ticket binding  · slug `json-injection` · **CRITICAL**

### Threat

`eshu-gateway.sh` builds JSON bodies by string concatenation (lines 44, 54,
289). `ESHU_EXECUTION_ID`, `ESHU_SESSION_ID` and `ESHU_WINDOW_TOKEN` come from
the agent's SSH command prefix (parsed at lines 75–83) and are inserted raw.
A crafted value can therefore add or override fields in the body the
dashboard receives. Two consequences:

1. **Unapproved execution.** `/api/log` (`main.py:637`) stores whatever
   `status` it is given, and `update_ticket_consumed_by_ip`
   (`db/requests.py:40`) turns an `approved` row into a ticket — so the body of
   a log call must never be able to carry a status or command the gateway did
   not intend.
2. **Approval mismatch.** The command the operator approves (from the
   `/api/request` body) is not compared with the command the gateway finally
   runs (`eshu-gateway.sh:337-374`).

The reviewer has a working reproduction; the tests below must cover both.

### Changes

**1a. Gateway: validate prefix metadata** (`dashboard/eshu-gateway.sh`, right
after the prefix-parsing loop, before freeze)

* Allowed formats:
  * `ESHU_SESSION_ID`: `^[A-Za-z0-9_-]{1,64}$` (matches `core/mcp_server.py:620`)
  * `ESHU_SESSION_SIG`: `^[0-9a-f]{64}$`
  * `ESHU_EXECUTION_ID`: `^[A-Za-z0-9_.:-]{1,64}$`
  * `ESHU_WINDOW_TOKEN`: `^[A-Za-z0-9]{1,64}$`
* Implement with bash `[[ =~ ]]` (no python start). A value that is set but
  invalid → print
  `[LOCKED] Invalid ESHU_<NAME> prefix value — allowed: <charset>. [Gateway $GATEWAY_VERSION]`,
  `logger` a line **without echoing the raw value** (log its length only), and `exit 1`.
  Do not call the dashboard with the bad value.
* Keep the existing behaviour for absent values.

**1b. Gateway: make every JSON body safe**

* With 1a in place every interpolated field is from a safe charset, except
  `$reason` in `log_window_reject` (internal literal strings) and `$enc_cmd`
  (base64). Add a short comment above each `-d` body stating why the
  concatenation is safe and that new fields must be validated or JSON-escaped.
* Additionally, guard `$TARGET_IP` (installer-baked) the same way:
  `^[0-9A-Fa-f:.]{1,64}$` checked once at startup; refuse to run if invalid.

**1c. Gateway: bind execution to the approved command** (stage 6, the
`approved` branch, and the `consumed` branch)

* After a successful `/api/ticket/<id>` claim, extract the ticket string
  (`"<created_at>|<command>"`, see `db/requests.py:148-158`), split on the first
  `|`, and execute **only if** the command part is byte-for-byte equal to `$cmd`.
  Use one `python3 -c` to parse the JSON (this path already runs python).
* On mismatch: `[LOCKED] Approved command does not match the requested command — refusing to run.`,
  log it, `exit 1`. Never fall through to execution.
* `consumed` branch: the poller already deposited the ticket in the lockbox;
  only execute if a lockbox line `ts|$cmd` (exact match, not expired) exists,
  and burn it — reuse the existing stage-5 logic rather than duplicating it.
  If none is found, behave like a timeout.

**1d. Dashboard: lock down `/api/log`** (`dashboard/main.py:637`)

* Accept only `status ∈ {"auto-approved", "blocked", "frozen", "session-required", "window-rejected"}`
  (these are exactly the statuses current gateways send — verify with
  `grep -rn 'log_auto_approve\|"status":' dashboard/*.sh dashboard/static`).
  Anything else → HTTP 400, nothing stored. In particular `approved`,
  `pending`, `consumed`, `fleet-run`, `window-approved` must be rejected.
* Cap `reason` at 200 chars and `token` at 64.

**1e. Dashboard: validate metadata server-side** (`/api/request`, `/api/log`)

* Apply the same session-id / execution-id regexes from 1a. Invalid → HTTP 400.
  Put the regexes in one place (e.g. `core/utils.py`) and reuse them.
* Compat: current gateways only forward what the agent sent; a legitimate agent
  already uses valid ids. ~~Old gateways sending an injected body now get 400.~~
  **Correction (reviewer, round 1):** this is not sufficient on its own — see
  1f in "Reviewer round 1" below.

### Tests to add

* `tests/test_gateway_script.py`
  * Hostile `ESHU_EXECUTION_ID`, `ESHU_SESSION_ID`, `ESHU_WINDOW_TOKEN` values
    containing `"`, `,`, `{`, `\`, including a value that tries to add extra
    JSON keys to the body → command refused, exit 1, **no** curl call recorded.
  * For a set of valid prefixes, every recorded `-d` body parses with
    `json.loads` and has **exactly** the expected key set (no extra keys).
  * Ticket mismatch: extend the stub's `claim-mode` with a `mismatch` mode
    returning `{"ticket":"123|some other command"}` → not executed, exit 1.
  * Ticket match: stub returns `{"ticket":"123|<the cmd>"}` → executed.
    (Update the existing `test-ticket` stub default to the `ts|cmd` format so
    existing JIT tests keep passing with the new check.)
  * `consumed` with no lockbox entry → not executed.
* `tests/test_api.py` (or a new `tests/test_sec_log_status.py`)
  * `POST /api/log` with each allowed status → 200.
  * With `approved` / `pending` / `consumed` / `window-approved` → 400 and no row.
  * End-to-end regression: gateway-token client posts `/api/log` with
    `status="approved"`, then `GET /api/poll/<ip>` → `ticket` is `null`.
  * `/api/request` with an execution id containing `"` → 400.

### Compat impact

Dashboard part (1d, 1e) is safe for old gateways and **closes attack 1 fleet-wide
as soon as the dashboard is deployed**. 1a–1c need the gateway update to close
attack 2.

### Acceptance criteria

- [ ] Both attacks reproduced as failing tests on `master`, passing on the branch.
- [ ] `bash scripts/verify.sh` green; generated installers regenerated and identical.
- [ ] No new `python3` start on the allowlist fast path (stages 0–4).
- [ ] Existing gateway tests unchanged in intent.

### Implementer notes

Implemented on `sec/chunk-1-json-injection`, cut from `security-hardening@b6445a8`.

**What changed**

- **1a — gateway prefix validation** (`dashboard/eshu-gateway.sh`): a new
  `validate_prefix` helper runs immediately after the prefix-parsing loop and
  before freeze/blocklist handling. It enforces the four charsets, prints
  `[LOCKED] Invalid ESHU_<NAME> prefix value — allowed: <charset>.`, logs the
  name and length only (never the raw value in that line) and exits 1 before
  any dashboard call. Absent values keep their old behaviour.
- **1b — JSON bodies**: added a "why this concatenation is safe" comment above
  each `-d` body, plus a startup `TARGET_IP` guard
  (`^[0-9A-Fa-f:.]{1,64}$`) that refuses to run before any dashboard call.
- **1c — ticket binding**: after a successful `/api/ticket/<id>` claim, one
  `python3 -c` parses the JSON, splits `"<created_at>|<command>"` on the first
  `|`, and executes only on a byte-exact command match; a mismatch logs
  `JIT COMMAND MISMATCH` and exits 1. A malformed ticket is treated as a failed
  claim (retry, never execute). Stage 5 is now a `claim_lockbox_ticket` helper;
  the `consumed` branch reuses it and times out (exit 1) when no live `ts|cmd`
  line exists, so it also cannot execute without a deposited ticket.
- **1d — `/api/log`**: only `auto-approved`, `blocked`, `frozen`,
  `session-required`, `window-rejected` are accepted; anything else is 400
  before `create_request` (so an injected `approved` can never be turned into a
  ticket by `update_ticket_consumed_by_ip`). `reason` is capped at 200 chars and
  `token` at 64 (truncated, so an old gateway with a long reason still logs;
  charset validation is the gateway's job).
- **1e — server-side metadata**: `core/utils.py` now owns the four charset
  patterns and `valid_gateway_metadata()`, used by `/api/request` and
  `/api/log`; a set id outside the charset is 400. Empty ids remain valid.

**Test-first evidence**

- The regressions were written and run before the fix. Against the unmodified
  sources, **24 of them failed** — 11 in `tests/test_gateway_script.py`
  (hostile prefixes adding JSON keys, JSON metacharacters in ids, ticket
  mismatch, `consumed` with no lockbox entry, invalid `TARGET_IP`) and 13 in the
  new `tests/test_sec_log_status.py` (rejected log statuses, the
  approved-status → pollable-ticket attack, invalid metadata, reason cap). All
  pass after the fix.
- `bash scripts/verify.sh` → **1196 passed** (baseline 1162, **+34**: 15 gateway
  tests, 19 API tests). `bash scripts/pre-commit.sh` green. Installers
  regenerated with `cd dashboard && python3 gen_installer.py`; the two generated
  files are identical. `dashboard/static/dev/` was not edited (the golden tests
  rewrite it as a side effect; it was restored to HEAD before committing).

**Existing tests changed (intent preserved)**

- `test_jit_consumed_runs_without_a_second_claim` asserted the vulnerable
  behaviour (a `consumed` status ran with no ticket). Renamed to
  `test_jit_consumed_claims_the_lockbox_ticket` and given a live lockbox line;
  the no-ticket case is now
  `test_sec_chunk1_consumed_without_a_lockbox_ticket_is_refused`.
- Three strict-session tests used short placeholder signatures
  (`deadbeef`/`whatever`). Under 1a those are malformed, so they now use
  64-hex wrong/placeholder values; the tests still cover the invalid-signature
  and fail-open-before-first-key paths.
- The `curl` stub now mirrors the real dashboard ticket format
  (`123|<the requested cmd>`, compact JSON) and has a `mismatch` mode plus a
  `raw_bodies()` helper; `GatewaySandbox` accepts a `target_ip` override so the
  `TARGET_IP` guard can be tested.

**Compatibility / deliberate non-changes**

- Dashboard 1d/1e accept every request an un-updated gateway sends today
  (allowed statuses, empty metadata) and close attack 1 fleet-wide on deploy;
  1a–1c need the gateway update for attack 2.
- `$reason` in `log_window_reject` stays unvalidated because it is only ever an
  internal literal from a feature script; the new comment says so and says any
  future external reason must be validated/escaped.
- No new `python3` start on the allowlist fast path (stages 0–4): prefix and
  `TARGET_IP` checks are pure bash. The only added `python3` is on the JIT
  ticket claim, which already ran python.
- Not done (out of Chunk 1 scope): the §8 compat suite
  (`tests/test_sec_compat.py`) that runs the **old** gateway script against the
  new dashboard. The sandbox reads the live script, so this needs a pinned
  pre-chunk copy and is left as a rollout-gate task for the reviewer. Also
  untouched: chunk 2 chaining, `/api/log` body-size limits, and server-side
  `WINDOW_TOKEN_PATTERN` enforcement (the gateway enforces the charset; the
  dashboard only caps the token length).

### Reviewer round 1 — changes requested

Reviewed `4170619`. Gateway changes (1a–1c), `/api/log` lockdown (1d), test
quality and notes are **approved as-is** — do not rework them. Installers are in
sync, pre-commit and the touched test files pass, and the reviewer's private
reproduction of the `/api/log` path is now refused (400, no ticket).

**Gap (planner error, not an implementer error):** 1e cannot detect injection.
An injected value closes its own JSON string before adding keys, so after
parsing *every* field (including `execution_id`) looks valid. Against the new
dashboard, a body built by an **un-updated** gateway that carries a duplicate
`encoded_command` is still accepted by `/api/request` (200) — the operator
approves one command and the old gateway runs another. Until every gateway is
updated, only the dashboard can stop this.

**1f. Reject duplicate JSON keys on gateway-facing endpoints** (dashboard)

* Legitimate gateways never send duplicate keys; a duplicate key is the
  signature of injection. Reject such bodies with **400** before the handler
  runs, store nothing, and `record_audit_event('gateway_body_rejected', ip, details='duplicate JSON keys: <key names>')`
  (key names only — never the values).
* Scope: at minimum `/api/request`, `/api/log`, `/api/register`,
  `/api/gateway-heartbeat`, `/api/fleet/commands/{id}/result`,
  `/api/uninstall-progress`, `/api/window-requests`. Applying it to **all**
  JSON `POST/PUT/PATCH` under `/api/` is acceptable and preferred if the full
  suite stays green (the UI never sends duplicates either). Do not apply it to
  `/mcp`.
* Implementation guidance (FastAPI 0.139 / Starlette 1.3 here): use an async
  dependency (or a custom `APIRoute` subclass) that does
  `json.loads(await request.body(), object_pairs_hook=_no_dupes)` where
  `_no_dupes` raises on a repeated key at **any nesting level**. FastAPI caches
  the body on the request, so the handler still receives it. Avoid
  `@app.middleware("http")` body reads. Non-JSON or empty bodies: leave to
  the existing validation (do not change their status codes).
* Keep 1e — it is still useful against malformed values.

**1g. Compat suite `tests/test_sec_compat.py`** (required now, not at rollout)

* Pin the pre-chunk gateway: `git show 625995d:dashboard/eshu-gateway.sh > tests/fixtures/legacy/eshu-gateway-625995d.sh`
  (commit it; add a header comment saying it is a frozen copy used only for
  compatibility tests and must never be edited or deployed).
* Let `GatewaySandbox` take an optional `script` path (default: the live
  script) so the same harness can run the legacy copy.
* Tests: run the **legacy** script in the sandbox for the normal flows
  (exact-allowlist run, blocklist block, freeze, session-required, window
  reject, JIT request), capture each recorded `-d` body + URL, and replay the
  **raw bytes** (`client.post(url_path, content=raw, headers=…)`, not `json=`)
  against the new dashboard with a gateway token for `10.0.0.1`. Every
  legitimate body → 2xx.
* Same replay with hostile prefixes run through the legacy script (a value that
  adds an `encoded_command`, and one that adds a `status`) → **400** and no new
  `requests` row, and `GET /api/poll/10.0.0.1` → `ticket` is `null`.
* Mark the legacy-script tests clearly (e.g. `test_sec_compat_legacy_*`).

**Acceptance for round 2**

- [ ] 1g's hostile-replay test fails on `4170619` and passes after 1f
      (say so in the notes).
- [ ] `bash scripts/verify.sh` green; count ≥ 1196 + new tests.
- [ ] Push to the **same** branch `sec/chunk-1-json-injection`; add a short
      "Round 2 implementer notes" subsection below this one.

### Round 2 implementer notes

Implemented on the same branch, on top of `4e1d18b`.

**1f — duplicate JSON keys** (`dashboard/core/body_guard.py`, wired via
`app.router.route_class` in `dashboard/main.py`)
- `NoDuplicateJSONKeysRoute` runs before every `/api/` POST/PUT/PATCH handler
  when the content type is `application/json`. It parses with an
  `object_pairs_hook` that records repeated keys at any nesting level; the
  endpoint still receives the body because Starlette caches `request.body()`.
- A duplicate body gets 400 before any store, plus an audit row
  `gateway_body_rejected` with `duplicate JSON keys: <names>` (names only,
  never values). Non-JSON/empty/malformed bodies fall through to the existing
  validation, and `/mcp`, GET and non-`/api` routes are untouched.
- The installed FastAPI is 0.162 (not the 0.139 the plan mentions), which no
  longer accepts `route_class=` in the `FastAPI(...)` constructor; the class is
  assigned to `app.router.route_class` before the first route decorator.

**1g — compat suite** (`tests/test_sec_compat.py`,
`tests/fixtures/legacy/eshu-gateway-625995d.sh`)
- Pinned `git show 625995d:dashboard/eshu-gateway.sh` with a frozen-copy header;
  `GatewaySandbox` now accepts an optional `script=` path.
- Legitimate flows from the pinned gateway (allowlist, blocklist, freeze,
  session-required, window reject, JIT request) are replayed as raw bytes and
  all return 2xx. Hostile prefix values that inject `status` or
  `encoded_command` (including in the JIT body) replay to 400, add no
  `requests` row, leave `/api/poll` at `ticket: null`, and produce a
  `gateway_body_rejected` audit row.

**Test-first evidence**
- Before 1f, **9** of the new tests failed: 6 in
  `tests/test_sec_duplicate_json.py` (duplicate/nested keys returned 200 and
  stored rows) and the 3 `test_sec_compat_legacy_*_is_rejected` hostile replays
  (200 + a stored row). The 6 legitimate legacy flows already passed, as
  intended.
- After: 18 new tests pass; `bash scripts/verify.sh` → **1214 passed**
  (1196 + 18). No installer source changed, so no regeneration was needed.

**Deliberate non-changes**
- Kept 1e (still rejects malformed but non-duplicate ids).
- Non-duplicate malformed bodies are not audited here; they keep FastAPI's
  normal 422 path.

---

## Chunk 2 — Command chaining & self-protection bypasses  · slug `chaining` · HIGH

### Threat

* Regex allowlist entries are matched with `[[ "$cmd" =~ $pattern ]]` and then
  run with `bash -c "$cmd"` (`eshu-gateway.sh:229-238`). Any pattern that is not
  fully anchored or contains `.*` approves `allowed-cmd; anything`.
* Prefix windows (`static/features/approved_windows.sh`, `match_type=prefix`)
  have the same flaw: `"${win_cmd}"*`.
* The hardcoded self-protection blocklist (`eshu-gateway.sh:109-122`) is a
  substring match and misses: `/run/eshu.tickets` (≠ `/var/run/eshu.`),
  `/etc/eshu/` (the directory holding `features.d/` and `dashboard_url`, which
  does not match `/etc/eshu-`), globs (`/etc/eshu?exact.txt`, `/etc/esh*`),
  doubled slashes (`/etc//eshu-…`), and `cd /etc && cat eshu-…`.

### Changes

**2a. Shell metacharacter gate for auto-run paths** (gateway, before stage 3)

* Compute `HAS_META=1` if `$cmd` contains any of: `;` `&` `|` `` ` `` `$(` `>` `<`
  newline, carriage return, or `\`.
* When `HAS_META=1`: skip the exact allowlist (3), regex allowlist (4), and
  **prefix** windows (exact windows still require byte equality, so they may
  stay). The command falls through to JIT, where a human sees it.
* Log `META: routing to JIT` via `logger`, mirroring the encoded-payload handling.
* Escape hatch: none in this chunk. (If an operator legitimately allowlists a
  pipeline, e.g. `ps aux | grep x`, it will now go to JIT. Note this in the
  CHANGELOG under "Security — behaviour change".)

**2b. Self-protection blocklist**

* Normalise before matching: collapse repeated `/`, remove `/./` segments.
  Then block if the normalised command contains any of:
  `/usr/local/bin/eshu-`, `/etc/eshu`, (no trailing dash — covers `/etc/eshu-*`
  and `/etc/eshu/`), `/var/run/eshu`, `/run/eshu`, `eshu.db`, `eshu.tickets`,
  `eshu-session-key`, `features.d`, `sudoers.d/eshu`, `.ssh/authorized_keys`.
* Also block if the command contains a glob character (`*`, `?`, `[`) **and**
  the substring `esh` — cheap, conservative, and blocks `/etc/esh*`.
* Keep the existing `$(which` / `` `which `` rule.
* Update the `case` comment block and `docs/ARCHITECTURE.md` stage-1 row; state
  clearly that stage 1 is **defence in depth, not a sandbox** — commands run as
  root, and the real boundary is stage 2a + human approval.
* Update `test_repo_script_keeps_placeholders_and_expected_paths` only if the
  relocation in the harness needs it; the harness must still relocate paths.

**2c. Dashboard parity**

* `core/cmd_blocklist.py` `HARD_PATTERNS` / `hard_block_match` are used by the
  dashboard for fleet runs and the policy tester. Make them match 2b
  (normalisation + same tokens) so the dashboard never reports "allowed" for
  something the gateway hard-blocks.
* `core/policy_eval.py`: mirror 2a — a command with metacharacters evaluates to
  `jit`, never `auto_approved`, so `/api/policies/test` and `/api/policies/preview`
  tell the truth.

### Tests to add

* Gateway: for an anchored allowlist `^uptime` and a regex `^journalctl .*`,
  each of `uptime; id`, `uptime && id`, `uptime | sh`, `` uptime `id` ``,
  `uptime $(id)`, `uptime > /tmp/x`, `uptime\nid` → goes to JIT (curl to
  `/api/request`), does **not** auto-run.
* Prefix window `systemctl status` + `systemctl status nginx; id` → rejected
  (window reject, not run).
* Self-protection: `cat /run/eshu.tickets`, `ls /etc/eshu/features.d`,
  `cat /etc//eshu-exact.txt`, `cat /etc/./eshu-exact.txt`, `cat /etc/esh*`,
  `echo x > /etc/eshu/dashboard_url` → hard-blocked.
* Plain allowlisted commands (`uptime`, `df -h`) still auto-run.
* Python: `tests/test_policies.py` (or new file) — parity table between
  `hard_block_match` and the gateway for the same command list.

### Compat impact

Gateway-only behaviour change; dashboard parity is display-only. No protocol change.

### Acceptance criteria

- [ ] Every bypass above is a failing test on `master` and passing on the branch.
- [ ] No existing legitimate allowlist test regresses (if one used a pipe,
      move it to the JIT expectation and say why).
- [ ] CHANGELOG entry added.

### Implementer notes

Implemented on `sec/chunk-2-chaining`, cut from the tip of
`sec/chunk-1-json-injection` (`4fab82e`) as requested for tonight's stack.

**2a — metacharacter gate** (`dashboard/eshu-gateway.sh`,
`dashboard/static/features/approved_windows.sh`)
- Before stage 3 the gateway sets `HAS_META=1` for `; & | ` $( > <`, newline,
  CR or backslash, logs `META: routing to JIT`, and skips the exact, regex and
  **prefix** windows. Exact windows still require byte equality and may run.
  There is no escape hatch in this chunk.
- `approved_windows.sh` checks `${HAS_META:-0}` in the prefix branch and falls
  through to the existing `command mismatch` reject, so a prefix window can no
  longer approve `systemctl status nginx; id`.

**2b — self-protection** (`dashboard/eshu-gateway.sh`,
`docs/ARCHITECTURE.md`)
- Stage 1 now normalises repeated `/` and `/./` before matching, blocks
  `/etc/eshu*`, `/run/eshu*`, `/var/run/eshu*`, `eshu.tickets`,
  `eshu-session-key`, `features.d`, `sudoers.d/eshu` and
  `.ssh/authorized_keys`, and refuses any glob character near `esh`. The
  `$(which` / `` `which `` evasion rule is unchanged.
- The stage-1 comment and the architecture table now say explicitly that stage 1
  is **defence in depth, not a sandbox**; commands run as root and the real
  boundary is 2a + human approval.

**2c — dashboard parity** (`dashboard/core/cmd_blocklist.py`,
`dashboard/core/policy_eval.py`)
- `hard_block_match` uses the same normalisation/tokens/glob+`esh` rule, and
  `policy_eval` returns `jit` (detail_type `shell_meta`) for any metacharacter
  command before the allowlists, so `/api/policies/test` and the preview agree
  with the gateway.
- `HARD_PATTERNS` is still the single list exposed to the UI; the parity test
  asserts the same command list the gateway tests use.

**Test-first evidence**
- Before the fix, **35** new assertions failed: 17 in
  `tests/test_gateway_script.py` (9 metacharacter bypasses, exact-allowlist
  bypass, 7 self-protection bypasses, prefix-window bypass) and 18 in
  `tests/test_sec_policy_bypass.py` (hard-block parity/normalisation and
  metachar verdicts).
- After: `bash scripts/verify.sh` → **1263 passed** (1214 + 49 new tests:
  20 gateway, 29 parity). Installers regenerated with
  `cd dashboard && python3 gen_installer.py`; both generated files are
  identical. `dashboard/static/dev/` was restored to HEAD after the golden tests
  dirtied it.

**Existing tests changed (intent preserved)**
- `test_nonzero_exit_is_marked_and_propagated` and
  `test_sanitizer_still_applies_when_the_command_fails` used
  `sh -c 'echo …; exit N'`, which 2a now correctly routes to JIT. They now run
  an executable helper script in the sandbox (`./fails.sh`, `./leaks.sh`) with
  no metacharacters, so exit propagation and output sanitisation are still
  covered.
- The sandbox `curl` stub now handles `-o`/`-w` and
  `/api/approved-windows/execute/*` (returns 200), and has an
  `approved_windows()` helper that installs the real feature script with
  `/etc/eshu-windows.txt` relocated. The relocation assertion accepts both the
  live (`/etc/eshu`) and frozen pre-chunk (`/etc/eshu-`) hard-block token.

**Behaviour change / compatibility**
- Gateway-only; no protocol change. Dashboard parity is display-only.
- As required, operators who allowlisted a pipeline (e.g. `ps aux | grep x`)
  now get JIT; this is recorded in `CHANGELOG.md` under Security as a
  behaviour change. Exact windows remain usable for byte-identical commands.

---

## Chunk 3 — Root-owned temp files  · slug `tmp-races` · HIGH

### Threat

The poller (root) downloads to fixed `/tmp` paths and then executes/parses them:
`/tmp/eshu-policy.json`, `/tmp/eshu-ticket.json`, `/tmp/eshu-update.sh`,
`/tmp/eshu-rollback.sh`, `/tmp/eshu-dev-update.sh`, `/tmp/eshu-uninstall.sh`,
`/tmp/eshu-fleet-runner.sh(.tmp)`, `/tmp/eshu-fleet-<id>.sh`; the fleet runner
writes `/tmp/eshu-fleet-out-<id>.txt`; the installer writes
`/tmp/eshu-syntax-error.log`; the enrollment script (`main.py:1773`) writes
`/tmp/eshu-install.sh` and runs it as root. Root must never execute or parse
a file from a fixed path in a world-writable directory.

### Changes

* Poller: at startup create a private work dir
  `ESHU_WORK=/run/eshu` (`install -d -m 0700 -o root -g root`); if it exists,
  verify it is a real directory (not a symlink), owned by root, mode 0700 —
  otherwise `logger` and exit non-zero (systemd restarts it). Use
  `"$ESHU_WORK"/…` for every temp file above. Use `mktemp -p "$ESHU_WORK"`
  for per-download names.
* Fleet runner: take its output path inside the work dir (pass it as an
  argument from the poller; keep the old positional args order and add the new
  one at the end so an old runner + new poller still works).
* Installer template: `/tmp/eshu-syntax-error.log` → `mktemp`.
* Enrollment script (`main.py` `/api/enroll`): use `T=$(mktemp -d)`, download
  into it, `trap 'rm -rf "$T"' EXIT`.
* Leave `/var/run/eshu.tickets` etc. where they are (root-owned, not in `/tmp`),
  but make sure they are created with `umask 077`.
* Update the self-protection list in chunk 2 if `/run/eshu` is not already covered
  (it is: `/run/eshu`).

### Tests to add

* Poller harness: relocate `ESHU_WORK` into `tmp_path`; assert that no path
  under the harness's fake `/tmp` is written; assert the poller refuses to start
  if `ESHU_WORK` is a symlink or world-writable.
* `grep`-style test: no `/tmp/` literal remains in `eshu-poller.sh`,
  `fleet-runner.sh`, `eshu-installer-template.sh` (allowlist explicit exceptions
  with a comment if any are truly needed).
* `/api/enroll` script contains `mktemp -d` and no `/tmp/eshu-install.sh`.

### Compat impact

Gateway-side only; the fleet-runner argument is appended (old runner ignores it).

### Acceptance criteria

- [ ] No root-executed file is read from a world-writable directory.
- [ ] Poller harness tests green, including the new refusal tests.

### Implementer notes

Implemented on `sec/chunk-3-tmp-races`, cut from the tip of
`sec/chunk-2-chaining` (`ac4bc6e`).

**Poller** (`dashboard/eshu-poller.sh`)
- Startup creates `ESHU_WORK=/run/eshu` with `install -d -m 0700 -o root -g
  root`, then refuses to start if it is a symlink, not a directory, not
  root-owned, or not exactly mode `0700`. It logs the reason (systemd restarts
  the service and the operator sees it) and sets `umask 077`.
- Every scratch/download now lives under `$ESHU_WORK` via `mktemp -p
  "$ESHU_WORK" …`: policy JSON, ticket JSON, update/rollback/dev-update,
  uninstall and the fleet runner download. The per-cycle JSON files are removed
  at the end of the cycle, so the work dir does not accumulate them.
- Fleet dispatch passes a new **7th** argument, the output file
  (`$ESHU_WORK/eshu-fleet-out-<id>.txt`), keeping the old positional order; the
  command ID is validated as numeric before it is used in a path.

**Fleet runner** (`dashboard/static/fleet-runner.sh`)
- Uses `$7` for its output path. An older six-argument poller gets a
  `mktemp` sibling of the command file instead of a fixed `/tmp` name (new
  runner + old poller still works; old runner + new poller is unchanged).

**Installer template** (`dashboard/eshu-installer-template.sh`)
- The syntax-check log and the legacy authorized_keys rewrite both use
  `mktemp` now. The second one was not in the chunk text, but it is the same
  class of fixed root-owned `/tmp` path and the new no-`/tmp/` test would
  otherwise need an exception.

**Enrollment bootstrap** (`dashboard/main.py` `/api/enroll`)
- The generated script downloads into `T=$(mktemp -d)` and registers
  `trap 'rm -rf "$T"' EXIT`; the installer is run from `"$T/eshu-install.sh"`.
  The explicit `rm` is gone (the trap owns cleanup).

**Test-first evidence**
- Before the fix, **11 of the 12** new `tests/test_sec_tmp_races.py` tests
  failed (work-dir writes, symlink/world-writable/owner refusal, all three
  no-`/tmp/` scans, installer mktemp, fleet output path, enrollment bootstrap).
  The remaining umask assertion was tightened to require a top-level
  `^umask 077$` (the pre-fix script only had subshell umasks), so all 12 are
  genuine regressions.
- `bash scripts/verify.sh` → **1275 passed** (1263 + 12). Installers
  regenerated; both generated files are identical. `dashboard/static/dev/` was
  restored to HEAD after the golden tests dirtied it.

**Existing tests changed**
- `tests/test_enrollment.py::test_script_is_privilege_aware` asserted
  `bash /tmp/eshu-install.sh`, i.e. the fixed world-writable path this chunk
  removes; it now asserts `bash "$T/eshu-install.sh"`.
- `tests/test_poller.py`'s harness now creates a sandbox `work/` dir, stubs
  `stat` for the owner/mode check, records `-o` targets and `systemd-run`
  arguments, and treats the `ESHU_WORK` relocation as optional so the new
  tests run against the pre-fix script too.

**Compatibility / deliberate non-changes**
- Gateway-side only; no protocol change. `/var/run/eshu.tickets` and the other
  lockbox/trigger files stay where they are (root-owned, not world-writable);
  `umask 077` only tightens how the poller creates them.
- The tests do not run as root: the sandbox pre-creates `ESHU_WORK` and stubs
  `stat`, so the production ownership check is exercised without needing root.

---

## Chunk 4 — Dashboard XSS, CSP, CSRF  · slug `web-hardening` · MEDIUM

### Threat

* `dashboard/static/app.js:677` puts `g.hostname` raw into an `onclick` string
  and innerHTML; `g.ip` is raw at lines 622, 989, 1007. Hostname is rewritten on
  every heartbeat (`db/gateways.py:296-304`), so a compromised gateway can inject
  script into the operator's session → Fleet Run → root on every gateway.
* No Content-Security-Policy.
* Session cookie is `SameSite=Lax` only. Lax does not stop **same-site**
  requests (other homelab apps on sibling subdomains). Body-less POSTs such as
  `/api/approve/{id}`, `/api/unfreeze`, `/api/dev/promote`,
  `/api/gateways/{ip}/uninstall`, `/api/policies/commit`,
  `/api/policies/trigger-update` are forgeable with a plain HTML form.

### Changes

* **XSS:** audit every `innerHTML` / string-built HTML in `app.js` (there are
  ~130). Every interpolated value that is not a number must go through `esc()`;
  values inside inline `onclick="...('X')"` must not be raw strings — prefer
  `data-*` attributes + one delegated listener, or at minimum
  `esc(JSON.stringify(value))`. Fix lines 622, 677, 989, 1007 and any other
  instance found. Record the list of fixed sites in the implementer notes.
* **Server-side input validation:** `RegisterPayload.ip` / `HeartbeatPayload.ip`
  must be a valid IP (`ipaddress.ip_address`); `hostname` limited to
  `^[A-Za-z0-9._-]{1,253}$` — reject otherwise (400). Compat: real gateways send
  `hostname -I` / `hostname` values that satisfy this.
* **CSP:** add a middleware setting
  `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`
  plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`,
  `X-Frame-Options: DENY` on HTML and `/api/*` responses (skip `/mcp`).
  `'unsafe-inline'` for scripts stays until inline handlers are gone — note it as
  follow-up. Check `index.html` for external origins first; if any exist, list
  them explicitly in the policy.
* **CSRF:** middleware for state-changing methods (POST/PUT/PATCH/DELETE) on
  `/api/*` **that are authenticated by the session cookie**: require either
  (a) `Origin` header equal to the request's own origin (scheme+host[:port],
  honouring `X-Forwarded-Proto`/`Host`), or (b) a custom header
  `X-Eshu-CSRF: 1` that `app.js`'s fetch wrapper always sends. Reject with 403
  otherwise. Requests that carry `X-Gateway-Token`, `X-Enrollment-Token` or an
  agent bearer token and **no session cookie** are exempt (gateways/agents, not
  browsers). `/api/auth/login` must also be protected (login CSRF).
  Find the single fetch wrapper in `app.js`/`state.js` and add the header there;
  if calls bypass the wrapper, fix them.

### Tests to add

* Heartbeat with hostname `x');alert(1);//` → 400.
* Register with `ip` `1.2.3.4');//` → 400.
* CSRF: session-authed POST `/api/freeze` without `Origin` and without
  `X-Eshu-CSRF` → 403; with either → 200; with a foreign `Origin` → 403.
* Gateway-token POST `/api/request` without the header → still 200 (compat).
* CSP header present on `/` and on an `/api/...` response.
* Update `tests/conftest.py`'s `auth_client` to send `X-Eshu-CSRF: 1` by default
  so existing tests pass — do this in one place, not per test.
* `scripts/ui_check.py` / `node --check` still pass.

### Compat impact

Gateways/agents unaffected (exempt by design). Browser UI requires the new
`app.js` — cache-bust the `?v=` query in `index.html`.

### Acceptance criteria

- [ ] No raw interpolation of server data into HTML/JS strings remains in `app.js`
      (reviewer will grep).
- [ ] Manual smoke test noted in implementer notes: login, approve a JIT, run
      a fleet command, freeze/unfreeze — all via the UI with the new headers.

### Implementer notes

Implemented on `sec/chunk-4-web-hardening`, cut from the tip of
`sec/chunk-3-tmp-races` (`114cb6f`).

**XSS (`dashboard/static/app.js`)**
- Added `escJs(v) = esc(JSON.stringify(v))`, plus `idJs`/`idText` numeric
  coercers. `esc()` alone is not safe inside a single-quoted inline handler
  because the HTML parser decodes entities back before V8 compiles it.
- A full audit of all **130 `innerHTML` sites** found **70 findings / 76
  interpolations / 18 function groups**; all are fixed. The 18 groups:
  `openContextPanel`, `renderSeg`, `fetchWindowsTable`, `queueModel`,
  `renderQueue`, `smSshDetailCard`, `openSessionModal`, `renderJitItem`,
  `renderTable`, `renderSessionOverrideBanner`, `renderFleetRun`,
  `fetchPolicyChanges`, `renderAuditLog`, `renderRhythm`, `fetchVersion`,
  `fetchAgentTokens`, `renderTools`, `renderFleetTxRail`.
- The highest-risk sinks were the feature-flag key (`renderSeg`), request
  status text (`renderTable`), chart date labels (`renderRhythm`) and
  `/api/version` (`fetchVersion`). The other 72 were ids (integers in today's
  schema) now passed through `Number()`/`idJs`; that is defence in depth.
- Also hardened earlier in the pass: gateway ip/hostname handlers
  (`renderDevGatewayPills`, `onDevGatewayInput` via `data-dev-host` + delegated
  listener, window gateway menus, gateways table, stats nodes), session ids
  (`sessionRow`, `renderSessionCard`), integration names
  (`renderIntegrationList`), fleet `gateway_ip` handlers, window tokens,
  policy old/new content, deploy hashes/version, and generated regexes.
- No `insertAdjacentHTML`/`outerHTML`/`document.write`/`srcdoc` sinks exist.

**CSRF**
- New `csrf_middleware`: for `/api/` POST/PUT/PATCH/DELETE authenticated by a
  valid `eshu_session` cookie, it requires `Origin` equal to this request's
  own scheme+host (honouring `X-Forwarded-Proto`/`Host`) or `X-Eshu-CSRF: 1`;
  otherwise 403. `/api/auth/login` is always checked (login CSRF).
- Requests with no session cookie are left to the existing auth gates, so
  gateway tokens, enrollment tokens and agent bearer tokens are unaffected.
- `app.js` now installs one global `fetch` wrapper that adds `X-Eshu-CSRF: 1`
  to every state-changing call, including raw `fetch()` sites; `index.html`
  cache-busts `app.js` to `?v=ui12`.

**CSP / headers**
- `security_headers_middleware` sets `Content-Security-Policy` (exactly the
  policy in the chunk), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin` and `X-Frame-Options: DENY` on HTML and
  `/api/*` responses; `/mcp` is skipped. `index.html` has no external origins.
- Follow-up (as the plan allows): `script-src` still includes
  `'unsafe-inline'` because inline handlers remain; moving them to delegated
  listeners lets the CSP drop it.

**Server-side validation**
- `_validate_gateway_identity` uses `ipaddress.ip_address` and
  `^[A-Za-z0-9._-]{1,253}$` on `/api/register` and `/api/gateway-heartbeat`,
  before auth, returning 400. Real gateways send `hostname -I`/`hostname`, so
  compatible.

**Test-first evidence**
- `tests/test_sec_web_hardening.py` has 12 tests. Against the chunk-3 tip
  (`114cb6f`) **7 failed** (hostname/ip validation, CSRF header/foreign-Origin,
  login CSRF, CSP headers); the 5 positive/no-change tests passed. All 12 pass
  after the fix.
- `bash scripts/verify.sh` → **1287 passed** (1275 + 12).
  `node --check dashboard/static/app.js` and `scripts/ui_check.py` pass. No
  installer source changed, so no regeneration was needed.

**Existing tests changed**
- `tests/conftest.py`'s `auth_client` now sends `X-Eshu-CSRF: 1` (one place,
  as required) and the four manual login/logout calls in `test_auth.py`,
  `test_api.py`, `test_policies.py` add the header.

**Manual smoke test**
- **Not run in this unattended environment** (no browser and no deployment).
  The reviewer should verify login, approve a JIT, run a fleet command and
  freeze/unfreeze in the UI with the new `app.js`; the API-level equivalents
  (session writes with the header/Origin, gateway-token exemption, CSP headers)
  are covered by the tests above.

**Compatibility**
- Gateways/agents are unaffected (exempt by design). The browser UI requires
  the new `app.js`; the `?v=ui12` bump makes browsers pick it up. No protocol
  or response-shape change.

---

## Chunk 5 — Unauthenticated endpoints & weak tokens  · slug `authz` · MEDIUM

### Threat / changes

| # | Where | Change |
|---|---|---|
| 5a | `GET /api/policies/test` (`main.py:845`) — no auth, reveals allowlist/blocklist | require `_check_session` |
| 5b | `GET /api/approved-windows` — lists commands/targets to anyone | require session **or** a gateway token (return only that gateway's windows) **or** an agent token. Check what `scripts/eshu_call.py` and `docs/AGENT_MANUAL.md` use before changing; keep the agent path working |
| 5c | `POST /api/window-requests` — accepts calls with no token (`main.py:2390`) | require a gateway token or agent token; reject unauthenticated |
| 5d | `GET /api/enroll/token-status` — unauth | require session |
| 5e | `POST /api/auth/set-password` on first run | allow unauthenticated only from loopback **or** when an env var `ESHU_SETUP_TOKEN` matches a header; print the setup token to stdout at startup when no password is set. Document in `docs/FIRST_RUN.md` |
| 5f | Enrollment tokens (`db/enrollment.py:31`) use time + `hash(str(conn))` | `secrets.token_urlsafe(24)` |
| 5g | Window tokens (`db/windows.py:128,288`) use `random.choices`, 10 chars | `secrets.choice`, 24 chars, alphanumeric (keep the charset — chunk 1 regex) |
| 5h | Host header injected into the enrollment bash script (`main.py:1755-1768`) | validate `Host` against `^[A-Za-z0-9.-]+(:[0-9]{1,5})?$` (and bracketed IPv6); otherwise 400 |
| 5i | `approved_windows.sh` claim fails **open** on any status except 403/404 | fail open **only** on curl exit/`000` (network down); every HTTP status other than 200 rejects |
| 5j | Minimum password length 4 (`main.py:405`) | 12 |
| 5k | `_verify_password` plaintext fallback (`core/utils.py`) | remove; if a legacy plaintext hash is detected at startup, log a loud warning telling the operator to run `set_password.py` |

### Tests to add

One test per row (401/403 when unauthenticated, 200 when authorised); token
entropy/charset tests; window claim stub returning 429 / 500 / 401 → rejected,
curl exit 7 → runs (fail-open retained only for network loss).

### Compat impact

5b/5c may affect agents — verify `scripts/eshu_call.py`, `docs/AGENT_MANUAL.md`,
MCP tools in `core/tool_handlers.py` first and keep them working (update the
manual if the auth header changes). 5e changes first-run UX — document it.

### Implementer notes

Implemented on `sec/chunk-5-authz`, cut from the tip of
`sec/chunk-4-web-hardening` (`9c845be`).

**Endpoints**
- **5a** `GET /api/policies/test` now calls `_check_session` (session only). The
  response shape is unchanged. Agents lose this pre-flight; the manual now says
  to send the command and let the gateway decide.
- **5b** `GET /api/approved-windows` accepts a session, a gateway token or an
  agent bearer token. A gateway token is scoped to its own `target_ip`; agents
  and sessions may use the optional `ip` filter. Non-session callers still get
  `token`/`retrieval_key` stripped.
- **5c** `POST /api/window-requests` requires a gateway token (bound to
  `gateway_ip`) or an agent bearer token; a browser session is deliberately not
  an agent credential. Unauthenticated calls are 401.
- **5d** `GET /api/enroll/token-status` requires a session.
- **5e** First-run set-password is allowed only from loopback or with
  `X-Eshu-Setup-Token` matching `ESHU_SETUP_TOKEN` (or the generated
  `_SETUP_TOKEN`). Startup prints `First-run setup token: …` when no password is
  set. The setup overlay has a **Setup token** field (remote installs), and the
  app.js cache-buster is `ui13`. `docs/FIRST_RUN.md` documents the flow.
- **5h** `/api/enroll` validates `Host` as `hostname[:port]` or
  `[IPv6][:port]`; anything else is 400 before the bash bootstrap is built.

**Tokens / passwords**
- **5f** Enrollment tokens are `secrets.token_urlsafe(24)` (32 chars) instead of
  the time/connection-hash value.
- **5g** Window tokens are 24 alphanumeric chars from `secrets.choice`, at all
  three creation sites (`create_approved_window`, `create_window_request`,
  `approve_window_request`). The charset keeps the chunk-1 gateway regex valid.
- **5j** Minimum password length is 12 in the API, the setup/settings UI and
  `dashboard/set_password.py`.
- **5k** `_verify_password` has no plaintext fallback; a legacy non-PBKDF2 hash
  fails login and startup prints a loud warning pointing at
  `python3 dashboard/set_password.py`.

**5i window claim**
- `approved_windows.sh` captures curl's exit status and treats any non-zero exit
  as `000` (network down → fail open on the local cache). Every other non-200
  HTTP answer (401/403/404/429/500/…) is a refusal via `_window_reject`. The
  200-with-valid-cache path still runs.

**Test-first evidence**
- Before the fix, **14** new assertions failed: 11 in `tests/test_sec_authz.py`
  (5a/5b/5c/5d/5e/5f/5g/5h/5j/5k) and 3 gateway window-claim cases
  (401/429/500); 403/404 already rejected. After the fix all pass.
- `bash scripts/verify.sh` → **1313 passed** (1287 + 26: 20 in
  `tests/test_sec_authz.py`, 6 in `tests/test_gateway_script.py`).
  No installer source changed (the feature script is served directly), so no
  regeneration was needed.

**Existing tests changed**
- `tests/test_policies.py`: `test_policy_test_stays_public` → now asserts 401
  (`test_policy_test_requires_session`), reflecting 5a.
- `tests/test_window_read.py`: the "open" read tests now use an agent token for
  the list and window-request POST; the claim source assertion checks the new
  200/000/other branches.
- `tests/test_enrollment.py`: token-status tests use a session; the
  register-consumes-token test uses a fresh unauthenticated client so the
  enrollment path (not the session path) is exercised.
- `tests/test_auth.py`: 12-char passwords; first-run test sends the setup token.
- `tests/test_cmd_risk.py`: the risk/dry-run policy test uses `auth_client`.

**Compatibility / docs**
- Gateways are unaffected: gateway-token callers keep working; a gateway token
  on the list endpoint is scoped to its own windows.
- Agents must now send `Authorization: Bearer <agent-token>` for `POST
  /api/window-requests` and `GET /api/approved-windows`; the read-only poll by
  `retrieval_key` and the single-window lookup are unchanged. `docs/AGENT_MANUAL.md`
  examples and the endpoint table were updated. `scripts/eshu_call.py` already
  supports arbitrary auth headers and MCP tool handlers do not call these
  endpoints, so no code change was needed there.
- `/api/policies/test` is session-only; agents should send the command instead.
- First-run UX: remote operators use the Setup token field (or loopback/CLI);
  documented in `docs/FIRST_RUN.md`.

---

## Chunk R — Review follow-ups  · slug `review-fixups` · MEDIUM

Branch `sec/chunk-R-review-fixups` from `security-hardening` (normal §0 rules,
one branch, one PR). Each item needs a `test_sec_R_*` regression test that
fails before the fix.

**R1. Self-protection rules that are too broad (chunk 2 follow-up).** Stage 1
is FATAL and cannot be approved by a human, so false positives are permanent
for that command. Today these are hard-blocked:
`systemctl status *mesh*`, `find /home -name "*refresh*"`,
`[ -f /tmp/threshold ] && echo y`, `curl -s "http://x/api?fresh=1"`,
`grep -r Refresh /var/log/*`, `ls -la ~/.ssh/authorized_keys`.
(The glob+`esh` rule was the planner's choice, not an implementer error.)

* Keep **FATAL** for the explicit Eshu paths/tokens: `/usr/local/bin/eshu-`,
  `/etc/eshu`, `/var/run/eshu`, `/run/eshu`, `eshu.db`, `eshu.tickets`,
  `eshu-session-key`, `/etc/sudoers.d/eshu`, and `features.d` **only when it
  appears as `eshu/features.d`**.
* Move to a new **force-JIT** tier (like the encoded-payload and metacharacter
  gates — never auto-run, but a human may approve it):
  * glob characters (`*`, `?`, `[`) combined with the case-insensitive
    substring `esh` **only when it is a path component prefix**
    (`/esh`, ` esh`, `=esh`, or the start of the command) — e.g.
    `cat /etc/esh*` still forced to JIT, `ls *mesh*` not affected at all;
  * `.ssh/authorized_keys` (any user).
* Mirror the tiers in `core/cmd_blocklist.py` / `core/policy_eval.py`
  (fatal vs `jit` with `detail_type: "self_protection_jit"`).
* Tests: every false-positive command above is **not** FATAL (and runs if
  allowlisted and metacharacter-free); `cat /etc/esh*`, `cat ~/.ssh/authorized_keys`
  go to JIT; every chunk-2 FATAL regression test still passes unchanged.

**R2. Permanent compat test for the window feature script.** The dashboard
serves `static/features/approved_windows.sh` to **every** gateway on the next
poll — it bypasses Build → Edge → Fleet. Add to `tests/test_sec_compat.py`:
legacy gateway (`fixtures/legacy`) + the current feature script — exact window
with claim 200 runs; prefix window with claim 200 runs; claim 404 is rejected;
claim network failure runs. (Reviewer verified these by hand; make them
permanent.)

**R3. Duplicate-key guard when `Content-Type` is missing.** FastAPI parses the
body as JSON when no `Content-Type` is sent, but `core/body_guard.py` only
inspects `application/json`. Also inspect bodies with **no** content type
(and `application/*+json`). Tests for both.

**R4. First-run loopback check behind a same-host proxy (chunk 5 follow-up).**
`_first_run_setup_allowed` trusts `request.client.host == 127.0.0.1`. If a
reverse proxy runs on the same host, every remote browser looks like
loopback. Treat the request as non-loopback when it carries any of
`X-Forwarded-For`, `X-Real-IP`, `Forwarded`. Document in `docs/FIRST_RUN.md`.
Test: loopback client + `X-Forwarded-For` and no setup token → 403.

**R5. Docs — residual risk, no code.** In `docs/ARCHITECTURE.md` (policy
pipeline section) add a short "Writing safe allowlist rules" note: anchor with
`^…$`, avoid `.*` in arguments, and that argument-level execution
(`find … -exec … +`, `xargs`, `awk 'BEGIN{system(…)}'`, quote-split paths) is
not caught by the metacharacter gate — such commands belong in JIT or an
exact rule.

### Acceptance criteria

- [ ] R1–R4 regression tests fail before / pass after; R5 docs present.
- [ ] `bash scripts/verify.sh` green, count ≥ 1313 + new tests.
- [ ] Installers regenerated (R1 touches `eshu-gateway.sh`).

### Implementer notes

Implemented on `sec/chunk-R-review-fixups`, cut from `security-hardening@9624247`.

**R1 — self-protection tiers**
- FATAL is now only the explicit Eshu paths/tokens: `/usr/local/bin/eshu-`,
  `/etc/eshu`, `/var/run/eshu`, `/run/eshu`, `eshu.db`, `eshu.tickets`,
  `eshu-session-key`, `eshu/features.d`, `/etc/sudoers.d/eshu`, plus the
  `$(which` / `` `which `` evasion rule.
- New stage 1.5 `SELF_JIT` (never FATAL, never auto-run, human may approve):
  any `.ssh/authorized_keys`, and a glob character next to an `esh` path
  component (`/esh`, space, `=esh`, or command start). It skips the exact,
  regex and feature-script/window paths, then goes to JIT.
- `core/cmd_blocklist.py` mirrors this with `self_protection_jit_match()`;
  `hard_block_match()` is FATAL-only. `policy_eval` returns
  `action: "jit", detail_type: "self_protection_jit"` after the editable
  blocklist (matching the gateway's stage order).
- Tests: the six listed false positives are not FATAL;
  `systemctl status *mesh*`, `find … "*refresh*"` and the fake dashboard curl
  run when allowlisted (stubbed binaries), while `grep` is classified only
  (the gateway itself uses `grep`); `cat /etc/esh*` and
  `cat ~/.ssh/authorized_keys` are JIT even when allowlisted. The chunk-2 FATAL
  cases are unchanged except that the two broad ones are now in the JIT tests.

**R2 — window feature-script compat**
- Added four permanent tests in `tests/test_sec_compat.py`: legacy gateway
  (`625995d`) + current feature script, exact claim 200 runs, prefix claim 200
  runs, claim 404 rejects, claim network failure (curl exit 7) runs.
- These are test-only; they already passed on `security-hardening` because the
  chunk-5 claim fix is already in the served feature script. They now guard the
  direct-served script path against regressions.

**R3 — duplicate-key media types**
- `core/body_guard.py` now inspects a body when `Content-Type` is missing/empty
  and when the media type is `application/*+json`; an explicit other type
  (e.g. `text/plain`) is still left to the endpoint. Tests cover no content
  type and `application/vnd.api+json`.

**R4 — loopback behind a same-host proxy**
- `_first_run_setup_allowed` treats any `X-Forwarded-For`, `X-Real-IP` or
  `Forwarded` header as non-loopback, requiring `X-Eshu-Setup-Token`.
  `docs/FIRST_RUN.md` documents it. Tests cover all three headers.

**R5 — docs**
- Added "Writing safe allowlist rules" to `docs/ARCHITECTURE.md` (anchor
  `^…$`, avoid `.*`, and argument-level execution via `find -exec`, `xargs`,
  `awk 'BEGIN{system(…)}'`, etc. is not caught by the metacharacter gate).

**Test-first evidence**
- Before the fixes, **26** `test_sec_R_*` assertions failed (R1 gateway/parity,
  R3, R4, R5). The four R2 tests passed immediately (test-only item, no fix).
- Final `bash scripts/verify.sh` → **1343 passed** (1313 + 30 new R tests).
  The count was kept at baseline + new tests by adding three explicit Eshu-path
  FATAL cases while the two broad chunk-2 param cases moved to JIT; no coverage
  was dropped.
- Installers regenerated with `cd dashboard && python3 gen_installer.py`; both
  generated files are identical. `dashboard/static/dev/` was untouched.

**Compatibility**
- New gateway + new dashboard agree on the tiers. New dashboard + old gateway
  remains safe: the old gateway still FATALs the broad commands until it is
  updated (dashboard tester shows JIT for them); no request shape changed.
- This only narrows permanent refusals and adds a force-JIT tier; explicit Eshu
  paths remain FATAL. No protocol change.

---

## Chunk H — Read-only integration tools must really be read-only  · slug `read-tools` · HIGH · **READY**

Branch `sec/chunk-H-read-tools` from `security-hardening` (normal §0 rules).
Dashboard only — no gateway, poller or installer changes.

### Threat

`core/tool_runner.py` runs any tool marked `read_only: True` immediately, with
no approval (the "Read-only: always execute" branch). Two **generic** read tools
take the operation from the agent's arguments and never check that it reads:

1. **`read` (generic HTTP, `core/generic_tools.py`)** — documented as "GET or
   HEAD", but the runner forwards `args['method']` to `execute_generic_call`,
   which accepts `POST`, `PUT`, `PATCH` and `DELETE`. Reviewer-verified: a
   `read` call with a mutating method reaches the upstream with that method
   and no approval. Enabled live on 9 integrations (incl. Home Assistant,
   Proxmox, Omada, Pulse, Jellyfin, the *arr apps), all using admin-level
   credentials.
2. **`ws_read` (generic Home Assistant WebSocket)** — forwards any command type
   and payload (`core/ha_ws.py: execute_ws_call`). Service calls, Supervisor
   API commands and registry `create/update/delete/remove` commands all run
   without approval, bypassing the gated `call_service` / `ws_write` tools.

The only limit today is an optional per-agent `arg_policy` rule
(`core/agent_policy.py`: `method_allow`, `command_allow*`), which agents without
a policy do not have. Live data (2026-11-07): every past `read` call was GET
(161) or HEAD (3) and every `ws_read` call was a genuine read (42) — no misuse.

### Changes

**H1. Generic HTTP `read` is GET/HEAD only.** In the tool runner, before
executing a generic HTTP tool whose `read_only` is true, normalise
`args['method']` (default `GET`) and refuse anything other than `GET`/`HEAD`
with `{"error": "not_a_read_method", ...}` (HTTP-style status 400) pointing to
the gated `write` tool. Enforce it in `execute_generic_call` too by giving it a
`read_only: bool` parameter (defence in depth), so no future caller can reuse
the read path with a mutating method.

**H2. `ws_read` only runs read commands.** New `core/ha_ws_policy.py` with
`is_read_command(command) -> bool`:
* **True** only for an explicit reviewed set plus safe name patterns: last path
  segment in `list`, `get`, `get_single`, `info`, `browse_media`,
  `statistics_during_period`, `config` (read form, e.g. `automation/config`);
  plus `render_template`, `get_states`, `get_config`, `get_services`.
* **False** (checked first, wins over the patterns) for `call_service`,
  `execute_script`, any `supervisor/*`, `hassio/*`, `auth/*`, `config/auth*`,
  `backup/*`, `repairs/*` actions, any `subscribe_*`/`unsubscribe_*`, and any
  command whose last segment is `create`, `update`, `delete`, `remove`, `set*`,
  `reload`, `restart`, `migrate`, `install`, `uninstall`, `write`.
* Unknown commands are **false** (fail closed) — they can still be run through
  the gated `ws_write`.
The runner refuses a `ws_read` whose command is not a read with
`{"error": "not_a_read_command", ...}` pointing to `ws_write`, **before** any
WebSocket connection is opened.

**H3. Audit.** Every refusal from H1/H2 writes an `integration_calls` row
(`outcome: "refused"`, the method/command, no payload values) and a
`record_audit_event('read_tool_refused', ...)`. These checks run regardless of
agent policy; agent `arg_policy` rules still apply on top.

**H4. Catalog invariant.** A test iterates every seeded catalog
(`core/*_seed.py`, `generic_tools_for(kind)` for every kind) and asserts:
every `read_only: True` HTTP tool uses `GET`/`HEAD`, **unless** it is
`not_implemented`, or it is listed in an explicit, commented allowlist of
reviewed read-by-POST endpoints (e.g. query/search endpoints that only read).
Every `read_only: True` WS tool with a fixed `path_template` must satisfy
`is_read_command`. A new tool that breaks this fails CI.

**H5. Docs.** `CHANGELOG.md` under "Security — behaviour change";
`docs/AGENT_MANUAL.md` (integrations section): `read` accepts only GET/HEAD,
`ws_read` only read commands; use `write` / `ws_write` (approval-gated) for
anything else, and the two new error codes.

### Tests

* H1: generic `read` with `POST`, `PUT`, `PATCH`, `DELETE` (and lower-case
  variants) → `not_a_read_method`, **no upstream request** (assert on the mock
  upstream), one refused audit row. `GET` and `HEAD` unchanged (existing tests).
  `execute_generic_call(..., read_only=True)` with `DELETE` raises.
* H2: allowed table — every command in live use: `automation/config`,
  `recorder/statistics_during_period`, `persistent_notification/get`,
  `system_log/list`, `config/area_registry/list`,
  `config/entity_registry/get`, `config_entries/get_single`,
  `media_source/browse_media`, `render_template`, `zha/devices`, plus
  `get_states`, `get_config`, `get_services`. (`zha/devices` has no read-style
  last segment — add it to the explicit set and say so in a comment.)
  Refused table — `call_service`, `supervisor/api`, `execute_script`,
  `config/entity_registry/update`, `config/device_registry/remove`,
  `input_boolean/create`, `backup/generate`, `auth/delete_refresh_token`,
  `subscribe_events`, and an unknown command. A refused `ws_read` opens **no**
  WebSocket (monkeypatch `ha_ws_request` to fail the test if called) and writes
  the audit row.
* H4: the catalog invariant test, plus a deliberately bad fake tool proving it
  fails.
* Compat: curated tools and `write`/`ws_write` behave exactly as before; MCP
  tool schemas unchanged.

### Compat impact

Additive refusals on two generic tools; schemas unchanged; no gateway impact.
Agents that only read (all live traffic) are unaffected.

### Operator stopgap until this ships

For each agent without a policy, an `arg_policy` rule on each generic `read`
tool with `method_allow: ["GET", "HEAD"]`, and on Home Assistant `ws_read`
a `command_allow` list of the read commands above. Remove once H ships.

### Acceptance criteria

- [ ] H1–H4 regression tests fail before / pass after.
- [ ] `bash scripts/verify.sh` green; count ≥ current baseline + new tests.
- [ ] Reviewer re-runs the generic-`read` mutating-method reproduction → refused.

### Implementer notes

_(fill in)_

---

## Chunk 6 — Update integrity & per-gateway session keys  · slug `supply-chain` · HIGH (design change)

> The reviewer will write a detailed design for this chunk before it starts.
> Do not begin Chunk 6 until this section says **READY**.

Scope preview:

* Installer, feature scripts and fleet runner are downloaded over (often plain)
  HTTP and run as root with no integrity check → LAN MITM = root on the fleet.
  Plan: the dashboard publishes the SHA-256 of each served script in the
  **authenticated** policy response; the poller verifies before running/moving.
  Optional second step: detached signatures (`ssh-keygen -Y verify`).
* One session-signing key is sent to every gateway (`main.py:734`); the HMAC
  covers only the session id. Plan: derive a per-gateway key
  (`HMAC(master, gateway_ip)`) and include an expiry in the signed payload;
  remove the "fail open until first key sync" window.

---

## Chunk 7 — Low-severity hardening  · slug `low` · LOW

> Detailed list will be finalised after Chunk 5. Preview:

* Hash gateway tokens at rest like agent tokens (`db/gateways.py:139`), with a
  migration; constant-time comparisons.
* Server-side session revocation: a per-install session epoch bumped on logout
  and on password change.
* Trust `X-Forwarded-Proto` only from configured proxy IPs (`ESHU_TRUSTED_PROXIES`).
* `/api/register` rate limit keyed on `request.client.host`, not `payload.ip`.
* Integration proxy: disable automatic redirects or strip auth headers on
  cross-host redirects; reject `%2e%2e` in paths.
* Pin dependency versions in `dashboard/requirements.txt`.
* Docs: README/ARCHITECTURE wording about the blocklist being a hard boundary.

---

## 8. Rollout gate (no deploy before this)

Applies to the merge of `security-hardening` → `master` and any deploy to the
dashboard LXC or gateways. The reviewer runs it; the implementer does not deploy.

1. All chunks in scope are **Merged** in the table below.
2. `bash scripts/verify.sh` green on `security-hardening`; CI green on GitHub.
3. Regenerated installers are in sync: `cd dashboard && python3 gen_installer.py && git diff --exit-code`.
4. Compat check: run the **old** gateway script (from `master`) in the
   `GatewaySandbox` against the **new** dashboard `TestClient` for the main
   flows (allowlist log, blocked log, JIT request → approve → claim, heartbeat,
   policy poll). Added as `tests/test_sec_compat.py` in Chunk 1 and extended
   by later chunks.
5. Manual UI smoke test against a **local** dashboard run from the branch
   (`cd dashboard && ESHU_SETUP_TOKEN=… bash start.sh`, browser on loopback):
   first-run setup, login/logout, approve + deny a JIT, Fleet Run dispatch,
   freeze/unfreeze, create/toggle/delete a window, add/test an integration —
   with the browser devtools console free of CSP or 403 CSRF errors.
   **Done 2026-11-07 at `39c40e8` (reviewer, headless Chromium via
   Playwright, fresh DB on 127.0.0.1):** 14/14 checks — CSP header present,
   first-run setup on loopback, fake gateway enrol + heartbeat, JIT approve and
   deny through the UI handlers, freeze/unfreeze, window create/toggle/delete,
   Fleet Run dispatch, integration add, every view rendered, a stored
   `<img onerror>` command shown as text and never executed, cookie-only write
   refused with 403, logout + login. Zero console errors, zero CSP violations,
   zero JS exceptions, zero unexpected 4xx/5xx.
6. Note: `static/features/approved_windows.sh` and `static/fleet-runner.sh`
   reach **every** gateway as soon as the dashboard is deployed (no Edge
   stage) — their legacy-gateway compat tests (Chunk R2) must be green.
7. Deploy order:
   1. Dashboard LXC: back up `eshu.db`, `git pull` the merged `master`,
      `bash scripts/deploy-dashboard.sh`, check the UI and `/api/version`.
   2. Gateways: Seed Edge → push to **one** dev gateway → soak ≥ 24 h (JIT,
      allowlist, window, fleet run, freeze/unfreeze, uninstall/reinstall on a
      throwaway host) → Promote to Fleet.
   3. Rollback: dashboard `git checkout <previous tag>` + deploy; gateways via
      the existing Golden rollback.

---

## 9. Status

| Chunk | Slug | Severity | Status | Branch | Reviewer notes |
|---|---|---|---|---|---|
| 1 | json-injection | Critical | **Merged** | `sec/chunk-1-json-injection` @ `4fab82e` | Round 2 approved; reviewer repro (old-gateway raw bodies) → 400, no ticket |
| 2 | chaining | High | **Merged** (follow-up R1) | `sec/chunk-2-chaining` @ `ac4bc6e` | Approved; glob+esh rule too broad → R1 |
| 3 | tmp-races | High | **Merged** | `sec/chunk-3-tmp-races` @ `114cb6f` | Approved |
| 4 | web-hardening | Medium | **Merged** | `sec/chunk-4-web-hardening` @ `9c845be` | Approved; UI smoke test done 2026-11-07 (see §8 item 5) |
| 5 | authz | Medium | **Merged** (follow-up R4) | `sec/chunk-5-authz` @ `2d579be` | Approved; loopback check vs same-host proxy → R4 |
| R | review-fixups | Medium | **Merged** | `sec/chunk-R-review-fixups` @ `0a8e400` | Approved; reviewer re-ran full suite (1343 passed), pre-commit, installer sync, and the tier probe (former false positives run normally; broad matches → JIT; Eshu paths FATAL) |
| H | read-tools | High | **Ready** | `sec/chunk-H-read-tools` | Found during feature work (ws_read) + reviewer (generic read accepts mutating methods) |
| 6 | supply-chain | High | Design pending | — | |
| 7 | low | Low | Draft | — | |

Chunks are done **in order**; each new chunk branches from the updated
`security-hardening` after the previous one is merged.

Rollout 2026-11-07: `master` deployed to the dashboard LXC (pre-deploy DB
backup taken; rollback commit `625995d`). Edge pushed to dev gateways heimdall
and n8n; Hermes post-hardening suite passed (15/15 incl. re-tests 9b/11b);
a second dev push confirmed the new poller's update path; Fleet Run passed on
both. Promoted to Fleet: all 16 active gateways downloaded the installer and
re-synced healthy (poller + gateway OK); zero rejected gateway bodies, zero 5xx.

Review of 2026-11-07: chunks 1–5 reviewed as a stack and fast-forwarded into
`security-hardening`. Reviewer re-ran `pre-commit.sh`, the installer-sync
check and the **full suite at `2d579be`: 1313 passed**, plus private repros
(old-gateway injected bodies on `/api/log` and `/api/request`, incl.
`application/json; charset=utf-8`) and old-gateway + new `approved_windows.sh`
(exact, prefix, 404) — all as expected.
