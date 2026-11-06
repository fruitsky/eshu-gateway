# Feature Plan — Reviewable approvals & less approval fatigue

Status: **draft** · Branch: `feature-roadmap` (local) · Baseline: `master@625995d`

This plan turns the feature analysis of 2026-11-06 into small, reviewable
chunks. It uses the same workflow as `docs/security/HARDENING_PLAN.md` (on
branch `security-hardening`): one agent implements a chunk, a second agent
reviews it against the acceptance criteria before it is merged. **Nothing
reaches the dashboard LXC or any gateway until the rollout gate in § 8 passes.**

---

## 1. Why — what the live data says

A read-only query of the live dashboard database (17 Jul – 6 Nov 2026, about
2,200 SSH requests on 20 gateways, about 690 MCP calls) gave these numbers. Only
aggregates are recorded here; **no raw commands, IPs or session goals go into
this repo** (it is public).

| Observation | Number | What it means |
|---|---|---|
| Real operator denials | ≈ 4 (the other ~110 are test pings) | Denying is rare; a "deny with a reason" feature has low value |
| Average command length by month | 95 → 110 → 418 → **1,329** → 394 chars | Agents now send scripts, not commands |
| Commands > 200 / > 1,000 chars | 641 / 118 | |
| Chained (`&&`/`;`) / piped | ~40% / ~40% | |
| Commands using `echo === SECTION ===` markers (since Sep) | 262 of 1,129 | The agent packs several checks into one approval |
| Distinct commands seen exactly once | 1,148 of 1,248 | The allowlist and gap learning can rarely help |
| Human approval latency | avg 16 s; 49% ≤ 10 s; none > 90 s; >1 kB commands avg 21 s | Long commands cannot be read in the time given |
| Human approvals ≤ 60 s after the previous one | 463 of 848 | Approvals come in bursts |
| JIT approvals given by host/session override, not a human | 419 of 1,267 (33%) | In big sessions the operator gives up and opens an override |
| Biggest sessions: commands via override | 55/113, 89/111, 48/56 | |
| Read-style vs change-style commands (rough keyword count, since Sep) | ~460 vs ~130 | Most traffic is the agent looking around |
| Commands that use `pct exec` / `docker exec` | 23 / 82 | The real target is a container, not the host that is recorded |

**Conclusion.** The bottleneck is *how reviewable a command is*, not policy.
The work below, in order:

1. **Make a command readable in seconds** (break it into steps, mark the writes).
2. **Let reading run without a prompt** where it is safe (opt-in, shadow mode first).
3. **Let the agent ask once for a planned set of changes** instead of the
   operator opening a blanket override.
4. **Record what happened** (exit code and output), so sessions can be reviewed afterwards.

---

## 2. Ground rules

Everything in `HARDENING_PLAN.md` § 0 applies unchanged (branching, generated
installers, compatibility rule, hermetic tests, style). The differences:

* Chunk branches are `feat/fN-<slug>`, created from **`feature-roadmap`**.
  Do not push to `master`, `feature-roadmap` or `security-hardening`.
* Test names: `test_fN_<what>`.
* **No real data in fixtures.** Command fixtures are synthetic. They may copy
  the *shape* of live traffic (sections, `pct exec` loops, journalctl
  pipelines, a base64 script piped through `su -c`) but never real hostnames,
  IPs, paths that reveal people, or `.env` contents.
* **Classification is advisory unless a chunk says otherwise.** F1/F2 only
  change what the operator sees. Only F4 lets a classification approve
  anything, and only behind an off-by-default setting.

### 2.1 Coexisting with the security work

The security branch changes the same files (`main.py`, `app.js`,
`eshu-gateway.sh`). The rules:

| Chunk | Touches | Can start |
|---|---|---|
| F1 | new module + one hook in `main.py` | **now**, from `feature-roadmap` |
| F2, F3, F4, F6, F7 | `main.py`, `app.js`, `db/*` | after security chunks 4–5 are merged into `security-hardening`; then merge `security-hardening` into `feature-roadmap` first |
| F5 | `eshu-gateway.sh`, poller, installers | only after security chunks **1–3** are merged (JSON body rules, ticket binding, temp-file rules) |

**Design rule that follows from security chunk 2a:** once 2a lands, any
command with shell metacharacters will never auto-run from a gateway
allowlist. So every new "auto-approve" path here (F3 plans, F4 read lane) is
decided **on the dashboard, inside `POST /api/request`**, the same way host
and session overrides work today (`main.py:561–634`). The gateway then claims
the ticket as usual, and security chunk 1c makes sure it runs exactly the
approved bytes. **No feature in this plan adds a gateway-side allowlist.**

---

## Chunk F1 — Command breakdown engine · slug `breakdown` · dashboard only

### Goal

A pure, well-tested Python module that turns a command string into a list of
steps, each tagged *read*, *write* or *unknown*, plus a one-line summary.
F2 (UI), F4 (read lane) and F7 (inner target) all build on it.

### Changes

**New `dashboard/core/cmd_parse.py`**

```python
def breakdown(command: str) -> dict
```

Returns:

```json
{
  "version": 1,
  "steps": [
    {"text": "journalctl -u nginx --since today --no-pager",
     "op": "&&",                 // operator joining it to the previous step: "", "&&", "||", ";", "|", "\n"
     "kind": "read",             // read | write | unknown
     "why": "journalctl (no vacuum/rotate)",
     "section": "LOGS",          // nearest preceding echo marker, or ""
     "context": ["pct 103"],     // wrapper chain, outermost first
     "elevated": false},         // sudo / su seen in the wrapper chain
    ...
  ],
  "sections": ["LOGS", "DISK"],
  "summary": {"steps": 9, "read": 8, "write": 1, "unknown": 0},
  "headline": "9 steps · 8 read · 1 write: systemctl restart nginx",
  "inner_targets": ["pct:103"],  // for F7
  "flags": ["redirect-to-file", "heredoc", "encoded", "nested-ssh", "sensitive-path"],
  "complete": true               // false if parsing gave up somewhere (then kind=unknown)
}
```

Parsing rules:

1. Tokenise with `shlex.shlex(posix=True, punctuation_chars=True)`. Split on
   `&&`, `||`, `;`, `|`, `&` and newlines that are outside quotes.
2. **Section markers:** a step that is only `echo`/`printf` of text matching
   `^[=\-#*\s]*[A-Za-z0-9 _./:-]{1,40}[=\-#*\s]*$` with at least 2 marker
   characters becomes the `section` of the steps that follow. It is not counted
   as a step.
3. **Unwrap wrappers, depth ≤ 3:** `sudo [-n] [-u X]`, `timeout N`, `nice`,
   `ionice`, `env VAR=… cmd`, `sh -c '…'` / `bash -c '…'` (parse the string
   again), `su [-] user -c '…'`, `pct exec <id> -- …`, `docker exec [-i|-t|-u X]* <ctr> …`,
   `docker compose [-f F] exec <svc> …`. Each unwrap adds to `context`. Past
   depth 3 → `unknown`.
4. **Encoded payloads:** if `cmd_codec.detect_encoded_exec` matches, decode with
   `decode_preview` and parse the decoded text as nested steps
   (context `"decoded"`, flag `encoded`). If decoding fails → one `unknown` step.
5. **Heredocs** (`<<`): flag `heredoc`. The body is not executed text unless it
   is fed to `sh`/`bash`/`python*` (then `unknown`).
6. **Redirections:** `>`/`>>` to anything other than `/dev/null`, and `2>&1`
   aside → the step becomes `write` with why `redirect to <path>`; flag `redirect-to-file`.
   `<` input is fine.
7. `$( … )` and backticks: parse the inside as steps (context `"subst"`); the
   outer step's kind is the worst of its own kind and the inner kinds.
8. Nested `ssh` → `unknown`, flag `nested-ssh`. `xargs`, `watch`, `parallel`,
   `find -exec/-execdir/-ok/-delete`, `awk` with `system(` or `print >`,
   `sed` with `-i` or a `w`/`W`/`e` command → `write` or `unknown`
   (**when in doubt, `unknown`**).
9. **Sensitive paths** (flag only; F4 refuses them): `.env`, `*.pem`, `*.key`,
   `id_rsa*`, `id_ed25519*`, `authorized_keys`, `/etc/shadow`, `/etc/gshadow`,
   `/root/.ssh`, `*secret*`, `*token*`, `*passw*`, `*credential*`, `eshu`.

**New `dashboard/core/cmd_rules.py`** — the verb table, kept as data so it can
be reviewed on its own:

* `READ`: `cat head tail grep egrep fgrep rg ls stat file find* df du free
  uptime hostname uname id whoami date ps top(-b) ss netstat(-tulpn variants)
  ip(addr|link|route|neigh + show/list only) lsblk blkid findmnt mount(no args)
  journalctl(no --vacuum-*/--rotate/--flush/--relinquish) systemctl(status
  is-active is-enabled is-failed list-* show cat) docker(ps logs inspect images
  stats --no-stream version info; compose ps/logs/config/version) pct(list
  status config) qm(list status config) zpool(status list) zfs(list get)
  apt(list, policy) apt-cache apt-config(dump) dpkg(-l -s -L) dpkg-query
  sensors smartctl(-a -H -i -x) nvme(smart-log list) dmesg(no -c/-C) lscpu
  lspci lsusb nproc sort uniq wc cut tr awk* sed* echo printf test [ true
  which type command(-v) readlink realpath basename dirname git(status log
  diff show branch, no -c) python3(-V/--version) node(-v) wc jq yq`
  (`*` = with the argument limits from rule 8).
* `WRITE`: everything in `cmd_risk.RISK_RULES`, plus `rm mv cp mkdir rmdir
  touch ln chmod chown tee dd mkfs* truncate systemctl(start stop restart
  reload enable disable mask unmask daemon-reload) docker(run rm rmi stop start
  restart kill pull build compose up/down/pull/restart) apt/apt-get(install
  remove purge upgrade full-upgrade dist-upgrade autoremove update) pip/npm
  install kill pkill killall reboot shutdown poweroff halt crontab useradd
  usermod userdel passwd iptables(-A -I -D -F) ufw nft umount mount(with args) sysctl -w`.
* Anything else → `unknown`. `curl`/`wget` → `unknown` for F4 purposes, but
  `why` says "network fetch (GET)" when there is no `-o/-O/-T/-d/--data*/-X`
  other than GET, so F2 can render it softly.

**Hook in `main.py`:** `list_requests` attaches `r['breakdown'] = breakdown(cmd)`
to **pending** rows only (next to `risk`/`anomaly`). Add
`GET /api/requests/{req_id}/breakdown` (session auth) for history rows. Cache
results in a small LRU keyed by `sha256(command)` (`functools.lru_cache(maxsize=512)`
on the command string), because the queue is polled.

**New `scripts/breakdown_report.py`** — offline evaluation:
`python3 scripts/breakdown_report.py --db PATH [--since YYYY-MM-DD] [--show-unknown N]`.
Opens the DB **read-only** (`file:…?mode=ro`), runs `breakdown` over distinct
commands, and prints aggregates only: % of steps read/write/unknown, % of
commands that are fully read, top 20 `unknown` verbs (verb only, not the
command). `--show-unknown` prints example steps and exists only for local use.
The reviewer runs this against a copy of the live DB; its output is not committed.

### Tests to add (`tests/test_cmd_parse.py`, `tests/fixtures/commands/*.txt`)

* A table of ≥ 60 synthetic cases covering every rule above, including:
  section-marker scripts; `pct exec` loops over 3 ids; `docker exec` + `sh -c`;
  `su - u -c "echo <b64> | base64 -d | bash"` (decoded steps appear);
  journalctl | grep | sed | sort | uniq pipelines (all read);
  `sed -i`, `tee`, `> file`, `>> file`, `2>&1`, `> /dev/null`;
  `find … -delete`, `awk '{system("x")}'`; nested ssh; quotes containing `;` and `|`
  (must **not** split); depth-4 wrapping (→ unknown); unterminated quote
  (→ `complete: false`, one unknown step, no exception).
* Property-style: for 500 random strings, `breakdown` never raises and
  `summary` counts add up to `len(steps)`.
* `breakdown` of a step that `cmd_risk.get_cmd_risk` flags is never `read`
  (consistency with the existing risk map).
* API: a pending row in `/api/requests` has `breakdown`; history rows do not;
  `/api/requests/{id}/breakdown` returns it and needs a session.
* Performance: 1,000 breakdowns of a 2 kB command finish in < 2 s on CI.

### Compat impact

Additive JSON fields and one new GET endpoint. No gateway change.

### Acceptance criteria

- [ ] All tests green; `bash scripts/verify.sh` count ≥ baseline + new tests.
- [ ] Reviewer's `breakdown_report.py` run on the live DB copy: ≤ 15% of steps `unknown`,
      and every `unknown` in the top 20 is either justified or added to the rules.
- [ ] Not one fixture contains real hostnames, IPs or secrets.
- [ ] No behaviour change: `/api/policies/test`, `/preview` and the gateway are untouched.

### Implementer notes

_(fill in)_

---

## Chunk F2 — Reviewable approval card · slug `review-card` · dashboard UI

### Goal

Use the F1 breakdown so the operator can judge a 1–2 kB command in seconds.

### Changes (`dashboard/static/app.js`, `style.css`, `index.html`)

* **Headline line** at the top of every pending card (queue hero card, context
  panel `ctx-*`, session modal rows, the mobile drawer):
  `9 steps · 8 read · 1 write — systemctl restart nginx`. Colours: all-read =
  muted/neutral, any write = warning, any unknown = warning with "read it as
  written". Never green; the card must not look "pre-approved".
* **Step list** below the raw command (raw command stays available, collapsed
  by default when `summary.steps ≥ 4`):
  * grouped by `section`;
  * each step shows its `context` chain as a small prefix chip (`pct 103`, `sudo`, `decoded`);
  * `write` and `unknown` steps are expanded and highlighted; runs of `read`
    steps collapse to "6 read steps ▸";
  * flags shown as chips: `encoded`, `heredoc`, `nested ssh`, `sensitive path`, `redirect`.
* **Approve confirm for unknown/write-heavy commands:** if `summary.unknown ≥ 1`
  *and* the command is > 500 chars, the first Approve press scrolls to and
  briefly highlights the first unknown step; a second press approves. (No modal,
  no extra click for short or all-read commands.)
* Keyboard: the existing queue cursor shortcuts keep working; add `X` to toggle
  the raw command.
* History rows: a "breakdown" link that fetches `/api/requests/{id}/breakdown` on demand.
* `describeCmd` / `ctx-human` stay as they are for single-step commands.

### Tests to add

* Extend `scripts/ui_check.py` (or the existing UI test pattern) to load a fixture
  pending row with a breakdown and assert the headline and step list render, and
  that agent-controlled text is escaped (step `text`, `section`, `context` all go
  through `escapeHtml`). This XSS check is mandatory — section names are
  agent-controlled.
* `node --check app.js` (pre-commit).
* Screenshots for the reviewer: desktop + 400 px width, for a 9-step read-only
  command, a mixed command, and an encoded one. Attach them under Implementer notes.

### Compat impact

UI only.

### Acceptance criteria

- [ ] A 1 kB synthetic recon command with one `sed -i` shows the write step
      without scrolling, at desktop and phone width.
- [ ] Every agent-controlled string is escaped (test proves it).
- [ ] No regression in the existing card (risk line, anomaly, decoded preview, TTL).

### Implementer notes

_(fill in)_

---

## Chunk F3 — Session plans · slug `session-plans` · dashboard + MCP

### Goal

Replace "approve five, then open a 30-minute override" with one meaningful
review: the agent sends the **changes** it intends to make, the operator
approves the list, and those exact commands run without further prompts during
the session. Looking around is covered by F4; F3 covers the doing.

### Design

* A plan belongs to a minted session (`sessions.id`). Items are exact commands
  per host:
  `{"host": "<gateway ip or *>", "command": "<exact text>", "max_uses": 1}`.
  Matching is **byte-exact after trimming whitespace**. No patterns, no
  prefixes: this keeps the chunk 2a and 1c guarantees intact.
* Lifecycle: `proposed → approved | denied → expired | ended`. Default TTL
  60 min, max 4 h, set by the operator on approval. One active plan per session;
  a new proposal replaces a `proposed` one and needs a fresh approval if one is
  already `approved`.
* The operator may **remove items** before approving (approve a subset). Editing
  item text is out of scope.
* Max 50 items, each ≤ 4 kB.

### Changes

* **DB** (`db/session_plans.py`, tables created in `db/core.py` init):
  `session_plans(id, session_id, status, created_at, decided_at, decided_by, until, note)`
  and `session_plan_items(id, plan_id, host, command, max_uses, uses, removed)`.
* **MCP tools** (`core/mcp_server.py`, added next to `session_begin`, always allowed):
  * `session_plan(session_id, items, note)` → `{plan_id, status:"proposed"}`
    and sends a notification ("Plan for session X: 7 changes on 3 hosts").
  * `check_plan(plan_id)` → status, remaining uses per item, `until`.
  Update `SESSION_BEGIN_DESC` and `docs/AGENT_MANUAL.md`: *recon first, then
  propose a plan for the changes*.
* **REST** (session auth): `GET /api/session-plans?status=proposed`,
  `POST /api/session-plans/{id}/approve` `{ttl_minutes, removed_item_ids[]}`,
  `POST /api/session-plans/{id}/deny`, `POST /api/session-plans/{id}/end`.
  Every action is audit-logged (`plan_proposed/approved/denied/ended`).
* **Approval path** (`POST /api/request`): after the freeze check and before
  the session override, if `sid` has an approved, unexpired plan with an item
  matching `(host or *, command)` and `uses < max_uses`, and the gateway is not
  Zero-Trust → atomically increment `uses` (single `UPDATE … WHERE uses < max_uses`),
  create the request as `approved` with `reason="session-plan:<plan>:<item>"`,
  and audit `jit_plan_approved`. The breakdown-based checks in F4 do **not** apply here.
  The operator approved these exact bytes.
* **UI:** a "Plans" card in the queue area above single requests. Each item is
  rendered with the F2 breakdown component; checkboxes remove items; a TTL
  selector (30 / 60 / 120 / 240 min). Session modal shows the plan and per-item uses.
* **Stats:** the request reason prefix `session-plan:` counts as its own
  "how approved" bucket (see F6).

### Tests to add

* Propose → approve subset → matching command auto-approves once; second use
  with `max_uses=1` → normal pending JIT; removed item → pending JIT.
* Host-specific item does not match another host; `*` matches any.
* Expired / ended / denied plan → pending JIT.
* Zero-Trust gateway and freeze both win over a plan.
* Concurrency: two simultaneous requests for a `max_uses=1` item → exactly one approved
  (reuse the `test_db_concurrency.py` pattern).
* A plan for session A never approves a command carrying session B's id, or no id.
* With strict sessions on, an unsigned id cannot use a plan (the gateway refuses
  first; add a dashboard-side assertion too).
* MCP: `session_plan` validates its inputs (item count, sizes, host format) and
  rejects an unknown session id.

### Compat impact

Additive. Old gateways work unchanged: they already poll `request_status` and
claim tickets for `approved` rows.

### Acceptance criteria

- [ ] All tests above pass; the audit log shows who approved which items.
- [ ] Docs updated (AGENT_MANUAL, API.md).
- [ ] Reviewer sign-off from the security chat on the approval path (one paragraph in Implementer notes).

### Implementer notes

_(fill in)_

---

## Chunk F4 — Read-only lane (shadow first) · slug `read-lane` · dashboard

### Goal

Let commands that **only read** run without a prompt, opt-in per gateway, after
a shadow period that shows the operator what it *would* have approved.

### Design

* Per-gateway setting `read_lane`: `off` (default) | `shadow` | `on`.
* A JIT request is **read-lane eligible** only if all of these hold:
  1. `breakdown.complete` is true and every step is `read` (no `write`, no `unknown`);
  2. no flags from {`encoded`, `heredoc`, `nested-ssh`, `sensitive-path`, `redirect-to-file`};
  3. no `elevated` step that reads a path outside `/proc`, `/sys`, `/var/log`, `/etc/os-release`
     (reading as root can reach secrets; keep it narrow);
  4. command ≤ 8 kB; gateway not frozen and not Zero-Trust;
  5. not blocked by the central blocklist (`policy_eval` verdict is not `blocked`/`fatal`).
* `shadow`: the request stays pending as usual, but the row gets
  `reason="read-lane-shadow"` (shown as a small chip "would auto-run") and an
  audit event. The operator's own decision is the ground truth.
* `on`: create the request as `approved`, `reason="read-lane"`, audit
  `jit_read_lane_approved`, no notification.
* A **read-lane report** in Statistics: for shadow-mode gateways, how many
  requests were eligible, how many of those the operator approved and denied,
  and the median length. This is the evidence for switching to `on`.

### Changes

* `gateways.read_lane` column (migration, default `'off'`); `PUT /api/gateways/{ip}/read-lane`
  (session + audit). Toggle in the Gateways table next to Zero-Trust (three-state).
* `core/read_lane.py` `eligible(breakdown, verdict) -> (bool, reason)`.
* Hook in `POST /api/request` after plans (F3) and session overrides, before host override.
* Stats endpoint fields + UI tile.

### Tests to add

* An eligibility table: ≥ 40 cases from the F1 fixtures, each with the expected
  verdict, including every exclusion above. **Every `write`/`unknown` fixture
  from F1 must be ineligible** (generated test).
* `shadow` never approves; `on` approves only eligible commands; `off` does nothing.
* Zero-Trust, freeze and blocklist win.
* Reading `.env` (even names only) is ineligible.

### Compat impact

Additive; old gateways work unchanged (same approved-row path as overrides).

### Acceptance criteria

- [ ] Default is `off` on every existing and new gateway (migration test).
- [ ] Security chat has reviewed `cmd_rules.py` and `read_lane.py` before merge (recorded here).
- [ ] Rollout note: at least 7 days in `shadow` on at least 2 gateways before
      `on` is offered in the UI (the toggle shows "on" disabled until that gateway has ≥ 7 days of shadow data).

### Implementer notes

_(fill in)_

---

## Chunk F5 — Execution results (exit code + output tail) · slug `exec-results` · gateway + dashboard

> **Blocked** until security chunks 1–3 are merged. Before this chunk starts,
> the reviewer rechecks this design against the merged gateway script.

### Goal

Record what actually happened for every executed SSH command: exit code,
duration, and the last part of the (already sanitized) output.

### Design

* Gateway: `run_sanitized` keeps streaming to the agent unchanged and, in
  addition, tees the sanitized stream into a private temp file (created
  following the security chunk 3 rules), capped at 16 KiB (keep the **tail**:
  `tail -c 16384`). After the command ends it posts, in the background:
  `POST /api/exec-result {target_ip, request_id?, encoded_command, exit_code, duration_ms, encoded_output, output_truncated}`.
  * `request_id` is known for JIT/ticket paths. For allowlist and window paths
    the dashboard matches on `(target_ip, sha256(command))` to the newest
    `auto-approved`/`window-approved` row in the last 120 s without a result.
  * JSON body rules from security chunk 1 apply (base64 for free text; validated ids).
  * Pure bash + coreutils on the hot path (Pi 1 rule): no extra `python3` start.
* Per-gateway setting `capture_output`: `off` | `exit-only` (default) | `tail`.
  Delivered in the policy poll like `zero_trust` and written to `/etc/eshu-capture`.
* Dashboard: table `request_results(request_id PK, exit_code, duration_ms, output, truncated, created_at)`.
  Before storing, run `core/secret_scrub.py` over the output (second pass after the gateway's `sanitize`).
  Retention: 30 days by default (setting), cleaned by the existing cleanup loop.
* UI: exit-code badge and duration on history rows and in the session modal; output
  behind a "show output" toggle (escaped, monospace, max height).
* Session summary (`get_session_summary`) adds `failed` (exit ≠ 0).

### Tests to add

* Gateway sandbox: exit 0 / non-zero / output > 16 KiB (tail kept, truncated flag) /
  `capture_output=off` posts nothing / `exit-only` posts no output / the agent's stdout is byte-identical
  to today's (regression).
* Matching for allowlist paths; no double-attach; unknown request id rejected.
* Secret scrub applied; retention cleanup.
* Compat: old gateway + new dashboard (no results posted) works; new gateway + new dashboard posts.

### Compat impact

New endpoint, new poll field. Old gateways never call it.

### Acceptance criteria

- [ ] Agent-visible output and exit codes unchanged (golden test).
- [ ] Results visible for JIT, allowlist, window and plan paths.
- [ ] Installers regenerated in the same commit; `static/dev/` untouched.

### Implementer notes

_(fill in)_

---

## Chunk F6 — Session wrap-up & "how approved" stats · slug `session-wrapup` · dashboard

### Changes

* Session modal header card: goal, duration, commands by **how they were approved**
  (allowlist · human · plan · read-lane · session override · host override ·
  window), writes (from F1) per host, failures (F5, if present), and total
  time the agent waited for a human.
* Statistics: a stacked "how approved" series over 14 days, with **override share**
  as the headline number (data showed 33%; the goal of F3/F4 is to replace overrides).
* `session_summary` MCP output gains the same `by_approval` breakdown.

### Tests

* Bucket mapping from `status`/`reason` covers every value present in the code
  (`override`, `session-override:*`, `session-plan:*`, `read-lane`, `read-lane-shadow`,
  window statuses, `fleet-run`, `integration-*`), with a test that fails when a new
  reason prefix is added without a bucket.

### Implementer notes

_(fill in)_

---

## Chunk F7 — Inner target attribution · slug `inner-target` · dashboard

### Changes

* `requests.inner_target` column (`pct:103`, `docker:sonarr`, `qm:201`), filled
  at request/log time from `breakdown.inner_targets` (first one; `multi` if several).
* History filter and a chip on rows; Statistics "busiest targets" counts inner targets.
* Optional: map `pct:<id>` to a gateway hostname when the Proxmox integration
  knows it (read-only lookup, cached; skip if not configured).

### Tests

* Fill-in for each wrapper type; `multi`; none.

### Implementer notes

_(fill in)_

---

## Chunk F8 — Test-traffic hygiene · slug `test-traffic` · dashboard · LOW

### Changes

* `requests.excluded` flag; `POST /api/requests/exclude` `{ids[] | session_id}` (session auth, audit).
  Statistics, learning gaps (`core/learning.py`) and anomaly profiles ignore excluded rows.
* Sessions whose goal contains "smoke test" or "test" are suggested (not
  auto-excluded) in the session modal.
* One-off: the operator can exclude the August test-ping burst from the UI; no
  migration touches live data automatically.

### Implementer notes

_(fill in)_

---

## 7. Backlog (not planned in detail)

* Deny with a reason, shown to the agent (cheap; low value given the data).
* MCP `ssh_exec` tool, so MCP-only agents reach gateways through the same gate.
* Gateway tags, with policy / freeze / Fleet Run per tag.
* Approve/deny from a phone notification (Web Push via the existing PWA, or ntfy) —
  needs a security design for authenticating the notification action.
* Config export/import (policies, agent policies, windows, integration definitions without secrets).
* Split `main.py` into FastAPI routers; build installers in CI instead of committing copies.
* Agent-facing read-only "would this auto-run?" tool (lower value: commands rarely repeat).

---

## 8. Rollout gate

Same as `HARDENING_PLAN.md` § 8, plus:

1. The security rollout goes first. No feature chunk is deployed before the
   merged security work is live and soaked.
2. Deploy dashboard-only chunks (F1, F2, F6, F7, F8) together. F3 and F4 each
   need a separate deploy note in the CHANGELOG ("new approval path").
3. F4 ships with every gateway `off`; the operator moves gateways to `shadow` by hand.
4. F5 follows the gateway pipeline: Seed Edge → one dev gateway → soak ≥ 24 h → Fleet.

---

## 9. Status

| Chunk | Slug | Area | Status | Branch | Depends on | Reviewer notes |
|---|---|---|---|---|---|---|
| F1 | breakdown | dashboard | **Ready** | `feat/f1-breakdown` | — | |
| F2 | review-card | UI | Ready (after F1 + sec 4–5) | `feat/f2-review-card` | F1, sec 4 (CSP) | |
| F3 | session-plans | dashboard + MCP | Ready (after F2) | `feat/f3-session-plans` | F2, sec 5 | |
| F4 | read-lane | dashboard | Ready (after F3) | `feat/f4-read-lane` | F1, F3, security review | |
| F5 | exec-results | gateway + dashboard | Blocked | `feat/f5-exec-results` | sec 1–3 | design recheck needed |
| F6 | session-wrapup | dashboard | Ready (after F4) | `feat/f6-session-wrapup` | F3, F4 (F5 optional) | |
| F7 | inner-target | dashboard | Ready (after F1) | `feat/f7-inner-target` | F1 | |
| F8 | test-traffic | dashboard | Ready | `feat/f8-test-traffic` | — | low priority |
