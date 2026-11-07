# Feature Plan — Reviewable approvals & less approval fatigue

Status: **active** · Branch: `feature-roadmap` · Baseline: `master@5c0045f` (security chunks 1–5 + R + H merged), merged into this branch 2026-11-07

This plan turns the feature analysis of 2026-11-06 into small, reviewable
chunks. One agent (the **implementer**) builds one chunk per branch; a second
agent (the **reviewer**) reviews the diff against the chunk's acceptance
criteria and merges it. The security work (`docs/security/HARDENING_PLAN.md`,
chunks 6–7) is **paused** while this plan runs. **Nothing reaches the
dashboard LXC or any gateway until the rollout gate in § 8 passes.**

## 0. Start here (implementer)

1. Read § 1 (why), § 2 (ground rules) and **only** the chunk you are given.
2. Do the chunks **in the order of the status table (§ 9)**. Start with the
   first chunk whose status is **Ready**. Do not start a chunk that says
   *after* another chunk until that chunk is **Merged**.
3. One chunk per branch, one branch per hand-off. When you are done, push the
   branch and stop. The reviewer will answer on the same branch (review notes
   are committed into the chunk's "Reviewer notes" section) or merge it.
4. If anything in a chunk is unclear, contradicts the code, or looks unsafe:
   do the safe, smaller thing, and explain it under **Implementer notes**.
   Do not widen the scope.

---

## 0.1 The features in plain words

| Chunk | What it does for the operator |
|---|---|
| F1 | Splits each long command into steps and labels each step read, write or unknown |
| F2 | The approval card shows "9 steps · 8 read · 1 write: …" and highlights the risky step |
| F7 | Records the real target of `pct exec` / `docker exec`; warns when a Proxmox-host override also covers its containers |
| F3 | The agent proposes a list of changes; approve the list once and those exact commands run without more prompts |
| F4 | Read-only commands can run without asking, per host, opt-in, after a week in "shadow mode" |
| F5 | Records each command's exit code and the end of its output |
| F12 | Agents run SSH commands through Eshu over MCP with their own token: one identity per agent for SSH and APIs, no shared SSH key |
| F6 | A summary card per session: goal, what ran, what changed, what failed, how much ran under overrides |
| F13 | At the end of a job the agent files a conclusion (what worked, what didn't, the fix); confirmed conclusions become searchable runbooks |
| F8 | Mark test traffic so it stops distorting statistics |
| F9 | Freeze also stops integration writes; read-only views of HA add-ons, backups, updates and system info |
| F10 | Approval-gated HA actions: add-on restart/update, backups, HA update, restart after a passing config check |
| F11 | Edit HA YAML through Eshu with a diff, automatic backup, and automatic undo if the config check fails |
| F14 | "Where is X?": one lookup for a service's host, container, IP, URL, proxy entry and DNS name |
| F15 | One-call health checks (service, container, disk, DNS) that return a short summary |
| F16 | Reviewed, parameterised scripts ("recipes") any agent can run with one approval, often drafted from F13 runbooks |
| F17 | A ready-made setup for a local model in LM Studio: its own token, a small toolset, and a hand-off to Hermes |

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

## 2. Ground rules (read before every chunk)

### 2.1 Branching and workflow

1. `git fetch origin && git switch feature-roadmap && git pull --ff-only`
2. Create the chunk branch **from `feature-roadmap`**:
   `git switch -c feat/fN-<slug>` (the slug is in each chunk heading).
3. Implement **only that chunk**. Out-of-scope findings go under
   "Implementer notes" — do not fix them.
4. Run the full gate: `bash scripts/verify.sh` (≈ 6 min). It must be green and
   the test count must be **≥ the previous count + the tests you added**.
   Quote the final pytest summary line in your implementer notes.
5. Conventional commits, small and logical, e.g.
   `feat(dashboard): add command breakdown engine`. End every commit message with
   a `Co-Authored-By:` line naming your model, if your harness adds one.
6. `git push -u origin feat/fN-<slug>`. **Do not** merge it yourself and **do
   not** push to `master`, `feature-roadmap` or `security-hardening`.
7. Last commit on the branch: fill in "Implementer notes" for the chunk in this
   file (what you did, anything surprising, what you deliberately did not do,
   the pytest summary line, and for UI chunks the screenshot paths).

The reviewer merges approved chunks into `feature-roadmap` and updates § 9.

### 2.2 Source-of-truth files — never hand-edit generated copies

| Edit this | Never edit by hand |
|---|---|
| `dashboard/eshu-gateway.sh` | `dashboard/eshu-gateway-install.sh` |
| `dashboard/eshu-poller.sh` | `dashboard/static/eshu-gateway-install.sh` |
| `dashboard/eshu-logger.sh` | `dashboard/static/dev/eshu-gateway-install.sh` (managed by the Edge pipeline) |
| `dashboard/eshu-installer-template.sh` | |

After changing any left-hand file:

```bash
cd dashboard && python3 gen_installer.py && cd ..
git diff --stat   # both generated installers must change identically
```

Commit the regenerated `dashboard/eshu-gateway-install.sh` and
`dashboard/static/eshu-gateway-install.sh` **in the same commit** as the
source change. Never touch `dashboard/static/dev/`. Only F5 touches gateway
scripts; every other chunk is dashboard-only.

### 2.3 Compatibility rule

The dashboard is deployed first; gateways update later via Build → Edge → Fleet.

* **New dashboard ↔ old gateway must keep working.** Every request today's
  gateway sends must still be accepted, and every response field it reads keeps
  its shape. New JSON fields are additive only.
* New gateway ↔ new dashboard must work. New gateway ↔ old dashboard need not.
* Each chunk states its compat impact. If you think a change breaks this rule,
  stop and say so in the implementer notes.

### 2.4 Testing rules

* Hermetic only: **no root, no network, no real `/etc`, no paths outside
  pytest's `tmp_path`.** Use the existing harnesses:
  * `tests/conftest.py` — `client` / `auth_client` FastAPI fixtures (state-changing
    calls from `auth_client` need the CSRF header the fixture already sets; check
    how `tests/test_sec_web_hardening.py` does it).
  * `tests/test_gateway_script.py` — `GatewaySandbox` (copies the real
    `eshu-gateway.sh`, relocates paths, stubs `curl`/`logger`). Extend it; don't fork it.
  * `tests/test_poller.py` — same pattern for the poller.
  * `tests/test_db_concurrency.py` — pattern for race tests.
* Test names: `test_fN_<what>`. Every behaviour in "Tests to add" gets a test.
* Do not weaken, skip or delete existing tests. If one must change, say why in
  the commit message.
* `bash scripts/pre-commit.sh` must pass (`bash -n`, `py_compile`, `node --check app.js`, UI token checks).

### 2.5 Data and privacy (the repo is public)

* **No real data in the repo.** Fixtures are synthetic. They may copy the
  *shape* of live traffic (section markers, `pct exec` loops, journalctl
  pipelines, a base64 script piped through `su -c`) but never real hostnames,
  IPs, people's names, paths that reveal people, tokens or `.env` contents.
  Use `192.0.2.x` / `198.51.100.x` (documentation ranges) and names like `web01`.
* Never copy the live database into the repo. `eshu.db` must not be committed.

### 2.6 Safety rules specific to this plan

* **Classification is advisory unless a chunk says otherwise.** F1/F2 change
  only what the operator sees. Only F3 (exact operator-approved bytes) and F4
  (off by default, shadow first) create new auto-approve paths.
* **New auto-approve paths are decided on the dashboard, inside
  `POST /api/request`** (`receive_request`, `dashboard/main.py` ≈ line 680),
  the same way session and host overrides are. The gateway already refuses to
  auto-run anything with shell metacharacters (stage 2.7 `HAS_META` in
  `eshu-gateway.sh`) and runs only the exact bytes of a claimed ticket. **No
  chunk adds a gateway-side allowlist, prefix match or new auto-run stage.**
* Order inside `receive_request` after this plan (keep it exactly):
  metadata validation → rate limit → decode → freeze flag → encoded-payload
  block → **F3 plan** → session override → **F4 read lane** → host override → pending.
  Freeze, Zero-Trust and the blocklists always win.
* Every new approval path writes an audit event and a distinct `reason`
  prefix on the request row (F6 counts on it).
* Every agent-controlled string shown in the UI (command text, step text,
  section names, plan notes) goes through `escapeHtml`. The CSP allows inline
  scripts, so escaping is the real XSS boundary.

### 2.6a Security review gate (agreed with the security work, 2026-11-07)

* **F3 and F4 change what can run without the operator's approval.** Before
  either merges into `feature-roadmap`, the feature reviewer approves it and then
  hands the branch to the **security reviewer** (the `security-hardening` work)
  for a second review. Both reviews are recorded under the chunk's Reviewer notes.
  Same for F10 and F11.
* **One notion of "read".** Security Chunk H (`master@5c0045f`) made the generic
  integration read tools truly read-only: `core/tool_runner.py`
  `_read_only_refusal` (HTTP: GET/HEAD only) and `core/ha_ws_policy.py`
  `is_read_command` (HA WebSocket: deny rules first, reviewed allow lists, an
  unknown command is not a read). New work must reuse these, never re-decide them:
  * Any feature that auto-runs an **integration** call as a read calls the Chunk H checks.
  * F4 (SSH read lane) is the SSH counterpart. It must follow the same design:
    deny rules win, reviewed allow lists (F1's `cmd_rules.py`), fail closed,
    and one entry point, `core/read_policy.py`, that exposes both
    `is_read_integration_call(tool, transport, args)` (a thin wrapper over the
    Chunk H functions, no new logic) and `is_read_ssh(breakdown)` (F4). F4's
    eligibility code calls only `read_policy`, and the refusal/audit shape
    matches Chunk H's (`reason_code`, an audit event, no payload values in the log).

### 2.7 Style

* Python: match the surrounding code; **no new third-party packages**.
* JS: plain ES5-style like `app.js`; no build step, no frameworks.
* Bash (F5 only): keep `set -eo pipefail`, quote every expansion, no new
  dependencies beyond `bash`, `curl`, `python3`, `coreutils`, `systemd`. Gateways
  include a Raspberry Pi 1: no extra `python3` start on the hot path. The
  gateway runs as root via `sudo`; temp files only via `mktemp -p /run/eshu`
  (the private 0700 work dir the poller creates — refuse if it is missing or a symlink).
* No drive-by refactors, renames or reformatting.

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

### Reviewer notes

_(reviewer)_

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

### Reviewer notes

_(reviewer)_

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
- [ ] Reviewer does a dedicated security pass on the approval path (atomic use counting, session binding, ordering in § 2.6) and records it under Reviewer notes.
- [ ] **Security reviewer sign-off** (§ 2.6a) recorded before merge.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

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
* `core/read_policy.py` (§ 2.6a): `is_read_ssh(breakdown)` holds the SSH read rules; `is_read_integration_call` wraps Chunk H.
* `core/read_lane.py` `eligible(breakdown, verdict) -> (bool, reason)`: gateway state, size and blocklist checks, then `read_policy.is_read_ssh`. No read rules of its own.
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
- [ ] Reviewer does a dedicated security pass on `cmd_rules.py` and `read_lane.py` (try to find a read-classified command that writes, executes or leaks a secret) and records it under Reviewer notes.
- [ ] **Security reviewer sign-off** (§ 2.6a) recorded before merge; `read_lane.py` goes through `core/read_policy.py` only.
- [ ] Rollout note: at least 7 days in `shadow` on at least 2 gateways before
      `on` is offered in the UI (the toggle shows "on" disabled until that gateway has ≥ 7 days of shadow data).

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunk F5 — Execution results (exit code + output tail) · slug `exec-results` · gateway + dashboard

> **Two-step chunk.** First push a branch with **only** a design note under
> Implementer notes: the exact bash for capturing the output tail and posting
> it, how it interacts with `run_sanitized`, `set -eo pipefail`, the
> `[ESHU exit=N]` sentinel and the JSON-body rules at the top of
> `eshu-gateway.sh`, and the temp-file handling (§ 2.7). Wait for the reviewer
> to mark the design **approved** in Reviewer notes, then implement on the same branch.

### Goal

Record what actually happened for every executed SSH command: exit code,
duration, and the last part of the (already sanitized) output.

### Design

* Gateway: `run_sanitized` keeps streaming to the agent unchanged and, in
  addition, tees the sanitized stream into a private temp file (created
  with `mktemp -p /run/eshu`, removed on exit via `trap`), capped at 16 KiB (keep the **tail**:
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

### Reviewer notes

_(reviewer)_

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

### Reviewer notes

_(reviewer)_

---

## Chunk F7 — Inner target attribution · slug `inner-target` · dashboard

### Changes

* `requests.inner_target` column (`pct:103`, `docker:sonarr`, `qm:201`), filled
  at request/log time from `breakdown.inner_targets` (first one; `multi` if several).
* History filter and a chip on rows; Statistics "busiest targets" counts inner targets.
* Optional: map `pct:<id>` to a gateway hostname when the Proxmox integration
  knows it (read-only lookup, cached; skip if not configured).
* When the operator opens a **host override** on a gateway whose recent
  history (30 days) has `pct:`/`qm:` inner targets, the override dialog shows:
  "This host reaches N containers/VMs via pct/qm — an override here also
  covers commands run inside them." (Warning only; no behaviour change.)

### Tests

* Fill-in for each wrapper type; `multi`; none.
* The override-dialog warning appears only when inner targets exist (API field test).

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

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

### Reviewer notes

_(reviewer)_

---

## Chunk F9 — Freeze covers integrations + HA Supervisor reads · slug `ha-supervisor-read` · dashboard

### Why

Home Assistant OS has a Supervisor (add-ons, backups, updates, host/OS info).
Eshu's HA integration cannot see it today, but HA Core already forwards it over
the WebSocket API with the `supervisor/api` command
(`homeassistant/components/hassio/websocket_api.py`: `@require_admin`, schema
`endpoint: str, method: str, data?: dict, params?: dict, timeout?`). **HA applies
no endpoint allowlist:** any admin token can call every Supervisor endpoint,
including host reboot, backup restore and add-on options. Eshu must therefore
own the allowlist. Endpoints are fixed in Eshu's tool definitions; an agent never supplies one.

Separately, **Emergency Freeze does not stop integration calls today** (only
SSH gateways). That has to change before Eshu gets more power over HA.

### Changes

* **Freeze covers integrations.** In `core/tool_runner.py`, while the freeze
  flag is set, every **mutating** integration call (and every approval of a
  pending integration call in `main.py`'s approve route) is refused with
  `{"error": {"code": "frozen", …}}`, plus an audit event. Reads keep working (an
  operator investigating an incident needs them). The UI's freeze banner text
  says "SSH and integration writes".
* **Supervisor reads** — new WS tools in `core/ha_seed.py`, all `read_only`,
  implemented as handlers (`core/tool_handlers.py`) that call
  `ha_ws_request(integration, 'supervisor/api', {'endpoint': E, 'method': 'get'})`
  with `E` from a constant table in a new `core/ha_supervisor.py`:
  `ha_supervisor_info` (`/supervisor/info`), `ha_core_info` (`/core/info`),
  `ha_os_info` (`/os/info`), `ha_host_info` (`/host/info`),
  `ha_addons` (`/addons`, compact: slug, name, state, version, update_available),
  `ha_addon_info(slug)` (`/addons/{slug}/info`, slug validated `^[a-z0-9_]{1,64}$`;
  drop `options` from the output — they hold add-on secrets),
  `ha_backups` (`/backups`, compact), `ha_resolution` (`/resolution/info`).
* `ha_check_config` — REST `POST /api/config/core/check_config` (read-only in effect; it
  validates and changes nothing). Mark it `read_only`.
* Every response goes through `secret_scrub`. A `401/403` from `supervisor/api`
  becomes a clear error: "the HA token is not an admin token. Supervisor tools need an admin user."
* `docs/INTEGRATIONS.md`: the HA section says which tools need an admin token.
  It also recommends (as a setup step, not enforced) a **dedicated HA admin user
  for Eshu** (e.g. `eshu`) whose long-lived token is used by the integration, so
  HA's logbook attributes agent actions to it and the token can be revoked
  without touching the operator's own login. Note in the docs that a
  long-lived token carries all of its user's rights. The operator's current
  token comes from their own (admin) login.
* `ha_whoami` (read-only): WS `auth/current_user` → `{name, is_admin, is_owner}`,
  so the operator (and the Supervisor tools' error messages) can confirm which
  user the token belongs to.

### Tests to add

* Freeze: a mutating HA call and an approval of a pending integration call are
  refused while frozen; reads still run; audit rows written.
* Each Supervisor tool sends exactly the fixed endpoint and method (stub `ha_ws_request`);
  a slug with `/`, `..` or `?` is rejected before any call.
* `ha_addon_info` output never contains `options`.
* Non-admin error mapping.
* **Allowlist test:** the set of endpoints any F9/F10 tool can send equals the
  constant table. Fails when someone adds an endpoint without updating it.

### Compat impact

Additive tools. Freeze now also blocks integration writes, which is a behaviour change. CHANGELOG entry.

### Acceptance criteria

- [ ] No code path can send an endpoint that is not in `ha_supervisor.py`'s table.
- [ ] Freeze blocks integration writes and approvals; reads unaffected.
- [ ] Seeded tools appear after "seed" on an existing HA integration without duplicates.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunk F10 — HA Supervisor actions (approval-gated) · slug `ha-supervisor-actions` · dashboard

### Changes

> Before coding, confirm each payload shape against the HA Core source
> (`components/hassio/websocket_api.py`, `components/hassio/update.py`) and the
> Supervisor API docs. The reviewer has only verified `supervisor/api`'s schema. Record what you
> checked in Implementer notes.

* Mutating tools (`read_only: False`, `always_gate: True`, so each call lands in
  the Pending API Approvals queue), via the same `ha_supervisor.py` table:
  * `ha_addon_action(slug, action)`: `action ∈ {start, stop, restart}` → `POST /addons/{slug}/{action}`.
  * `ha_addon_update(slug, backup=true)` → WS `hassio/update/addon` `{addon, backup}`.
  * `ha_core_update(backup=true)` → WS `hassio/update/core` `{backup}`.
  * `ha_backup_create(name, partial_addons?, partial_folders?)` → `POST /backups/new/full` or `/backups/new/partial`.
  * `ha_core_restart()` → refuses unless an `ha_check_config` in the last 10 minutes
    on this integration returned `valid` (record the last result in `meta`);
    then `POST /core/restart`.
  * `ha_host_reboot()` → `POST /host/reboot`: **seeded disabled** (the tool toggle
    is off by default), and its approval card shows a red "reboots the whole HA host" line.
* **Never exposed** (and listed in a `FORBIDDEN` table that a test checks against):
  backup restore (`/backups/*/restore/*`), add-on options/install/uninstall,
  `/supervisor/options`, `/host/shutdown`, `/store/repositories`, `/auth*`,
  `/security*`, `/os/datadisk*`, `/os/boot-slot`.
* **Preflight** (`PREFLIGHT` in `tool_handlers.py`) validates arguments when the call
  is queued, so the approval card shows the exact action, the add-on name and the
  current → target version for updates (from `/addons/{slug}/info` / `/core/info`).
* **Existing `call_service` loophole:** `call_service(domain='hassio', …)` reaches
  the same powers outside this allowlist. When F10 is installed, `call_service`
  refuses domain `hassio` (error points to the F10 tools), unless an agent
  policy explicitly allows `hassio` (operator escape hatch, audited).
* Freeze (F9) blocks all of these.
* Plans (F3): add plan item type `mcp` `{integration, tool, args_hash}` so an
  approved plan can cover "backup → update → check → restart". Matching is by exact
  argument hash, the same rule as SSH items. If F3 has already merged, this is a
  small extension in F10; if not, F3 includes it.

### Tests to add

* Each action builds the right endpoint and payload; unknown actions and bad slugs refused.
* `ha_core_restart` refuses without a recent valid check; works after one.
* `ha_host_reboot` is seeded disabled.
* `FORBIDDEN` endpoints are unreachable from every tool (table test).
* `call_service` with `hassio` refused by default; allowed only with an explicit policy.
* Preflight populates the approval summary; plan item of type `mcp` approves exactly one matching call.

### Acceptance criteria

- [ ] Reviewer security pass: try to reach a forbidden endpoint through any F9/F10 tool or `call_service`.
- [ ] Every action is approval-gated and audited; freeze blocks them.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunk F11 — HA config files via the SSH add-on · slug `ha-config-files` · dashboard

> **Design first,** like F5: push a design note only, wait for approval, then implement.

### Why

YAML-only integrations need edits to `/config/*.yaml`, and no HA API writes
config files. The supported route is the **Advanced SSH & Web Terminal** add-on.
It cannot be an Eshu gateway: the add-on is an Alpine container without systemd
(no poller, logger or `systemd-run`) that is rebuilt on every add-on update. So
the **dashboard itself** connects to it.

### Design (to be confirmed in the design note)

* A new integration kind `ha_ssh` (or an "SSH access" section on the HA
  integration): host, port, username, and a **private key made only for this
  use**, stored like other integration secrets (masked, scrubbed). The host key
  is pinned on first use (TOFU) and shown to the operator; a changed key refuses.
* Transport: the system `ssh` client via `subprocess` (no new Python package),
  `BatchMode=yes`, `StrictHostKeyChecking=yes` with an Eshu-owned
  `known_hosts`, a temp key file under a 0700 dir, deleted afterwards, and a
  timeout. Commands are **fixed templates**, never agent-supplied shell. Reuse
  the dashboard-side SSH runner built in F12 (`ssh_exec`).
* Tools:
  * `ha_config_list()`: `*.yaml` under `/config` and `/config/packages/**`.
  * `ha_config_read(path)`: allowed paths only (above). `secrets.yaml`
    returns key names with values masked. Refused: `.storage/**`, `*.db*`,
    `.ssh/**`, anything outside `/config`, symlinks (checked on the remote with
    `realpath`). Returns `content` and `sha256`.
  * `ha_config_write(path, content, expect_hash, dry_run)`: mutating, always gated.
    `dry_run` returns a unified diff without approval. On approval:
    (1) re-check `expect_hash`; (2) copy to `<path>.eshu-<ts>.bak`;
    (3) write via stdin to a temp file and `mv` it into place; (4) run `ha core check`.
    (5) **If the check fails, restore the backup automatically** and return the
    check output. The approval card shows the diff (F2's card renders it).
  * `ha_config_restore(path, backup)`: gated; restores a `.eshu-*.bak`.
  * `ha_core_restart` from F10 also accepts a passing `ha core check` from this transport.
* Freeze blocks writes and restores. Results (output, exit code) are stored like F5's.
* Setup doc: create an Eshu-only key pair, add the public key to the add-on's
  `authorized_keys`, and keep "Protection mode" **on** (with it off, the add-on can control Docker = the whole host).

### Tests to add (outline; finalised in the design note)

* Path allowlist and refusals (including `..`, symlinks, `secrets.yaml` masking).
* Hash mismatch refuses; failed check restores the backup (stub the ssh runner).
* Changed host key refuses; key file permissions and cleanup.
* Command templates are built with `shlex.quote` and never include agent text unquoted.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunk F12 — SSH through Eshu over MCP (`ssh_exec`) · slug `ssh-exec` · dashboard (+ small gateway change)

> **Design first** (push a design note only, wait for approval). Needs the
> **security reviewer's** sign-off (§ 2.6a): it gives the dashboard a key that reaches every gateway.

### Why

Today every agent that uses SSH logs in as `eshu-gateway` with **one shared key
pair**. Eshu cannot tell agents apart on SSH, cannot give them different SSH
rules, and cannot revoke one without rotating the key everywhere. MCP already has
per-agent tokens and policies. Routing SSH through MCP gives one identity per
agent for both surfaces, and lets MCP-only clients (LM Studio, F17) use gateways
without an SSH client or key.

### Design (to be confirmed in the design note)

* **MCP tool** `ssh_exec(host, command, session_id?, timeout?)`, available to agents per policy.
  * `host` is a gateway IP or hostname known to Eshu (no arbitrary hosts).
  * The agent's own policy applies first (new SSH rules in `agent_policies`,
    e.g. `ssh_hosts_allow`, `ssh_mode: read_only | approval | normal`). A
    `read_only` agent's non-read command (per `core/read_policy.is_read_ssh`, § 2.6a)
    is sent to JIT, never auto-run.
* **Delivery:** the dashboard connects to the gateway as `eshu-gateway` with a
  **dashboard-only key**, using the dashboard-side SSH runner (system `ssh` via
  `subprocess`, `BatchMode`, pinned host keys, private 0700 key dir, timeouts). F11 reuses this runner.
  * The installer adds the dashboard key to `authorized_keys` with the same forced
    command **and** `from="<dashboard IP>"`.
  * The command carries signed prefix metadata `ESHU_AGENT=<id>` +
    `ESHU_AGENT_SIG` (HMAC like `ESHU_SESSION_SIG`, covering agent id, session id
    and a timestamp). The gateway validates it with the same charset rules as the
    other prefixes and passes `agent` to `/api/request` and `/api/log`. Requests
    gain an `agent` column, so history, stats and overrides can be per agent.
  * Every gateway check still runs (freeze, blocklists, allowlists, Zero-Trust, JIT).
    The policy is enforced twice: on the dashboard (agent policy) and on the gateway.
* **Approvals:** an MCP call cannot hang for minutes. If the gateway answers
  "JIT request #N sent", `ssh_exec` returns `{status: "pending", request_id: N}`
  straight away and the agent polls `check_request(N)`. Once approved, the
  agent calls `ssh_exec` again (the ticket is waiting), or a `wait: true`
  option polls for up to 60 s.
* **Output:** stdout/stderr (already sanitized by the gateway) come back in the
  MCP result, capped (default 64 KiB, tail kept) and also stored as an F5 result.
* **Migration:** direct SSH keeps working. A per-gateway setting
  `ssh_mode: direct+mcp | mcp_only` lets the operator retire the shared agent key
  from a gateway once its agents use `ssh_exec`.

### Tests to add (outline; finalised in the design note)

* Agent policy refusals and `read_only` mode; unknown host refused.
* Prefix signing and validation (gateway sandbox); a forged or unsigned
  `ESHU_AGENT` is refused; `from=` restriction present in generated `authorized_keys`.
* Pending → `check_request` → re-run flow; output cap; timeout.
* Requests and audit rows carry the agent; freeze blocks `ssh_exec`.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunk F13 — Session conclusions & runbooks · slug `session-conclusions` · dashboard + MCP

### Why

Sessions already hold the full trail: goal, every SSH command with its outcome
and approval path, every MCP call, and (after F5) outputs and exit codes. What is
missing is the **meaning**: what worked, what didn't, what the fix was. The agent
that did the job knows that best at the end of it. Captured and confirmed, it
becomes knowledge any later agent can retrieve: a strong cloud model (Hermes)
does the reasoning once, and a small local model (F17) reuses it cheaply.

### Changes

* **MCP tool** `session_conclude(session_id, outcome, summary, worked[],
  did_not_work[], root_cause?, fix?, follow_ups[], tags[], runbook_candidate)`.
  * `outcome ∈ {solved, partial, failed, abandoned}`.
  * `worked` / `did_not_work` items may reference request ids / call ids from the
    session (`{"ref": "req:2594", "note": "…"}`); Eshu validates that the refs
    belong to that session.
  * Allowed once per session per agent (a second call replaces the draft until
    the operator confirms it). `session_summary` reminds the agent to call it.
* **Storage:** `session_conclusions` table (session id, agent, fields above, `status:
  draft | confirmed | rejected`, operator note, timestamps), with an SQLite **FTS5**
  index over summary, root cause, fix, tags and the session goal (no new dependency).
* **Operator review** in the session modal (F6 card): shows the conclusion next
  to the evidence (commands, outcomes, outputs), with Confirm / Edit / Reject.
  Only **confirmed** conclusions are served to agents. Agent text is untrusted,
  escaped in the UI, and never executed.
* **Runbooks:** a confirmed conclusion with `runbook_candidate: true` can be
  promoted to a **runbook**: title, when to use it (symptoms/tags), ordered
  steps, each step either a reference to a command/tool call that worked in the
  session (copied verbatim, with its F1 breakdown) or a short instruction, plus
  "known dead ends" from `did_not_work`. Runbooks are documents for agents
  to follow, not executable. F16 can turn one into a recipe.
* **MCP tools for retrieval:** `find_runbooks(query, tags?)` and
  `similar_incidents(query)` → top 5 confirmed items, compact (≤ 1,500 tokens
  each), with ids; `get_runbook(id)`.
* **Secrets:** conclusions and runbooks pass through `secret_scrub` on write and on read.

### Tests to add

* Refs must belong to the session; draft → confirmed flow; only confirmed are retrievable.
* FTS search ranking smoke test; scrubbed secrets; XSS escaping of agent text.
* Promotion copies steps verbatim and records the source request/call ids.

### Implementer notes

_(fill in)_

### Reviewer notes

_(reviewer)_

---

## Chunks F14–F17 — Local-model support · outline (detailed before they start)

Goal: let a small local model (e.g. a 14–20B model in LM Studio on a 16 GB GPU)
handle routine homelab support by moving the intelligence to **authoring time**:
a strong model or the operator works something out once, Eshu stores it as
reviewed building blocks, and the local model picks one and fills in parameters.

* **F14 — Inventory (`whereis`)** · read-only. One cached lookup joining what Eshu
  already knows (gateways, Proxmox guests, Omada clients, NPM hosts, Pi-hole
  records, HA) → host, container id, IP, URL, FQDN, proxy entry, gateway present.
  Refreshed periodically; read through the Chunk H read-only checks.
* **F15 — Diagnostic probes** · read-only. Reviewed bundles
  (`diagnose_service(host, unit)`, `diagnose_container`, `disk_pressure(host)`,
  `dns_check(name)`) that run reads through `ssh_exec` (F12) and the read lane (F4)
  and return a structured digest of 1–2k tokens: status, de-duplicated recent errors,
  restarts, ports, disk, recent changes from Eshu's own history.
* **F16 — Recipes** · **design first + security reviewer sign-off.** Named,
  versioned procedures of steps (Eshu tool calls or SSH command templates on named
  hosts), with **typed parameters** (hostname, IPv4, port, slug: validated by
  regex, shell-quoted, never free text into a shell), a check after each
  step, and optional undo steps. Drafted by Hermes (often from an F13 runbook),
  approved once by the operator (pinned to the content hash; any edit is a new
  version), run with `recipe_run(name, params)`: one approval per run, or none
  for recipes that only read. Example: `add_internal_fqdn(fqdn, target_ip, port)` →
  Pi-hole record + NPM proxy host + `dig` and `curl` checks.
* **F17 — Local-agent profile** · an agent-policy preset and setup guide for LM
  Studio's MCP client (URL + bearer header in its `mcp.json`): its own token, a
  small toolset (`whereis`, probes, runbooks, recipes, generic reads,
  `ssh_exec` in `read_only` mode), short tool descriptions, writes gated, and an
  `escalate(summary)` tool that notifies the operator and attaches the session
  digest for Hermes to pick up.

---

## 7. Backlog (not planned in detail)

* Deny with a reason, shown to the agent (cheap; low value given the data).
* Multiple SSH keys tagged per agent (superseded by F12, kept only as a fallback idea).
* Gateway tags, with policy / freeze / Fleet Run per tag.
* Approve/deny from a phone notification (Web Push via the existing PWA, or ntfy) —
  needs a security design for authenticating the notification action.
* Config export/import (policies, agent policies, windows, integration definitions without secrets).
* Split `main.py` into FastAPI routers; build installers in CI instead of committing copies.
* Agent-facing read-only "would this auto-run?" tool (lower value: commands rarely repeat).

---

## 8. Rollout gate

The implementer never deploys. The reviewer runs this gate (same shape as `docs/security/HARDENING_PLAN.md` § 8):

1. The security rollout (chunks 1–5 + R) is already live. Security chunks 6–7
   are paused; they resume after this plan's rollout.
2. Deploy dashboard-only chunks (F1, F2, F6, F7, F8) together. F3 and F4 each
   need a separate deploy note in the CHANGELOG ("new approval path").
3. F4 ships with every gateway `off`; the operator moves gateways to `shadow` by hand.
4. F5 follows the gateway pipeline: Seed Edge → one dev gateway → soak ≥ 24 h → Fleet.

---

## 9. Status

| Order | Chunk | Slug | Area | Status | Branch | Depends on | Reviewer notes |
|---|---|---|---|---|---|---|---|
| 1 | F1 | breakdown | dashboard | **Ready** | `feat/f1-breakdown` | — | |
| 2 | F2 | review-card | UI | Ready (after F1) | `feat/f2-review-card` | F1 | |
| 3 | F7 | inner-target | dashboard | Ready (after F2) | `feat/f7-inner-target` | F1 | |
| 4 | F3 | session-plans | dashboard + MCP | Ready (after F7) | `feat/f3-session-plans` | F2 | + security reviewer sign-off |
| 5 | F4 | read-lane | dashboard | Ready (after F3) | `feat/f4-read-lane` | F1, F3, Chunk H | reviewer pass + security reviewer sign-off |
| 6 | F5 | exec-results | gateway + dashboard | Ready (after F4) — design first | `feat/f5-exec-results` | — | design approval needed |
| 7 | F12 | ssh-exec | dashboard + gateway | Ready (after F5) — design first | `feat/f12-ssh-exec` | F4, F5 | design approval + security reviewer sign-off |
| 8 | F6 | session-wrapup | dashboard | Ready (after F12) | `feat/f6-session-wrapup` | F3, F4, F5 | |
| 9 | F13 | session-conclusions | dashboard + MCP | Ready (after F6) | `feat/f13-session-conclusions` | F5, F6 | |
| 10 | F8 | test-traffic | dashboard | Ready (after F13) | `feat/f8-test-traffic` | — | low priority |
| 11 | F9 | ha-supervisor-read | dashboard | Ready (after F8) | `feat/f9-ha-supervisor-read` | — | token is now a dedicated `eshu` HA admin user |
| 12 | F10 | ha-supervisor-actions | dashboard | Ready (after F9) | `feat/f10-ha-supervisor-actions` | F9, F3, Chunk H | reviewer pass + security reviewer sign-off |
| 13 | F11 | ha-config-files | dashboard | Ready (after F10) — design first | `feat/f11-ha-config-files` | F10, F2, F5, F12 (runner) | design approval + security reviewer sign-off |
| 14 | F14 | inventory | dashboard | Outline — detail before start | `feat/f14-inventory` | Chunk H | |
| 15 | F15 | probes | dashboard | Outline — detail before start | `feat/f15-probes` | F4, F12 | |
| 16 | F16 | recipes | dashboard + MCP | Outline — design first | `feat/f16-recipes` | F3, F12, F13 | design approval + security reviewer sign-off |
| 17 | F17 | local-agent | dashboard + docs | Outline — detail before start | `feat/f17-local-agent` | F12–F16 | |
Chunks are done **in the order column**; each new chunk branches from the
updated `feature-roadmap` after the previous one is merged.
