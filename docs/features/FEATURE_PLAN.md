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
| X1 | Bug fix: the sidebar's Approve/Deny work for every kind of request, and approve/deny only act on a request that is still pending |
| F18 | The sidebar shows context from the database: this session's earlier commands, how often this command (or ones like it) ran before and what you decided, the host's state, and why it is waiting for you |
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
| F19 | Commands that run with nobody watching (read lane, probes, read-only recipes) get a hard time limit and output cap on the gateway |

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
     "elevated": false,          // sudo / su seen in the wrapper chain
     "input_redirects": [        // F4 SC3: `<`, `<<`, `<<<`, `<&` (omitted when none)
       {"op": "<", "target": "/etc/shadow"}]},
    ...
  ],
  "sections": ["LOGS", "DISK"],
  "summary": {"steps": 9, "read": 8, "write": 1, "unknown": 0},
  "headline": "9 steps · 8 read · 1 write: systemctl restart nginx",
  "inner_targets": ["pct:103"],  // for F7
  "flags": ["redirect-to-file", "heredoc", "input-redirect", "encoded", "nested-ssh", "sensitive-path"],
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
- [ ] Reviewer's `breakdown_report.py` run on the live DB copy (SSH rows only, see R11):
      ≤ 20% of steps `unknown`, and every `unknown` in the top 20 is either justified
      (interpreter code, `curl`, site-specific CLIs, user-defined functions) or added to the rules.
      *(Revised in review round 1 from 15%: the first measurement showed the remaining
      unknowns are mostly legitimately opaque.)*
- [ ] Not one fixture contains real hostnames, IPs or secrets.
- [ ] No behaviour change: `/api/policies/test`, `/preview` and the gateway are untouched.

### Implementer notes

**What shipped**

* `dashboard/core/cmd_rules.py` — the verb table: `classify(argv) -> (kind, why)`,
  argument-aware for `systemctl`, `journalctl`, `docker`, `apt`, `dpkg`, `find`,
  `sed`, `awk`, `ip`, `git`, `mount`, `sysctl`, `crontab`, `iptables`, … plus the
  sensitive-path patterns. Data-heavy so the security-relevant half can be
  reviewed on its own.
* `dashboard/core/cmd_parse.py` — `breakdown(command)`: shlex tokenising, section
  markers, wrapper unwrapping (depth ≤ 3), encoded payloads, heredocs,
  redirections, `$( … )`/backtick substitutions, shell keywords, flags,
  `inner_targets`, summary and headline. Cached with
  `functools.lru_cache(maxsize=512)`; each call returns a deep copy.
* `dashboard/main.py` — `list_requests` attaches `breakdown` to **pending** rows
  only (next to `risk`/`anomaly`); new `GET /api/requests/{req_id}/breakdown`
  (session auth, 404 on an unknown id). Nothing else changed.
* `scripts/breakdown_report.py` — read-only (`file:…?mode=ro`) aggregates: step
  split, fully-read share, top-20 unknown verbs; `--show-unknown N` prints
  examples and is explicitly local-use only.
* `tests/test_cmd_parse.py` + `tests/fixtures/commands/*.txt` — a 94-case table
  over 10 fixture files, plus property, risk-consistency, cache, performance,
  report and API tests.

**Surprises and decisions worth a look**

1. **shlex punctuation runs.** `shlex` hands back `);`, `))` and `&&` as single
   tokens, so a closing parenthesis never matched. `_normalise()` splits runs
   into single operators before paren matching and statement building.
2. **Encoded payloads need a long blob.** `cmd_codec.decode_preview` only looks
   at base64 runs of ≥ 16 characters, so `echo dXB0aW1l | base64 -d | bash`
   (8 chars) is correctly "could not decode" and becomes one `unknown` step.
   The fixtures use realistic multi-line payloads.
3. **Loop headers.** `for …; do …; done`, `case … esac` and `if …; then` are
   structure, not commands: `for`/`case` headers are dropped (substitutions
   inside them are still parsed), `if`/`while` keep their condition.
   `pct exec $i …` with a variable id stays `unknown`, because the chunk's
   `pct exec <id>` needs a literal id and expanding loop variables is a parser
   feature the chunk does not ask for.
4. **A `case` body must not hide behind its header.** `case x in a) rm …` used
   to reach the parser as one argv (`)` was dropped), so the `rm` vanished into
   the dropped header. `)` is now a soft statement boundary; regression case
   `loops.txt::case-block`.
5. **Risk-map agreement.** `cmd_risk.get_cmd_risk` has false positives
   (`\brm\b` also matches `cat /tmp/rm`), so a step the risk map flags is
   never reported `read` — it degrades to `unknown`. That makes the chunk's
   consistency test true by construction.
6. **Cache key.** The chunk says "LRU keyed by `sha256(command)`
   (`lru_cache` on the command string)". I used `lru_cache(maxsize=512)` on the
   command text itself: hashing first would add a copy and lose readability for
   no gain.
7. **Verb table additions beyond § F1's READ list.** The acceptance criterion
   says unknown verbs are "justified or added to the rules", so I added
   read-only/recon verbs that were missing: `pwd printenv env false sleep seq
   groups getent lsof pgrep pidof lsns tty w who users last sha256sum sha1sum
   md5sum cksum diff cmp tree column rev paste comm lsmod modinfo locale
   lsb_release dmidecode lshw nvidia-smi pveversion vcgencmd systemd-analyze`,
   the session-local builtins `cd pushd popd export unset set shift umask`, and
   argument-aware `hostnamectl`/`timedatectl` plus `systemctl --failed` (a
   subcommand-less `systemctl` is a listing). All are read-only or only change
   the shell's own state; drop any you disagree with and the table says which
   fixtures move.
8. **`docker stats` without `--no-stream` is `unknown`** (it streams forever),
   and `sed 's/x/y/e'` / `awk system(…)` are `unknown` rather than `write`,
   because they execute rather than write.
9. **`<<-EOF`** arrives from shlex as `<<` + `-EOF`; the dash is re-attached so
   a tab-stripped body is still skipped. A heredoc whose closing delimiter never
   arrives sets `complete: false` — we cannot tell which lines the shell runs.
10. **Compatibility.** Additive only: one new JSON field on pending rows and one
    new GET. Old gateways are untouched; `/api/policies/test`, `/preview` and
    the gateway scripts are unchanged.

**Deliberately not done**

* No F2 UI, no gateway change, no read-lane/plan plumbing (F3/F4 own those), no
  new dependencies, no loop unrolling, no change to `cmd_risk`, policy or
  blocklist behaviour.
* `docs/TOOLSHED_PLAN.md` and `scripts/eval_tool_discovery.py` were already
  untracked in the working tree when I started; they are **not** part of this
  branch.
* The reviewer's live-database run of `scripts/breakdown_report.py`
  (≤ 15 % unknown steps) is still to do — it needs the database copy.
* Heads-up for the reviewer: running `bash scripts/verify.sh` leaves
  `dashboard/static/dev/eshu-gateway-install.sh` modified. `tests/test_golden.py`
  exercises `POST /api/dev/seed`, which copies the golden installer over the dev
  copy. That is a pre-existing side effect, unrelated to F1; I reverted it and
  committed nothing under `static/dev/`.

**Tests**

94 synthetic cases (10 fixture files) plus property, risk-consistency, cache,
performance, report and API tests: **321 tests added** (baseline 1343).
`bash scripts/verify.sh`:

```
1664 passed, 7 warnings in 339.63s (0:05:39)
```

**Round 2 — 2026-11-07 — round 1 addressed.**

All eleven items are fixed on this branch. Every item has a regression test
that failed on the reviewed commit and passes now; they live in
`tests/test_cmd_parse.py::TestReviewRound1`. The direction is now closed the
other way: a step is never `read` (and never disappears) where the shell would
write or execute.

| # | Done | How | Test |
|---|---|---|---|
| R1 | yes | `shlex` runs with `commenters=''`; `_strip_comments()` removes `#` only at a word start outside quotes and copies heredoc bodies verbatim (so `# $(…)` in a body still counts). | `test_f1_r1_hash_inside_word_is_not_a_comment` |
| R2 | yes | `_scan_heredocs()` reads the raw text before lexing and records each heredoc's delimiter quoting and body; statements carry the spec. Unquoted bodies are scanned for `$(…)`/backticks and parsed with context `heredoc` (quoted ones stay data). Redirect targets and here-string words are scanned too. | `test_f1_r2_heredoc_body_substitution`, `test_f1_r2_quoted_heredoc_body_is_data`, `test_f1_r2_herestring_and_redirect_target_substitution` |
| R3 | yes | `<( … )`/`>( … )` become one word atom; `_strip_substitutions()` reports `procsubst`/`procsubst-out`; inner steps parse with context `procsubst`, the outer kind is the worst of its own and the inner kinds, and an output sink floors it at `write`. `complete` is no longer `false`. | `test_f1_r3_process_substitution` |
| R4 | yes | `_sed_inplace()` walks short-flag clusters (`-Ei`, `-ni`, `-ri`, `-si`, `-i.bak`) and stops at value-taking letters `e`/`f`/`l`; `_sed_has_script_file()` handles `-f`/`--file` (→ `unknown`); `_sed_script_risk()` parses `s` commands with any delimiter plus their trailing flags (`e` → `unknown`, `w`/`W` → `write`), and every `-e` expression is scanned. | `test_f1_r4_sed_combined_flags_and_scripts` |
| R5 | yes | `DANGEROUS_ENV_NAMES`/`_PREFIXES` + `dangerous_env_name()`; `_dangerous_env_change()` checks leading assignments and `env` arguments at every unwrap level (so `sudo env …` is covered); `export`/`unset`/`set` left `READ_VERBS` and got handlers (`_declaration`, `_shell_state`, `_read_builtin`, `_printf`). A bare dangerous assignment now emits an `unknown` step instead of vanishing. Safe assignments stay `read`. | `test_f1_r5_dangerous_assignments`, `test_f1_r5_state_builtins_are_unknown` |
| R6 | yes | `resolve_verb()` + `SYSTEM_BIN_DIRS`; `classify()` refuses non-system paths, `_unwrap()`/`_has_wrapper()`/shell detection refuse to unwrap a non-system `sudo`/`timeout`/`env`/`sh`, and a non-system `echo` is no longer a section marker. | `test_f1_r6_path_qualified_verbs` |
| R7 | yes | awk: `system (` with whitespace, `print … \| "cmd"`, `@include`, `-f`; find `-fprint0`; git `--output`/`--ext-diff`/`--textconv`; smartctl action flags before read flags; `ip netns exec` unwrapped (context `netns NS`); `sort --compress-program`; `tree -o`; `dmidecode --dump-bin`; `systemd-analyze set-*`; nvidia-smi/vcgencmd allowlists; new `follows` flag for `tail -f`, `journalctl -f`, `docker logs -f`, `dmesg -w`, `docker events`. | `test_f1_r7_awk_exec_and_pipes`, `test_f1_r7_git_output_and_external_diff`, `test_f1_r7_smartctl_actions_win`, `test_f1_r7_ip_netns_exec_is_a_wrapper`, `test_f1_r7_misc_verb_paths`, `test_f1_r7_follow_flag` |
| R8 | yes | `shadow` and `environ` added to `SENSITIVE_HINTS`; `_sensitive_command()` flags `docker inspect`, `docker compose config` and bare `env`/`printenv`. | `test_f1_r8_sensitive_reads` |
| R9 | yes | `_group_sink()` decides: shell sinks (including `sh -s` and `eval`) are decoded into steps as before; interpreter sinks (`python*`, `perl`, `ruby`, `node`) become **one** `unknown` step, why `decoded <lang> script (N lines)`, with the source in a new `script` field; unrecognised sinks keep the outer steps (still flagged `encoded`). Interpreter heredocs (`python3 - <<'PY'`) get the same single step + `script`, capped at 8 KiB. | `test_f1_r9_decoded_interpreter_payload_is_one_step`, `test_f1_r9_python_heredoc_is_one_step` |
| R10 | yes | `:`, `exit`, `return`, `break`, `continue`, `wait` are `read` (why `shell control`); `tar` parses its mode (`t` read, `x`/`c`/`r`/`u`/`A` write, bundled `tf`/`czf`, `--list`/`--extract`); `install` is a write; `runuser` is a wrapper (`-u … -- cmd` and `-l … -c '…'`, context `runuser`, elevated); `_extract_functions()` drops definition headers and records the worst kind of each body, so a later call reports `calls function f` with that kind. Undefined functions stay `unknown`. | `test_f1_r10_control_tar_install`, `test_f1_r10_runuser_is_a_wrapper`, `test_f1_r10_shell_functions` |
| R11 | yes | `scripts/breakdown_report.py` excludes `status LIKE 'integration-%'` by default, `--include-integration` opts back in, and the excluded row count is printed. | `test_f1_r11_report_excludes_integration_rows` |

Three things for the reviewer to know about the new output:

* a step may now carry a `script` field (only the R9 interpreter steps). It is
  additive; F2 can render it collapsed.
* the `follows` flag is new; F4 should refuse it, because such a command never
  ends.
* `>( … )` floors its step at `write` even when the sink itself only reads
  (`ls >(cat)`) — the conservative reading of "at least write-adjacent".

`bash scripts/verify.sh` (all pre-commit checks green):

```
1686 passed, 7 warnings in 339.60s (0:05:39)
```

(1343 baseline + 321 round 1 + 22 round 2.)

**Round 3 — 2026-11-07 — round 2 addressed.**

S1–S7 are fixed on this branch (on top of the merge of `feature-roadmap`, which
brings security Chunk H). Each has a regression test that failed on the
reviewed commit; they live in `tests/test_cmd_parse.py::TestReviewRound2`.

| # | Done | How | Test |
|---|---|---|---|
| S1 | yes | Heredoc bodies are now skipped only when the newline that starts them arrives, and the heredoc attaches to the statement it belongs to — so `cat <<EOF; rm x` and `cat <<EOF && rm x` keep the second command as a `write` step instead of swallowing it. | `test_f1_s1_command_after_heredoc_operator_same_line` |
| S2 | yes | `_scan_heredocs()` collects **every** heredoc operator on the operator line first (`_heredoc_ops`) and then reads the bodies in order, each with its own quoting; `cat <<A <<B` scans both bodies. Quoted/partially quoted delimiters (`'A'`, `E"O"F`, `\EOF`) still mark the body as data. | `test_f1_s2_every_heredoc_body_is_scanned` |
| S3 | yes | `>&` with a non-numeric target joined the write redirects (a digit or `-` is still fd duplication), and `&>>` is parsed as one operator instead of `&>` + `>` — which had lost the target and set `complete: false`. | `test_f1_s3_ampersand_redirects_write_files` |
| S4 | yes | `tar` treats `--checkpoint-action` with `exec`, `-I`/`--use-compress-program`, `-F`/`--info-script`/`--new-volume-script`, `--rsh-command` and `--rmt-command` as `unknown` (`tar runs a program`) before any mode parsing. `--to-command` is left as it was, per the review. | `test_f1_s4_tar_options_that_execute` |
| S5 | yes | The awk program is split into statements on `;`, `{`, `}` and newline outside string literals: a statement starting with `print`/`printf` that contains `>`/`>>` is a `write`, `|` is `unknown`; `c|getline` (no spaces) is caught too. | `test_f1_s5_awk_output_and_command_pipes` |
| S6 | yes | `git status`/`log`/`diff`/`show` stay `read` but set the new `repo-config` flag (they can run repository-configured programs); `git branch` and every other verb do not. | `test_f1_s6_git_reads_carry_repo_config_flag` |
| S7 | yes | `set` is `read` again with why `shell options` (`set -e`, `set -euo pipefail`, `set +x`, `set -- a b`, bare `set`); `unset` of a dangerous name and the other R5 rules are unchanged. The round-1 R5 test was updated because it asserted the old `set` behaviour. | `test_f1_s7_set_is_shell_options` |

Notes for the merge:

* `repo-config` is advisory only (F4 decides whether to refuse); it is not set
  for `git -c` (already `unknown`) or for non-read subcommands.
* The optional accuracy items from the review (`dig`/`host`/`nslookup`,
  `pvesm status|list`) were deliberately left out — they were marked "not
  required for merge".

`bash scripts/verify.sh` (pre-commit green):

```
1703 passed, 7 warnings in 339.88s (0:05:39)
```

(1686 on the branch before this round + 10 from the merged security Chunk H +
7 new S tests.)

### Reviewer notes

**Round 1 — 2026-11-07 — changes requested.**

Good work overall: the structure is clean, the module is total (never raises),
the API hook is pending-only as specified, the report is read-only, the
fixtures contain no real data, and the implementer notes are excellent. I
re-ran the full gate (see the end of this section). The problems below are all in
one direction: **a step is labelled `read` (or disappears) when the shell would
write or execute.** F1 is advisory, but F2 shows operators "all read" and F4
will auto-run on it, so this direction must be closed before merge. Every item
below needs a regression test (`test_f1_r<N>_<what>`) that fails on the current branch.

**Parser differentials (shell runs something the breakdown does not show)**

* **R1 — critical: `#` inside a word is treated as a comment.** `shlex`
  starts a comment at any `#` that begins a token *in its own state machine*,
  which includes mid-word after a path separator or letter run. Example:
  `ls /tmp/#dir; rm f` gives one step, `ls /tmp/`, and the `rm` vanishes; the
  same happens with `echo a#b; rm f`. Bash only starts a comment when `#` begins
  a word (after whitespace or an operator). Fix: set `commenters = ''` and strip
  comments yourself at word starts, outside quotes. Tests: both examples give
  two steps, the second `write`; `ls # rm f` is still one step.
* **R2 — high: substitutions in heredoc bodies, here-strings and redirect
  targets are never parsed.** `cat <<EOF` with a `$( … )` line in the body,
  `cat <<< "$( … )"` and `cat < "$( … )"` all come out as one `read` step, but
  the shell runs the substitution. Fix: for an **unquoted** heredoc delimiter,
  scan the body for `$(`/backticks and parse them as steps (context `heredoc`);
  quoted delimiters (`'EOF'`, `"EOF"`, `\EOF`) keep the body as data. Parse
  substitutions in every redirect target and here-string word.
* **R3 — high: process substitution is not parsed.** `cat <(rm -rf x)` is
  `unknown` only by accident (the redirect loses its target), and
  `diff <(ls a) <(ls b)` comes out as two `read` steps with `complete: false`.
  Parse `<( … )` / `>( … )` as nested steps (context `procsubst`), with the
  outer step's kind the worst of its own and the inner kinds. `>( … )` is
  always at least `write`-adjacent: classify it like a pipe sink.

**Verb table: write/execute paths labelled read**

* **R4 — high: `sed`.** `-i` is missed in combined short flags: `sed -Ei`,
  `sed -ri`, `sed -ni` and `sed -si` are all `read` today (and `-Ei` is a very
  common agent spelling). Treat any short-flag cluster containing `i` as
  in-place. `s` commands with flag letters, `s/x/y/ge` and `s/x/y/gw FILE`, and
  any delimiter (`s#a#b#e`, `s|a|b|w f`) must be caught. `-f/--file SCRIPT`
  → `unknown` (we cannot see the script).
* **R5 — high: environment and shell-state changes that alter later steps.**
  `LD_PRELOAD=… cat f`, `PATH=…:$PATH ls`, `env LD_PRELOAD=… ls`,
  `export PATH=…; ls` and `printf -v PATH …; ls` are all-read today, but each
  can make the following "read" execute arbitrary code. Rule: an assignment
  prefix, an `env NAME=…` argument, or an `export/declare/typeset/readonly/local`
  argument that sets a **dangerous name** (`PATH`, `LD_*`, `BASH_ENV`, `ENV`,
  `IFS`, `SHELLOPTS`, `BASHOPTS`, `PS4`, `PROMPT_COMMAND`, `PYTHON*`, `PERL5*`,
  `RUBY*`, `NODE_OPTIONS`, `GIT_*`) makes that step `unknown`, with why
  `changes how later commands run`. `set`, `unset`, `alias`, `hash`, `enable`,
  `trap`, `source`, `.`, `read`/`printf -v` into a dangerous name → `unknown`.
  Remove `export`, `unset` and `set` from `READ_VERBS` and give them a handler.
  Safe assignments (`FOO=1 ls`, `export LC_ALL=C`) stay `read`.
* **R6 — high: path-qualified verbs.** `classify` uses
  `os.path.basename(argv[0])`, so `/tmp/x/cat f` and `./cat f` are `read`. A
  verb containing `/` is classified by its basename **only** when its directory
  is one of `/bin`, `/usr/bin`, `/sbin`, `/usr/sbin`, `/usr/local/bin`,
  `/usr/local/sbin`; otherwise `unknown` (`runs a binary outside the system
  paths`). Apply the same rule to wrapper detection: a non-system
  `…/sudo`, `…/timeout`, `…/env` is not unwrapped.
* **R7 — medium: verb-specific gaps.**
  * `awk`: `system (` with whitespace, `print … | "cmd"` / `printf … | "cmd"`
    (pipe to a command), `"cmd" | getline` (already), `-f/--file PROG` →
    `unknown`; `-i`/`--include` stays `write`, and `@include` → `unknown`.
  * `find`: add `-fprint0` to the write flags.
  * `git`: `--output`/`--output=` on `log/diff/show` → `write`; `--ext-diff`
    and `--textconv` → `unknown` (they run configured programs).
  * `smartctl`: write/action flags win over read flags — `-s`, `--smart`, `-t`,
    `--test`, `-X`, `--abort`, `-o`, `--offlineauto`, `-S`, `--saveauto`,
    `--set` → `write`. Today `smartctl -a -t long /dev/sda` is `read`.
  * `ip`: `ip netns exec NS cmd…` is a wrapper. Unwrap it (context `netns NS`)
    instead of reporting `write`.
  * `sort --compress-program=…` → `unknown`; `tree -o FILE` → `write`;
    `dmidecode --dump-bin` → `write`; `systemd-analyze set-*` → `write`.
  * `nvidia-smi`: `read` only with no arguments or with `-q`, `-L`, `--query*`,
    `--format`, `-i`/`--id`; anything else → `unknown` (`-r`, `-pl`, `-pm`, … change the GPU).
    `vcgencmd`: `read` only for `measure_*`, `get_*`, `version`, `commands`; else `unknown`.
  * Follow/stream modes (`tail -f/-F/--follow`, `journalctl -f/--follow`,
    `docker logs -f/--follow`, `dmesg -w/--follow`, `docker events`): keep
    `read` but add flag `follows` (F4 will refuse it: such a command never ends).
* **R8 — medium: sensitive reads.** Add `shadow` (for `getent shadow`) and
  `environ` (for `/proc/*/environ`) to `SENSITIVE_HINTS`. Flag
  `sensitive-path` for `docker compose config` (it prints the interpolated
  `.env` values), `docker inspect`, and bare `env`/`printenv` (they print
  secrets inside containers reached via `pct`/`docker exec`).

**Live-data accuracy**

* **R9 — high (accuracy): decoded payloads fed to an interpreter are parsed as
  shell.** `echo <b64> | base64 -d | python3` decodes Python and splits it into
  dozens of shell "steps" (`print`, `import`, `def`, `try:` …). On the live DB
  this alone is ~600 of the ~2,700 unknown steps. Rule: only decode-and-parse
  when the pipeline's sink is a shell (`sh`, `bash`, …, including `sh -s`). For
  `python*`, `perl`, `ruby` and `node` sinks, emit **one** `unknown` step,
  why `decoded <lang> script (N lines)`, and put the decoded text on the step as
  `script` (F2 shows it in a collapsible block). Same for `python3 - <<'PY'`
  heredocs: one step, with the heredoc body as `script`.
* **R10 — medium (accuracy).**
  * Shell control builtins `:`, `exit`, `return`, `break`, `continue`,
    `wait` → `read` (why `shell control`).
  * `tar`: `-t`/`--list` (and the `t` letter in a bundled first argument such
    as `tf`) → `read`; `x`/`c`/`r`/`u`/`A`/`--delete` → `write`.
  * `install` → `write`.
  * `runuser [-u U] [--] cmd…` and `runuser -l U -c '…'` are wrappers like
    `sudo` / `su` (context `runuser`, `elevated: true`).
  * Shell functions: a definition (`f() { … }`, `function f { … }`) is not a
    step itself; its body steps stay. A later call to a function defined in the
    same command gets kind = worst of the body, why `calls function f`.
    Calling an undefined function stays `unknown`.
* **R11 — low: report scope.** The `requests` table also holds MCP calls
  (`status LIKE 'integration-%'`, with tool names as the "command"). Exclude
  them by default (add `--include-integration` to opt in) and print how many
  rows were excluded. Fixtures: prefer `192.0.2.x` over `10.0.0.1` (nit, optional).

**Measured on the live database (read-only, aggregates only, not committed):**
1,365 distinct commands; 8,893 steps: 60.9% read, 7.1% write, **32.0% unknown**;
40.6% of commands fully read. SSH rows only: 31.2% unknown. Top unknown causes:
decoded Python parsed as shell (R9), shell functions defined in agent scripts,
`curl`, interpreter calls, `bash`/`sh` script sinks, `runuser`, `exit`, `tar`.
After R9–R11 I will re-measure against the revised ≤ 20% target.

**Gate re-run by the reviewer:** pre-commit OK; `1 failed, 1663 passed` — the
failure is `tests/test_db_audit.py::test_audit_log_ordering`, a pre-existing
timing flake (second-resolution timestamps vs a 1.1 s sleep under load). It
passes 5/5 in isolation and F1 does not touch the audit code. Not yours to fix
in this chunk. The API hook, the cache (deep copy per call) and the report's
read-only open look correct.

**What to do:** fix R1–R11 on this branch (`feat/f1-breakdown`), one or a few
commits per item, add the regression tests, re-run `bash scripts/verify.sh`,
add a **Round 2** paragraph to Implementer notes (each R item: done / how /
test name), push, and stop.

**Round 2 — 2026-11-07 — nearly there; changes requested (S1–S7).**

All of R1–R11 are fixed and each has a regression test. I re-ran my round-1
probe set (52 cases) and every one now gives the right answer. Performance is
fine (100 kB of pipelines in 0.3 s; 400-deep `$(` and 3,000 parentheses stay
bounded). **Live accuracy now passes:** SSH rows only, 6,558 steps: 75.6% read,
9.3% write, **15.1% unknown** (18.2% since September), under the revised 20%.
The top remaining unknowns are legitimately opaque (`curl`, site-specific CLIs,
interpreters, scripts piped into a shell). A second probe set (68 cases) found
the following. As before, each needs a `test_f1_s<N>_<what>` that fails first.

* **S1 — critical: a command after a heredoc operator on the same line
  disappears.** `cat <<EOF; rm /tmp/x` followed by a body and `EOF` gives one
  `read` step (`cat`), and the `rm` is gone. This is the R1 class again: the
  heredoc scan has to resume parsing the rest of the *operator line* (here
  `; rm /tmp/x`) and skip only the body lines after it.
* **S2 — high: only the first heredoc on a line has its body scanned.**
  `cat <<A <<B` with an unquoted `$( … )` in B's body is `read`. Handle every
  heredoc on the line in order, each with its own quoting. (Partially quoted
  delimiters such as `E"O"F` and `\EOF` are correctly treated as data. Keep that.)
* **S3 — high: `>& FILE` and `&>> FILE` write files.** `ls >& /tmp/x` is `read`
  today: only a digit or `-` after `>&` is fd duplication; anything else is a
  file write (add `>&` with a non-numeric target to the write redirects).
  `ls &>> /tmp/x` is labelled `write` but sets `complete: false`. Parse `&>>` as
  one operator.
* **S4 — high: `tar` options that execute.** `--checkpoint-action=exec=…`,
  `-I PROG` / `--use-compress-program`, `-F` / `--info-script` /
  `--new-volume-script`, `--rsh-command` and `--rmt-command` → `unknown`
  (`tar runs a program`). `--to-command` is already right.
* **S5 — medium: `awk` output and command pipes.** `printf "x" >> "/tmp/x"`
  (a string between `printf` and `>>`) and `c|getline` (no spaces) are `read`.
  Rule: inside the awk program, `|` followed by optional space and `getline` →
  `unknown`. In any statement that starts with `print`/`printf` (statements
  split on `;`, `{`, `}` and newline), a `>`, `>>` or `|` outside a string
  literal → `write` (or `unknown` for `|`). This is deliberately conservative:
  `print ($1 > 2)` becomes `write`, which only costs a prompt.
* **S6 — low: `git` reads that can run repository config.** `git status`,
  `log`, `diff` and `show` can run programs configured in the repository being
  read (for example `core.fsmonitor`), so `git -C /some/dir status` in a
  directory the agent controls is not purely a read. Keep `read` but add flag
  `repo-config`. F4 will refuse it unless the directory is on an allowlist (F4's design).
* **S7 — low (correction of my R5): `set` is too broad.** `set -e`,
  `set -euo pipefail`, `set +x` and `set -- a b` cannot change how later
  commands resolve, so they should be `read` (why `shell options`). Keep
  `unset` of a dangerous name and the other R5 rules. On the live DB this
  removes ~21 needless unknowns.

**Optional accuracy (not required for merge):** `dig`/`host`/`nslookup` →
`read` (network query, why `DNS lookup`); `pvesm status|list` → `read`.

**Gate re-run by the reviewer:** pre-commit OK; `1686 passed` (matches your count).

**What to do:** fix S1–S7 on this branch, re-run `bash scripts/verify.sh`, add a
**Round 3** paragraph to Implementer notes, push, and stop. If S1–S7 are clean
I expect to merge F1 next round.

**Round 3 — 2026-11-07 — approved and merged.**

S1–S7 are fixed, each with a regression test; the R5 test change is justified
(it encoded the `set` behaviour my S7 corrected). I re-ran all three probe sets
(52 + 68 + 35 adversarial cases): every command that writes or executes is now
`write` or `unknown`, and the only all-read results are genuine reads, or reads
carrying the right flag (`sensitive-path`, `follows`, `repo-config`). Where the
engine is wrong, it errs toward caution (a heredoc terminator with a trailing
space is still treated as ending the body, so the next line is reported as a
write). Performance unchanged (100 kB in 0.26 s).

Live accuracy (read-only, SSH rows only): 6,558 steps, 75.9% read, 9.3% write,
**14.7% unknown**; 44.1% of commands fully read. Acceptance criteria met.

Gate re-run by the reviewer: pre-commit OK; `1703 passed`.

Carried forward to F2/F4: render the step `script` field (collapsible) and the
`follows`, `repo-config` and `sensitive-path` flags; F4 refuses `follows`,
`repo-config` (unless allow-listed), `sensitive-path` and `complete: false`.


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
* Render the F1 additions that landed during review: a step's optional
  `script` field (decoded interpreter scripts, capped at 8 KiB) in a collapsed,
  escaped monospace block; and the flags `follows` ("runs until stopped"),
  `repo-config` ("git can run repository-configured programs"),
  `sensitive-path` and `encoded` as chips. `complete: false` shows a
  "could not fully parse; read the raw command" warning.
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

**What I did**

* **`dashboard/static/app.js`** — the breakdown renderer and its four surfaces.
  The helpers are pure (a request row in, an HTML string out):
  `bdHeadlineHtml`, `bdBodyHtml`, `bdStepsHtml`, `bdFlagsHtml`, `bdStepHtml`,
  `bdBlockHtml`, `bdRowHeadlineHtml`.
  * **Headline** above the raw command on the queue hero card, a compact
    one-line copy on every queue row, a "Breakdown" section at the top of the
    context panel (which is the full-width mobile drawer below 768px), and on
    the session-modal SSH rows.
  * **Step list** under the command: grouped by `section`, each step's
    `context` chain as prefix chips (`pct 103`, `sudo`, `decoded`), write and
    unknown steps expanded and highlighted, runs of read steps collapsed to
    "N read steps ▸", the optional `script` field in a collapsed escaped
    monospace block, and `complete: false` shown as *"could not fully parse;
    read the raw command"*.
  * **Flags** as chips: `encoded`, `heredoc`, `nested ssh`, `sensitive path`,
    `redirect`, `runs until stopped` (`follows`) and `repo can run programs`
    (`repo-config`).
  * **Colours** never read as pre-approved: all-read is muted ink (not green),
    a write is amber, an unknown step adds *"read it as written"*.
  * **Raw command** stays available and is collapsed behind a "raw command ▸"
    toggle (and the `X` queue shortcut) when the breakdown has 4+ steps.
  * **Two-press Approve**: for a command over 500 chars that still has an
    unknown step, the first Approve scrolls to and briefly highlights that
    step and says so in a toast; the second press approves. Short or all-read
    commands are unchanged. It lives in `handleAction`, so it covers every
    Approve path for that request (hero, context panel, session modal).
  * **History** rows get a `breakdown` link that fetches
    `GET /api/requests/{id}/breakdown` and expands a row beneath it.
* **`dashboard/static/index.html`** — the two context-panel sections
  (`ctx-breakdown-head`, `ctx-breakdown-steps`) and an `app.js` /
  `style.css` cache-buster bump.
* **`dashboard/static/style.css`** — the breakdown styles in the ink language;
  `scripts/ui_check.py` stays clean (no new `!important`, tokens only,
  nothing below 11px).
* **`tests/fixtures/f2_harness.js` + `tests/test_f2_review_card.py`** — the
  tests load the real `app.js` in a stubbed browser context under node and
  render a fixture pending row, so they assert on the shipped code.

**Surprises and decisions**

* The queue is a hero card plus compact rows, and the context panel becomes a
  full-width drawer below 768px — those *are* the four surfaces the chunk
  names. There is no separate mobile component to change.
* `escapeHtml` is the only escaper, and the test proves it by spying on it:
  step text, section names, every context chip, the decoded `script` and the
  headline are all recorded through it, and an `<img src=x onerror=…>`
  payload is asserted absent from the rendered HTML.
* A history breakdown row has to survive the 3-second table poll. The first
  version appended a `<tr>` once and the next poll removed it; the row is now
  rebuilt from `_bdHistory` on every render (verified by waiting two polls).
* The raw command block joins a multi-line script onto one line — that is the
  pre-existing `.jit-cmd-text` behaviour (3-line clamp), unchanged here. The
  step list is what makes a long script readable.
* A read step's `why` is usually just the verb (`uptime`), so it is hidden
  when it repeats the text; write/unknown whys always show.

**Deliberately not done**

* No backend change: pending rows already carry `breakdown` (F1) and
  `/api/requests/{id}/breakdown` already exists. No schema, API or gateway
  change.
* `describeCmd` / `ctx-human` are untouched, as are the risk line, the anomaly
  line, the decoded preview and the TTL.
* No new mobile-specific component (the existing responsive drawer covers it),
  and no change to the F1 engine or the security work merged from
  `feature-roadmap`.

**Screenshots** (desktop 1440 px, phone 400 px; all synthetic data)

* A 9+ step read-only recon command:
  ![all-read desktop](../screenshots/f2/all-read-desktop.png)
  ![all-read phone](../screenshots/f2/all-read-mobile.png)
* A mixed read/write command (1 kB recon with one `sed -i`; the write step is
  visible without scrolling):
  ![mixed desktop](../screenshots/f2/mixed-desktop.png)
  ![mixed phone](../screenshots/f2/mixed-mobile.png)
* An encoded command (`base64 -d | bash`, decoded steps):
  ![encoded desktop](../screenshots/f2/encoded-desktop.png)
  ![encoded phone](../screenshots/f2/encoded-mobile.png)
* The decoded `script` field (`| python3`), for completeness:
  ![script desktop](../screenshots/f2/script-desktop.png)
  ![script phone](../screenshots/f2/script-mobile.png)
* The context panel (the mobile drawer):
  ![context panel](../screenshots/f2/context-panel-desktop.png)

**Tests**

Eight tests in `tests/test_f2_review_card.py`, all `test_f2_*` — every one
failed on the reviewed commit (the renderer did not exist). They cover the
headline and step list rendering, the mixed write step, the unknown/incomplete
warnings, the flags, the `script` block and the XSS check, plus a static check
that the context-panel markup, the history link and the `X` shortcut exist.

`bash scripts/verify.sh` (pre-commit green):

```
1711 passed, 7 warnings in 341.19s (0:05:41)
```

(1703 before this chunk + 8 new tests.)


**Round 2 — 2026-11-08 — round 1 addressed.**

* **B1 — fixed.** `handleAction(id, action, opts)` now accepts
  `{confirmed: true}`, which skips the two-press check for callers that have
  already made the decision:
  * `confirmSessionOverride()` passes it — the operator has just granted an
    explicit session bypass, so the row it was opened from is cleared.
  * `bulkApprove()` does **not**. It approves everything that does not need
    review, leaves the long-unknown requests **selected**, and shows one
    toast: *"N requests have unknown steps — review and approve them
    individually."* Only the requests actually sent leave the selection.
  * Per-card buttons (hero, queue rows, context panel, History) and the
    session-modal buttons keep the two-press behaviour.
  * Because the queue re-renders on every poll, a kept selection is now drawn
    from `_ccSelectedIds` (checkbox class + `aria-checked`), so it no longer
    looks unselected after the next refresh.
  * Tests: `test_f2_b1_bulk_approve_keeps_unconfirmed_selected`,
    `test_f2_b1_session_override_approves_confirmed`,
    `test_f2_b1_card_approve_needs_two_presses`. The node harness now drives
    `bulkApprove` / `confirmSessionOverride` / `handleAction` with an
    `authFetch` and `showToast` spy, and sets `requestsData` from inside the
    context (it is a `let` in `state.js`, so a plain property assignment from
    outside the VM did not reach the app).
* **N1 — agreed and fixed.** `bdDecodedPreviewHtml()` skips the older DECODED
  block when a step already carries `script`, so the decoded source appears
  once, in the step's collapsed `decoded script` block. Used on the hero card,
  the context panel and the session modal. Test:
  `test_f2_n1_decoded_script_not_duplicated`; `script-desktop.png` /
  `script-mobile.png` were re-shot and now show the single block.

`bash scripts/verify.sh` (pre-commit green):

```
1715 passed, 7 warnings in 341.35s (0:05:41)
```

(1711 before this round + 4 new tests.)

### Reviewer notes

**Round 1 — 2026-11-08 — one fix requested (B1), then merge.**

Solid work. The rendering is pure and escaped throughout (`esc` covers `& < > " '`,
so the attribute uses are safe too), the history row survives the poll, the
headline is never green, the write step is visible without scrolling at both
widths (checked the committed screenshots), and the node-based escaping tests are
a good pattern for later UI chunks. Gate re-run by the reviewer: pre-commit OK,
`1711 passed`.

* **B1 — must fix: the two-press Approve silently drops requests from
  callers that are not a person pressing the button.** `handleAction(id,
  'approve')` now returns early (toast only) for a long command with an unknown
  step. Two existing callers rely on it approving:
  * `bulkApprove()` loops over the selection, then calls `clearSelection()`. A
    selected long-unknown request is skipped, and the selection is cleared, so the
    operator believes it was approved while it stays pending and the agent times out.
  * `confirmSessionOverride()` ("Start Session Override" from a pending row) calls
    `handleAction(ctx.request_id, 'approve')` to clear the row it was opened from;
    that row stays pending.

  Fix: give `handleAction` an options argument, e.g. `handleAction(id, action,
  {confirmed: true})`, which skips the two-press check.
  * Session override path: pass `confirmed: true` (the operator has just granted
    a bypass for this session explicitly).
  * Bulk approve: **do not** auto-confirm. Approve the others, leave the
    long-unknown ones **selected**, and show one toast: "N requests have unknown
    steps — review and approve them individually." Only clear the ones that were sent.
  * Keyboard approve and the per-card buttons keep the two-press behaviour.
  * Tests (node harness): bulk with one long-unknown + one normal request → one
    POST, the long-unknown id stays in the selection, toast shown; session-override
    path → POST sent; single press on the card → no POST, second press → POST.
* **N1 — optional nit: decoded scripts appear twice on the queue card**
  (the new collapsed `decoded script` block on the step, and the existing
  "DECODED" block from `decodedCmdHtml` below the meta line). If the step has a
  `script`, skip `decodedCmdHtml` for that card, or the reverse. Either is fine.

**What to do:** fix B1 (and N1 if you agree) on this branch, re-run
`bash scripts/verify.sh`, add a Round 2 paragraph to Implementer notes, push, stop.

**Round 2 — 2026-11-08 — approved and merged.**

B1 fixed as specified: `handleAction(id, action, {confirmed})`, used only by
`confirmSessionOverride()`; bulk approve sends the rest, keeps the
long-unknown ones selected (now drawn from `_ccSelectedIds`, so they stay
checked across polls) and says why. N1 fixed: the decoded script appears once
(checked the re-shot screenshot). Gate re-run by the reviewer: pre-commit OK,
`1715 passed`.

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

**What I did**

* **DB** (`db/session_plans.py`, tables created from `db/core.py`):
  `session_plans` + `session_plan_items` and the lifecycle
  `proposed → approved | denied → expired | ended`. `create_plan` replaces an
  existing `proposed` plan; `approve_plan` requires the plan to still be
  `proposed`, marks the removed items, clamps the TTL to 1–240 min (default
  60) and ends any other approved plan for the session; `expire_plans` lazily
  expires; `claim_plan_item` selects the matching item and then does **one**
  conditional `UPDATE … SET uses = uses + 1 WHERE id = ? AND removed = 0 AND
  uses < max_uses`, refusing when `rowcount != 1`.
* **Approval path** (`POST /api/request`): the plan check sits after the
  encoded-payload block and before the session override (§ 2.6). It runs only
  with a session id, when the fleet is not frozen and the gateway is not
  Zero-Trust; with strict sessions on, `session_problem(sid)` must be empty
  (the dashboard half of the signature rule — the gateway refuses unsigned ids
  first). A match creates the request as `approved` with
  `reason="session-plan:<plan>:<item>"`, audits `jit_plan_approved` and returns
  `{plan: true, plan_id}`. Matching is byte-exact after trimming whitespace;
  `*` matches any host, an explicit host matches only that gateway.
* **REST**: `GET /api/session-plans?status=&session_id=`,
  `POST /{id}/approve {ttl_minutes, removed_item_ids}`, `POST /{id}/deny`,
  `POST /{id}/end` — session-authenticated, each audited (`plan_approved`,
  `plan_denied`, `plan_ended` with the actor and item ids; the proposal is
  audited as `plan_proposed`). Plan items carry their F2 breakdown for the
  review card.
* **MCP**: `session_plan(session_id, items, note)` and
  `check_plan(plan_id, session_id?)`, registered next to `session_begin` on the
  shared server and every per-integration server and added to
  `_ALWAYS_ALLOWED`. `session_plan` validates the id shape and that the session
  exists, the 50-item / 4 kB / host / max_uses limits, notifies the operator
  ("Plan for session X: N changes on M hosts") and audits `plan_proposed`.
* **UI**: a "Plans awaiting approval" card above the queue. Each item shows its
  host, `×max_uses`, the exact command and the F2 breakdown; unticking removes
  it from the approval; a TTL selector (30 min – 4 h) and Approve/Deny. The
  session modal shows the session's plan with per-item `uses/max_uses` and an
  End button. Every agent-controlled string is escaped.
* **Docs**: `docs/AGENT_MANUAL.md` (a "Session plans" section plus the
  "one command, one ticket" exceptions), `docs/API.md`, and the `session_begin`
  description.

**Surprises and decisions**

* The plan check sits **after** the encoded-payload scan, so a blocked decoded
  payload still raises before any plan can approve it.
* The claim's `rowcount == 0` case means another request took the last use; the
  caller then falls through to normal JIT instead of being approved. Eight
  threads racing a `max_uses=1` item produce exactly one approval.
* Removed items stay in the table (`removed=1`) so the audit trail shows what
  the operator dropped; they never match. Denied/expired/ended plans match
  nothing (the claim requires `status='approved' AND until > now`), and
  `expire_plans` marks an elapsed plan `expired` so the UI says so.
* The proposal notification rides the existing `jit` webhook event, so it
  reaches operators with the default subscription; no new event type to
  configure.
* `check_plan` takes an optional `session_id` and refuses a mismatch — plan ids
  are small integers and a plan names the commands another session intends to
  run.
* The TTL is clamped server-side (1–240 min) even though the UI offers
  30/60/120/240, and a plan can only be approved once (`not_proposed`
  otherwise).
* One existing test changed: `tests/test_omada_mcp.py` asserted the exact
  per-integration tool set, which now includes `session_plan` and `check_plan`
  (commit message says so). Everything else is untouched.

**Deliberately not done**

* No item editing (out of scope), no guest-name labels (F7 optional), and no
  statistics bucket — the `session-plan:` "how approved" bucket is F6's job.
* No change to the gateway, to session overrides, or to F4's read lane. A plan
  is the only new auto-approve path and it only ever matches the exact bytes
  an operator approved.

**Tests** — `tests/test_f3_session_plans.py` (15): approve-once-then-pend and
the audit event; approve-a-subset (removed item pends); host-specific vs `*`;
session binding (another id and no id never match); expired/denied/ended plans
do not approve; Zero-Trust and Freeze win; strict sessions refuse an unminted
session; the 8-thread `max_uses=1` race approves exactly one; the REST list /
approve-subset / deny / end / auth; and the MCP validation + `check_plan`
reporting.

**Screenshot** — the Plans card (each item with its F2 breakdown, the TTL
selector and the approve/deny actions):
![Plans card](../screenshots/f3/plans-card-desktop.png)

and the session modal's plan with per-item uses:
![Session plan](../screenshots/f3/session-plan-modal.png)

`bash scripts/verify.sh` (pre-commit green):

```
1750 passed, 7 warnings in 350.22s (0:05:50)
```

(1735 before this chunk + 15 new F3 tests.)


### Round 2 — fixes for reviewer round 1 (R1–R4, S1–S4)

The "hand-off to the security reviewer" note was left alone as instructed.

* **R1 ownership.** `session_plan` refuses `session_not_yours` when the
  session's `sessions.agent` is set and differs from `current_agent_name()`;
  `check_plan` is scoped to the caller the same way, so the optional
  `session_id` argument is gone. The plan payload now carries `agent` and
  `session_goal`, and the card shows both (escaped) next to the plan number —
  the operator can no longer approve another agent's plan believing it is
  theirs. Tests: `test_f3_mcp_session_plan_refuses_another_agents_session`,
  `test_f3_mcp_check_plan_refuses_another_agents_plan`,
  `test_f3_plan_payload_names_the_agent_and_goal`.
* **R2 TTL reset on poll.** The chosen TTL lives in `_planTtl` (like
  `_planRemoved`), so the 3 s queue refresh keeps it. Harness test
  `test_f3_plan_ttl_survives_a_poll_rerender` (choose 30 min, render twice,
  still selected); the screenshot run also checked it live against the
  polling page (`ttl after poll: 30`).
* **R3 plan replaced mid-review.** `renderPlans` remembers the plan id shown
  per session; when a session's proposal changes, the new card gets the
  "This proposal replaced the one you were reviewing" banner, a warning
  border, and Approve needs a second press (F2's pattern). Harness test
  `test_f3_replaced_plan_needs_a_second_press` (first press sends 0 POSTs,
  second sends 1).
* **R4 End vs claim.** The claim's UPDATE now also requires
  `plan_id IN (SELECT id FROM session_plans WHERE status='approved' AND
  until > ?)`. `claim_plan_item` uses a `_claim_candidate` helper so the test
  can end the plan between the SELECT and the UPDATE:
  `test_f3_end_between_select_and_update_consumes_no_use` (no use consumed).
* **S1 state guards.** `_decide` allows Deny only from `proposed` and End only
  from `proposed`/`approved`; anything else is `PlanError('bad_state')` → 400,
  so a decided outcome is never rewritten. Test:
  `test_f3_deny_approved_and_end_expired_are_rejected`.
* **S2** `removed_item_ids` is typed `list[int]`, so a non-integer is a 422
  instead of a 500. Test: `test_f3_non_integer_removed_ids_is_422`.
* **S3** `fetchSessionPlans` uses `authFetch`. Harness test:
  `test_f3_plan_fetch_uses_authed_fetch` (the plans URL goes through
  `authFetch`, not plain `fetch`).
* **S4 F2's rule for plans.** `planNeedsConfirm` requires a second press when
  any kept item is a >500-char command with an unknown step (a plan can
  authorise many more runs than one request). Harness test:
  `test_f3_long_unknown_plan_needs_a_second_press`.

New harness: `tests/fixtures/f3_harness.js` on top of
`tests/fixtures/appjs_loader.js` (the F2/F7 pattern). All 25 F3 tests pass;
the full gate:

```
1760 passed, 7 warnings in 349.18s (0:05:49)
```

**Screenshot** — replaced so the card shows the proposing agent (`hermes`)
and the session goal next to the plan number:
![Plans card](../screenshots/f3/plans-card-desktop.png)

### Round 3 — security items (SR1–SR4)

The security reviewer approved F3 on `de741ec`, conditional on SR1; SR2–SR4
are done as well.

* **SR1 — truly byte-exact matching.** `claim_plan_item` no longer strips the
  request command (the gateway already trims; the old `str.strip()` also
  removed `\x1f`, `\x85`, `\xa0`, `\u2028`, …), and `validate_items`
  refuses any command containing a C0 control (tab excepted), DEL or
  non-ASCII whitespace — checked on the **raw** text, because `strip()` itself
  would have hidden `\x1c`/`\xa0` before the check. Tests:
  `test_f3_sr1_item_does_not_match_control_bytes` (an item `echo ok` does not
  match `echo ok\x1f`, `\x1cecho ok`, `echo ok\u00a0`; it still matches
  exactly `echo ok`) and `test_f3_sr1_proposal_refuses_hidden_characters`
  (those bytes, DEL and `\n` are refused with `invalid_command`; plain space
  and tab are accepted).
* **SR2 — ownership fails closed.** Both tools now compare
  `owner != current_agent_name()` without the `owner and` guard, so an empty
  owner (or an anonymous caller) is never "yours". Tests:
  `test_f3_sr2_anonymous_caller_is_refused` (no agent context, both tools) and
  `test_f3_sr2_session_without_owner_is_refused`.
* **SR3 — the warning sits where the decision is made.** The plan payload
  carries `strict_sessions` (`require_sessions_enabled()`), and the card shows
  "Strict sessions are off — anyone with the gateway SSH key who knows this
  session id can use this plan." when it is off. AGENT_MANUAL now recommends
  enabling strict sessions before relying on plans. Tests:
  `test_f3_sr3_api_reports_whether_strict_sessions_are_on` and the harness
  test `test_f3_sr3_strict_sessions_hint_in_the_card`.
* **SR4 — broad items stand out.** An item with `host: "*"` or
  `max_uses > 1` is rendered with `plan-item-broad`, an amber host label and
  an amber uses count (the two ways an item grants more than one exact run on
  one host). Harness test: `test_f3_sr4_broad_items_are_highlighted`
  (two of three items highlighted; a single-use exact item is not).

The Plans screenshot was refreshed: it shows the strict-sessions line, the
`any host` item and the `×2` item both highlighted, next to the agent and
session goal.

Full gate:

```
1767 passed, 7 warnings in 351.50s (0:05:51)
```

(1760 before this round + 7 new security tests.)

### Reviewer notes

**Round 1 (2026-11-08): changes requested.** 105 targeted tests pass locally
(`test_f3_session_plans.py` + `test_omada_mcp.py`); implementer's full run
1750 passed.

*Security pass on the approval path. These checks are correct:*

* **Order (§ 2.6):** the plan check sits after the encoded-payload block and
  before the session override. Freeze and Zero-Trust are checked first. The
  gateway's blocklists and self-protection run before the dashboard is asked,
  so a plan can never approve a blocked command.
* **Exact bytes end to end:** a match creates an ordinary `approved` row, so
  the gateway still runs it through claim-and-burn of a lockbox ticket that
  matches the command byte for byte. A plan adds no new way to execute.
* **Session binding:** the claim is keyed on `sid`. No id means no match, and
  another session's id never matches (tests cover both).
* **Use counting:** a conditional `UPDATE … WHERE uses < max_uses` with
  `rowcount` checked; the 8-thread race approves exactly one.
* **Approval:** by plan id only, over REST with `_check_session`. Items are
  never changed after creation (a new proposal is a new id), so an approved
  plan's content cannot be swapped. No MCP tool can approve.
* **XSS:** every agent string in the card and the modal goes through
  `escapeHtml`; ids are passed through `Number()`.

*Must fix:*

* **R1 Ownership.** `session_plan` accepts any existing session, including
  one minted by a different agent (`sessions.agent` records who minted it).
  The notification and card then show the other session's goal, so the
  operator approves a plan believing it is Hermes's. Fixes:
  * `session_plan`: refuse with `session_not_yours` when `sessions.agent` is
    set and differs from `current_agent_name()`.
  * `check_plan`: return only plans whose session belongs to the caller. That
    replaces the optional-`session_id` check; the `_ALWAYS_ALLOWED` comment
    already says these tools are "scoped to the caller".
  * The plan card shows the **agent** and the **session goal** (escaped).
  * Tests: agent B is refused for A's session, and `check_plan` refuses B.
* **R2 TTL resets on every poll.** `fetchSessionPlans()` runs in the queue
  refresh and rebuilds `plans-list.innerHTML`, so the TTL `<select>` jumps
  back to 1 h. An operator who picks 30 min and waits a few seconds approves
  for 1 h. Fix: keep the chosen TTL in state (like `_planRemoved`), or skip
  the re-render when the plan list is unchanged. Add a harness test: choose a
  TTL, re-render, check the selection.
* **R3 Plan replaced while it is being read.** A new proposal deletes the old
  one, and the next poll swaps the card's content under the operator. They
  can press Approve on items they never saw. Fix: when a session's proposed
  plan id changes while its card is shown, mark the card "Replaced, review
  again" and require a second press (reuse the F2 two-press pattern). Add a
  harness test.
* **R4 End vs claim.** The claim's UPDATE rechecks `uses` but not the plan.
  If End or Deny lands between the SELECT and the UPDATE, one more use goes
  through. Add `AND plan_id IN (SELECT id FROM session_plans WHERE status =
  'approved' AND until > ?)` to the UPDATE, with a test that ends the plan
  between the two steps (monkeypatch or a direct call).

*Should fix:*

* **S1 State guards in `_decide`.** Deny only a `proposed` plan, and End only
  a `proposed` or `approved` one; anything else returns `PlanError('bad_state')`,
  which becomes a 400. Today Deny on an approved plan, or End on an expired
  one, rewrites its recorded outcome.
* **S2** `removed_item_ids` with a non-integer gives a 500. Type it
  `list[int]` in the payload model.
* **S3** `fetchSessionPlans` uses plain `fetch`; switch to `authFetch` like
  the decide calls.
* **S4** Apply F2's rule to the plan: if any kept item is over 500 chars with
  an unknown step, Approve needs a second press. A plan can carry many more
  runs than a single request.

*Hand-off to the security reviewer (not a regression, no change asked
here):* with strict sessions off, any SSH caller that knows a session id
can use that session's plan. Session overrides have the same exposure
today. Minted ids are 12 hex chars, but agents may also bring their own
chat ids. The plan's docs should recommend turning strict sessions on
before relying on plans. The security reviewer decides whether to require
it.


**Round 2 (2026-11-08): approved by the feature reviewer; waiting on security sign-off (§ 2.6a).**
127 F3, F2 and MCP tests pass locally; implementer's full run 1760 passed.

* **R1:** `session_plan` and `check_plan` refuse another agent's session with
  `session_not_yours`. The card shows the agent and the session goal, both
  escaped. A session minted with no agent can still take a plan from any
  caller; the card then reads "unknown agent", which is acceptable.
* **R2:** the TTL lives in `_planTtl` and survives the 3 s poll.
* **R3:** a changed proposal id for a session on screen shows the banner and
  needs a second press. `create_plan` replaces a proposal in one transaction,
  so no poll can see a gap that would hide the swap.
* **R4:** the UPDATE rechecks the plan's status and `until`; a test ends the
  plan between the SELECT and the UPDATE.
* **S1–S4:** all done as asked.

**Do not merge until the security reviewer signs off below.**

**Round 3 (2026-11-08): SR1 checked; merged.** SR1 is correct.
`claim_plan_item` compares the request bytes unstripped, and `validate_items`
checks the raw text before `strip()`. The listed tests pass.

* **Fixed during review (SR2 gap):** the check `owner != current_agent_name()`
  still let an anonymous caller use an ownerless session, because `'' == ''`.
  Both tools now use `not owner or owner != current_agent_name()`. Added
  `test_f3_sr2_anonymous_caller_and_ownerless_session_is_refused`; it fails
  before the fix and passes after.
* **Observation (no change):** the gateway does not trim `SSH_ORIGINAL_COMMAND`.
  An agent that sends stray leading or trailing spaces gets no match and
  falls through to normal JIT, which fails safe.
* SR3 and SR4 done. Per the security reviewer's merge rule, no further
  security pass is needed.

#### Security reviewer sign-off

**2026-11-08 — approved, conditional on SR1 (security chat, reviewed at `de741ec`).**
Reviewer ran F3, all `test_sec_*`, gateway-script, session and session-override
tests on the branch: 317 passed. The branch already contains security Chunk H.

**What was reviewed and holds:**
1. **Approval path (`receive_request`).** Placement is right: after the
   encoded-payload block, before the session override. Freeze and Zero-Trust
   short-circuit it; strict sessions use the global flag
   (`require_sessions_enabled`), which is stricter than the per-gateway scope
   and is fine. Blocklists and the hard block are enforced by the gateway
   before a request ever reaches `/api/request`, so a plan cannot approve
   anything the gateway refuses. Security chunk 1c still binds execution to
   the approved bytes, and the approved row records
   `session-plan:<plan>:<item>` plus an audit event — attributable.
2. **`claim_plan_item`.** The conditional UPDATE (`uses < max_uses`, plan
   still `approved` and unexpired, item not removed) is atomic; a lost race
   rolls back and falls through to normal JIT. Correct.
3. **Ownership (`session_plan`, `check_plan`).** Sessions are only created by
   `session_begin`, which always records the calling agent; `upsert_session`
   never overwrites an existing owner, so adopting someone else's id does not
   transfer it (worst case a pre-minted id denies its real owner — no
   escalation). All 22 live sessions have an owner. Combined with R1 (agent +
   goal on the card), the operator cannot be misled about whose plan it is.
4. **Hand-off — should plans require strict sessions?** **No, not required.**
   Without strict sessions a plan is usable by any caller that holds the
   gateway SSH key *and* the session id — the same trust boundary session
   overrides already have — but a plan is strictly narrower: exact
   operator-approved bytes, named hosts, counted uses, TTL ≤ 4 h. Requiring
   strict sessions would make plans unavailable on installs that have not
   turned it on, for little gain. Do instead: **SR3** below. (The live install
   already runs with strict sessions on.)

**Required before merge:**

* **SR1 — make the match truly byte-exact.** `claim_plan_item` compares
  `command.strip()`. Python's `str.strip()` removes far more than the
  gateway's `[[:space:]]` trim (e.g. `\x1c–\x1f`, `\x85`, `\xa0`, `\u2028`), so
  the item `echo ok` matches a request for `echo ok` followed by one of those
  bytes; the request row then stores — and, via 1c, the gateway runs — the
  extra bytes. Impact is small (those bytes cannot start a new command), but
  the design promises exact bytes. Fix: compare the request command **without**
  stripping (the gateway already trims), and in `validate_items` refuse
  commands containing C0 control characters other than tab, DEL, or Unicode
  whitespace other than plain space/tab. Tests: an item `echo ok` does **not**
  match `echo ok\x1f`, `\x1cecho ok`, `echo ok\u00a0`; a proposal containing
  such characters is refused with `invalid_command`.

**Recommended (do now or in a follow-up, no second security pass needed):**

* **SR2 — ownership fails closed.** In both tools, treat an empty owner as
  "not yours" (`owner != current_agent_name()` without the `owner and`
  guard). Today unreachable (every session has an owner), but it removes the
  "unknown agent can take a plan from any caller" case noted under R1.
* **SR3 — say it where the operator decides.** When strict sessions are off,
  show one line in the plan approval card: "Strict sessions are off — anyone
  with the gateway SSH key who knows this session id can use this plan." Keep
  the docs recommendation to enable strict sessions before relying on plans.
* **SR4 — highlight the broad items.** In the card, make `any host` (`*`)
  items and `max_uses > 1` visually distinct, since those are the items that
  grant more than one exact run on one host.

**Merge rule:** once SR1 is in with its tests and the feature reviewer has
checked it, F3 may merge into `feature-roadmap` without another security
pass. SR2–SR4 do not block.

---

## Chunk F4 — Read-only lane (shadow first) · slug `read-lane` · dashboard

### Goal

Let commands that **only read** run without a prompt, opt-in per gateway, after
a shadow period that shows the operator what it *would* have approved.

### Design

* Per-gateway setting `read_lane`: `off` (default) | `shadow` | `on`.
* A JIT request is **read-lane eligible** only if all of these hold:
  1. `breakdown.complete` is true, every step is `read` (no `write`, no
     `unknown`), and every step's verb is on the reviewed `READ_LANE_VERBS`
     allowlist in `core/read_policy.py` — status verbs that take no file
     operands, and file readers (`cat`, `head`, `tail`, `grep`, `wc`, `ls`,
     `stat`, `du`) that may only touch the safe trees;
  2. no flags at all (F1's flag list fails closed; `repo-config` and
     `follows` block automatically, and so will any flag F1 adds later);
  3. every step reads only inside the safe trees — `/var/log/`, `/proc/`,
     `/sys/` and exactly `/etc/os-release`, `/etc/hostname`, `/etc/hosts`,
     `/etc/resolv.conf` (the gateway runs **every** command as root, so this
     applies to every step, not just `sudo` ones). `/proc/*/root`,
     `/proc/*/cwd`, `/proc/*/fd`, `/proc/*/map_files`, `/proc/*/mem`,
     `/proc/*/environ`, `kcore`, `kmsg` and all of `/dev/` are refused, as are
     globs and `..` components in a path operand;
  4. no inner target (`pct exec`, `qm guest exec`, `docker exec`) — the lane is
     opted in per gateway and the guest never opted in;
  5. command ≤ 8 kB; gateway not frozen and not Zero-Trust;
  6. not blocked by the central blocklist (`policy_eval` verdict is not `blocked`/`fatal`).
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

**What I did**

* **`core/read_policy.py` (§ 2.6a)**: one entry point for "is this a read?".
  `is_read_ssh(breakdown)` is F4's rule set — the breakdown must be complete,
  every step must be `read`, none of F1's blocking flags may be set
  (`encoded`, `heredoc`, `nested-ssh`, `sensitive-path`, `redirect-to-file`),
  and a step marked `elevated` may only touch `/proc/`, `/sys/`, `/var/log/`
  or exactly `/etc/os-release` (a path token containing `..` is refused
  outright, so `/var/log/../…` cannot slip through the prefix check).
  `is_read_integration_call(tool, transport, args)` is a thin wrapper over
  Chunk H's `_read_only_refusal` — no new integration logic.
* **`core/read_lane.py`**: `eligible(breakdown, verdict, command, mode,
  frozen, zero_trust)` checks mode, freeze, Zero-Trust, the 8 kB cap and the
  policy verdict (`blocked`/`fatal` lose), then delegates to
  `read_policy.is_read_ssh`. It has no read rules of its own.
* **Setting**: `gateways.read_lane` (`off` default, migration) plus
  `read_lane_since`; `read_lane_ready()` is true after 7 days in shadow.
  `PUT /api/gateways/{ip}/read-lane` (session auth, audit `read_lane_<mode>`)
  refuses `on` before that window — the UI disables the option, but the
  server does not rely on the UI.
* **Hook** in `POST /api/request`, after F3's plan and the session override,
  before the host override (§ 2.6). `shadow`: the request stays pending as
  usual (notify + `jit_created`) but gets `reason="read-lane-shadow"` and an
  audit `jit_read_lane_shadow`; the operator's decision is the ground truth.
  `on`: creates `approved` with `reason="read-lane"`, audits
  `jit_read_lane_approved`, no notification.
* **UI**: a "would auto-run" chip on shadow rows, a three-state
  `off / shadow / on` select on the Gateway row (`on` disabled until ready),
  and a **Read-only lane · shadow** tile in Statistics (eligible, approved /
  denied, median length, how many auto-ran once on).

**Surprises and decisions**

* The 7-day rollout clock is stored (`read_lane_since`, set on entering
  shadow) rather than inferred from audit history; the "at least 2 gateways"
  part of the rollout note is operator policy and is not enforced in code.
* Shadow reuses the normal pending path instead of a separate branch, so
  notifications and the `jit_created` audit stay exactly as they were.
* F1's `sensitive-path` already covers `.env`, keys, `/etc/shadow`,
  `/proc/*/environ` and the broad `secret`/`token`/`passw` tokens, so those
  commands are refused by flag before the elevated-path rule ever runs.
* A read lane has no session requirement (unlike F3 plans): it also covers
  SSH callers that never minted a session, which is the common case for
  allowlist-less reads.
* The new statistics queries tripped the KI-1 regression test on the first
  gate run (`r.target_ip` without the alias); they are aliased now — the test
  did its job.

**Tests** — `tests/test_f4_read_lane.py` (67): 24 eligible and 27 ineligible
table cases covering every exclusion in the chunk, `.env` (four spellings),
and a **generated** test that every F1 `write`/`unknown`/incomplete fixture is
ineligible (≥20 checked). Plus the Chunk H wrappers, shadow-never-approves,
`on`-approves-only-eligible, `off`-does-nothing, Zero-Trust/freeze/blocklist
wins, the 8 kB cap, the default-`off` migration, the endpoint + 7-day gate +
session auth, and the statistics report (counts and median).

Security sign-off (§ 2.6a) is still owed by the security reviewer; the branch
is ready for that pass.

`bash scripts/verify.sh` (pre-commit green):

```
1884 passed, 7 warnings in 371.46s (0:06:11)
```

(1817 before this chunk + 67 F4 tests.)


### Round 2 — fixes for reviewer round 1 (R1–R4, S1)

The design's rule 3 was wrong — the gateway runs **every** command as root, so
`sudo` says nothing about privilege. It is replaced by a closed allowlist on
top of F1, and the spec now says "every step (the gateway runs as root)".

* **R1 — allowlist on top of F1.** `READ_LANE_VERBS` in `core/read_policy.py`
  has two groups. Status verbs that take no file operands: `uptime`, `df`,
  `free`, `ip` (`a`/`addr`/`route`/`link`), `ss`, `ps`, `uname`, `hostname`,
  `lsblk`, `lscpu`, `whoami`, `id`, `date`, `nproc`, `systemctl`
  (`status`/`is-active`/`is-enabled`/`list-units`/`list-timers`), `journalctl`
  (with `-n`, or `--disk-usage`), `docker` (`ps`/`stats --no-stream`/
  `images`), `pct list`/`qm list`, `zpool status|list`, `zfs list`. File
  readers: `cat`, `head`, `tail` (no `-f`), `grep`, `wc`, `ls`, `stat`, `du`,
  and every operand — bare or relative words and `--opt=value` included —
  must be a safe path. Anything else is ineligible even if F1 says `read`.
  A wrapper other than `sudo` (`env`, `ip netns exec`, `timeout`, `runuser`)
  is refused too: it hides part of the command. Tests: the 45-case eligible
  table and `test_f4_every_eligible_verb_is_on_the_allowlist`; every probe
  class is in the ineligible table.
* **R2 — the safe trees apply to every step.** `/var/log/`, `/proc/`, `/sys/`
  and exactly `/etc/os-release`, `/etc/hostname`, `/etc/hosts`,
  `/etc/resolv.conf`; inside `/proc` the `root`, `cwd`, `fd`, `map_files`,
  `mem` and `environ` entries and `kcore`/`kmsg` are refused; all of `/dev/`
  is refused; any glob or brace character and any `..` part is refused on the
  whole token. Tests: block devices, `/proc` links, `cat config.yml`,
  `/srv/app/config.yml`, glob-before-`..`.
* **R3 — flags fail closed.** `READ_HARMLESS_FLAGS` is empty, so every F1
  flag blocks — `repo-config` and `follows` included, and any future flag.
  Tests: `test_f4_any_flag_blocks` plus the `git log` / `tail -f` /
  `journalctl -f` table cases.
* **R4 — no inner targets.** `pct exec`, `qm guest exec` and `docker exec`
  are ineligible. Test: `test_f4_inner_targets_are_ineligible` plus the table
  cases.
* **S1 — line caps.** `journalctl` needs `-n ≤ 1000` (or `--disk-usage`) and
  `tail -n` is capped at 1000, so one auto-run read cannot dump a whole
  journal. Test: `test_f4_journal_and_tail_line_caps`.

Two deliberate narrowings worth the security reviewer's eye: `grep` patterns
are bare operands under R1's "every operand ... including bare words", so only
`grep -e <pattern> <safe-path>` is eligible; and `echo` section markers are
not on the verb list, so agent scripts that echo section headers stay
ineligible.

`bash scripts/verify.sh` (pre-commit green):

```
1951 passed, 7 warnings in 373.87s (0:06:13)
```

(1884 before this round + 67 new/expanded F4 tests; the F4 file has
134 tests.)

### Round 3 — fixes for reviewer round 2 (R5–R8, S2, S3)

* **R5 — recursion only under `/var/log`.** `grep -R` and
  `--dereference-recursive` are refused everywhere (they follow links), and
  `grep -r`/`--recursive`, `ls -R` are allowed only when every operand is
  under `/var/log/`; `grep -d recurse`/`--directories=recurse` is refused.
  `du` recurses implicitly, so its operands may not be `/proc` or `/sys`.
  Table: `grep -R …`, `grep -r … /proc`, `grep -r … /sys`, `ls -R /proc`,
  `du -sh /proc`; eligible adds `grep -r -e error /var/log` and
  `ls -R /var/log`.
* **R6 — no `ps` environment output.** BSD option words (no leading dash)
  containing `e` are refused (`ps e`, `ps eww`, `ps auxe`), as is any
  `environ`/`env` token (`ps -eo pid,environ`, `ps --format=environ`);
  `ps -e`/`-ef` stay eligible. Table cases added.
* **R7 — no endless/interval forms.** `free -s`/`-c`, `zpool status|list`
  with a numeric interval or `-T`, and `ss -E`/`--events` are refused.
  `test_f4_eligible_cases_have_no_endless_form` statically asserts that no
  eligible table case carries one of those forms.
* **R8 — journal sources.** `--file=`, `--directory=`, `--root=`, `--image=`,
  `-m`/`--merge` (and their `--opt=value` spellings) are refused, not only
  `-D`.
* **S2** `head -c`/`tail -c` are capped at 1 MiB (`bytes-cap`), as `-n` is
  capped at 1000.
* **S3** `/sys/firmware` is out of the safe trees (EFI variables can carry
  vendor secrets).

All synthetic (`192.0.2.x`, `/var/log/synthetic-*`, `web01`). Full gate:

```
1986 passed, 7 warnings in 373.06s (0:06:13)
```

(1884 + 102: the F4 file now has 169 tests.)

### Round 4: on-lock

`on` is now hard-locked behind a `meta` key that only a human can set.

* `PUT /api/gateways/{ip}/read-lane` refuses `mode=on` with **409
  `read_lane_on_locked`** unless `meta.read_lane_on_enabled` is the string
  `"1"` (`read_lane_on_enabled()` in `db/gateways.py`). The key is unset by
  default and **no API or UI writes it** — it is set by hand after the
  security sign-off.
* The lock is checked before the 7-day readiness rule, so a gateway that has
  had a week of shadow still cannot be switched on while locked. `shadow` and
  `off` are unaffected.
* `GET /api/gateways` exposes the state as `read_lane_on_enabled`; the
  Gateway table's select shows **"RL on: locked until security sign-off"**
  (disabled) while the key is unset, and the 7-day hint takes over once it is.

**Tests** (`TestOnLock`, 6): refused by default even with `read_lane_ready`
true; shadow/off unaffected; allowed when the key is `"1"` and the window has
passed; still refused when unlocked but shadow is younger than 7 days; no API
attempt can set the key (and the gateway list reports the lock). The existing
7-day test now unlocks the key first, since the lock is its own gate.

The lock is the second gate on `on`: the security sign-off above approved the
lane on conditions SC1–SC8, and the operator sets `read_lane_on_enabled` by
hand only once those are addressed and the lane is verified in shadow.

`bash scripts/verify.sh`: `1992 passed, 7 warnings in 391.24s (0:06:31)`.

### Round 5: security conditions

The sign-off approved F4 on conditions SC1–SC8. All eight are in.

**SC1–SC3 are the diff the security chat should look at** (`4b3a121`, files
`dashboard/core/read_policy.py` and `dashboard/core/cmd_parse.py`); SC4–SC7 are
in the same commit, and SC8's tests are in `c470d6e`.

* **SC1 — options are allowlisted per verb and subcommand.** `OPTION_SPECS` in
  `core/read_policy.py` is the whole list, keyed by `(verb, subcommand)`.
  Spellings are exact (`--huma` is not `--human-readable`), a short cluster is
  allowed only when every letter is on that verb's list (`ss -tlnp` yes,
  `ss -tlnpq` no), and whether an option takes a value is decided per verb
  (`grep -m` is a count, `head -m` is not an option). `--` and a lone `-` are
  refused. No status option names a file, a host, a machine, a context or a
  format template: `docker -H`/`--context`/`--format`, `systemctl -H`,
  `ps -o`, `stat -c`, `date +%s` and `journalctl -o` are all out.
* **SC2 — every other word is a file.** `_scan_options` returns the operands
  after the listed options and their values, and each must pass `_path_ok`.
  grep's first non-option word is its pattern, and only when `-e` is absent.
  Consequence: a status verb's bare name is an operand, so
  `systemctl status nginx` is no longer eligible while bare `systemctl status`
  is — see the open question.
* **SC3 — no input redirection.** F1 now flags `input-redirect` and records
  `input_redirects: [{op, target}]` on the step; `is_read_ssh` refuses either.
  `cat /proc/cpuinfo < /etc/shadow` used to look like a plain read because the
  step text dropped the redirection.
* **SC4 — the policy checks subcommands.** `SUBCOMMANDS`/`ACTIONS` decide
  `systemctl`, `docker`, `pct`, `qm`, `zpool`, `zfs` and `ip` (including the
  action), so `zpool destroy`, `ip addr flush` and `systemctl list-unit-files`
  are refused even if F1 ever called them reads.
* **SC5 — bounded output.** `_cap_lines` (≤ 1000) and `_cap_bytes` (≤ 1 MiB)
  run for every spelling (`-n`, `--lines=`, `-n5`, `-100`); `+N` is refused
  because it means "from line N" (uncapped); `head` has the same cap as
  `tail`. Logs under `/var/log` leave the lane unless read through `head`,
  `tail`, or `grep` with `-m` ≤ 1000 — `cat` and `wc` stay for `/proc`,
  `/sys` and the four `/etc` files.
* **SC6 — tighter safe trees.** `PROC_DENY_PARTS` adds `exe` and `pagemap`,
  `kpage*` names are refused, and `/sys/kernel/debug`, `/sys/kernel/tracing`,
  `/sys/fs/bpf`, `/var/log/installer` and the cloud-init logs are out.
* **SC7 — a "needs a human" verdict wins.** `eligible()` acts only on a plain
  `jit` (no `detail_type`) or `auto_approved`; `self_protection_jit`,
  `shell_meta` and any future action are refused before the read rules run.
* **SC8 — tests.** `TestSecurityConditions` covers each class above, with
  synthetic paths and the `192.0.2.x` range, and the generated
  `test_f4_sc8_every_option_in_every_eligible_case_is_allowlisted` walks every
  eligible case and asserts each option spelling or cluster letter is in that
  verb's spec.

**Also from the sign-off**

* The duplicate `evaluate_policy_verdict` import is gone; it went with the
  `feature-roadmap` merge in `1d25807`.
* `receive_request` treats a stored `on` as shadow while
  `read_lane_on_enabled` is unset (`0861afe`), covered by
  `test_f4_stored_on_is_shadow_while_the_key_is_unset`.
* The sign-off's accepted residual risks are now in the README's
  "Security & threat model" section, next to the other honest limitations.

**Tests.** `tests/test_f4_read_lane.py` has 260 tests (169 before this round);
the F18 read-lane tests no longer skip (24 pass in that file). `bash
scripts/verify.sh`: `2120 passed, 7 warnings in 390.00s (0:06:29)`.

**Open questions**

1. **SC2 against a status verb's unit name.** SC2 says every word that is not
   a listed option or its value is a file operand and must pass the safe-tree
   check. Taken literally that makes `systemctl status nginx`,
   `systemctl is-active nginx`, `docker inspect web01` and
   `ip addr show dev eth0` ineligible, while bare `systemctl status` and
   `systemctl list-units --type=service` stay. The round-3 probe list treats
   `systemctl status`/`list-units` as everyday reads, and a unit name is a
   name, not a file — but the condition as written has no exception for it. I
   implemented it literally (fail closed) and moved those cases to the
   ineligible table. If a bare-name exception is wanted, it needs a scope
   (which verbs, which characters) and a line in §2.6a; otherwise no change.

### Round 6: fixes for reviewer round 5

* **R1 — the path is compared with its normalised form.** `_path_ok` refuses
  any path where `posixpath.normpath(token)` differs from the token (one
  trailing slash allowed), before any prefix check, so `/sys/kernel//debug`
  and `/sys/./kernel/debug` cannot get past the SC6 prefix denials. The
  explicit `..` refusal stays. `test_f4_r1_normalised_paths_cannot_slip_past_a_prefix_denial`
  covers every `SYS_DENY_PREFIXES`/`LOG_DENY_PREFIXES` entry with both
  spellings across `cat`, `head`, `tail`, `grep -m`, `ls` and `stat`;
  `test_f4_r1_a_trailing_slash_still_reads` keeps `/var/log/` legal.
* **R2 — recursion is out of the lane.** `-r`/`--recursive` are removed from
  the `grep` spec (and `r` from its cluster letters), so a recursive log grep
  is refused as an unknown option instead of walking into `/var/log/installer`
  and the cloud-init logs; `ls -R` and `du` still list names. This also closes
  the per-file `-m` bound. `test_f4_r2_recursive_grep_is_not_in_the_lane`
  tries `/var/log`, `/var/log/`, `//var/log` and `/var/log/./` with both
  spellings.
* **R3 — context options cannot undo the `-m` cap.** For a `/var/log`
  operand, `_grep_log_lines` requires `m × (1 + max(A + B, 2C)) ≤ 1000`;
  `-A`/`-B`/`-C` are still capped at 1000 individually.
  `test_f4_r3_grep_context_cannot_undo_the_cap` checks at the limit
  (`-m 500 -A 1`, `-m 300 -C 1`), over it (`-m 500 -A 2`, `-m 1000 -A 1000`)
  and that context outside `/var/log` is unaffected.
* **R4 — `head`/`tail` lose `-z`.** `-z`/`--zero-terminated` (and the `z`
  cluster letter) are gone from both specs, so they cannot turn a text log's
  line cap into a whole-file dump. `test_f4_r4_head_and_tail_have_no_zero_terminated`
  covers the short, long and clustered spellings for both verbs.
* **S2 — `zpool`'s cluster letters match its flags** (`vxHpLPg`), so `-g`
  works and `-G` is refused; **S4 — `grep -P`/`--perl-regexp` is out** of the
  lane until F19 adds a time bound. `test_f4_s2_*` and `test_f4_s4_*`.
* **Q1 — SC7 is unchanged in this round, as the task asked.** The security
  answer ("yes, allow `shell_meta`", in the Security reviewer sign-off below)
  landed while this round was being pushed, so the behaviour here is still the
  round-5 one: `shell_meta` is not a plain `jit`. The end-to-end test is
  `test_f4_sc7_chained_read_is_not_lane_eligible_end_to_end`: with the lane in
  shadow, `uptime && whoami` through `POST /api/request` stays pending with no
  `read-lane-shadow` reason, and with the lane `on` (and the lock unlocked)
  `uptime || whoami` is not auto-approved. The next round flips it, together
  with the security round-2 items below.
* **S1 and S3 are not in this round.** S1 (an operand-class model so status
  verbs refuse every operand) is a policy-shape change the reviewer marked
  optional and it needs a spec line for which verbs take paths; S3 (a maximum
  number of log operands) is bounded by the 8 kB command cap today. Both are
  written down here so they are not lost.

**Shared `core/read_policy.py` (merge prep).** F9 and F14 each carry only
`is_read_integration_call` plus the `read_only` guard, so their copies combine
cleanly with this file's SSH half. This branch's wrapper now has the same
guard — it was a bare `_read_only_refusal` wrapper, which would have called a
curated write tool a read — and `test_f4_a_curated_write_tool_is_not_a_read`
locks it in. At merge, keep this file's SSH rules and take F9's guarded
integration wrapper, as F9's Reviewer notes say.

**Security round 2 landed after this round's push.** It confirms R1-R4 (and R2
as implemented: `-r` is gone), and adds work that is deliberately **not** in
this round, because it is a new security condition, not a feature-review R
item:

* **SC9 (must):** no eligible word may carry a shell expansion (`$`,
  backtick, `{`, `}`, `*`, `?`, `[`, `]`, a leading `~`, a backslash) - path
  operands, option values and patterns alike - and a bare assignment
  statement must be refused. This is the one that must ship before
  `shell_meta` is allowed, hence the Q1 order: SC9 and Q1 in the same round.
* **R5 (add, must):** refuse the journal directory and `lastlog`/`wtmp`/
  `btmp`/`faillog` under `/var/log`, and refuse `grep -a`/`--text`.
* **Q1:** allow `shell_meta` (and refuse a `&`-backgrounded step); **Q2:** the
  narrow `systemctl status|is-active|is-enabled` name exception; **Q3:** only
  a bare `sudo` (F1 must record that the wrapper had no options).

Also confirmed there: unlocking `on` waits for the gateway to stop logging
session signatures (the F12 note), which is security Chunk 7 work.

`bash scripts/verify.sh`: `2134 passed, 7 warnings in 390.21s (0:06:30)` (round 6, after the read-policy guard and its test).

### Round 7: security round 2 (SC9, R5, Q1-Q3)

**Commits for the security chat: SC9 is `51f2e40` + `0ed276c`; Q1 is
`51f2e40` + `0ed276c`; Q3 is `51f2e40` + `0ed276c`.** (`51f2e40` is F1's half —
bare assignments, the background marker, the `sudo(options)` label; `0ed276c`
is the lane's half.)

* **SC9 — the shell cannot expand a word the lane approved.** `_step_ok`
  checks the **raw** step words (from the display text, before
  `shlex.split` strips F1's re-quoting) and refuses `$`, a backtick, `{`,
  `}`, `*`, `?`, `[`, `]`, a backslash anywhere and a leading `~`. It covers
  path operands, option values and both grep pattern spellings, and quoted
  words are included because F1 does not record whether a word was quoted.
  A bare `NAME=value` statement now emits an `unknown` step ("bare variable
  assignment") instead of vanishing, so the lane refuses it (and F1's
  `assignment-then-echo` fixture expects the extra step). Tests
  (`TestRoundSeven`): every character in a path, in a `grep -e` pattern and
  in an option value; the first-word pattern form; a quoted backslash; a
  leading `~`; the bare assignment (`FOO=1`) and the prefixed form (still
  refused, fail closed).
* **Recursion — every spelling is out of the lane.** `-r`, `-R`,
  `--recursive`, `--dereference-recursive`, `-ir`, `-ri`, `-rn`,
  `-d recurse` and `--directories=recurse` are unknown options, so each is
  refused (with `/var/log` and `/var/log/` as the operand). This replaces
  round 6's R2 with the simpler rule the security reviewer named; `ls -R` and
  `du` still list names.
* **R5 — binary logs.** `head`, `tail` and `grep` refuse the journal tree
  (`/var/log/journal` and below) and the login-record files (`lastlog`,
  `wtmp`, `btmp`, `faillog`, including `wtmp.1`-style rotations), because a
  file with few newlines defeats the line cap. `ls`/`stat` metadata stays
  allowed, and an ordinary log still reads. `grep -a`/`--text` are removed
  from the spec (so they are unknown options) and `--binary-files=text` was
  never listed; all are refused.
* **Q1 — chains and pipes are eligible; background is not.** `read_lane.eligible`
  now accepts `auto_approved`, a `jit` with no `detail_type`, **or a `jit`
  with `shell_meta`**, because `shell_meta` only says the gateway cannot vouch
  for the literal string and the lane parses every step itself (SC9 and the
  rest still apply to those steps). A step marked `background` by F1 (the
  statement before a `&`, including a trailing `cmd &`) is refused, since it
  can outlive the session. The end-to-end test is flipped:
  `test_f4_sc7_chained_read_is_lane_eligible_end_to_end` — in shadow,
  `uptime && whoami` through `POST /api/request` now carries
  `read-lane-shadow`; with the lane `on` it auto-approves; the verdict is
  asserted to be `shell_meta`.
* **Q2 — unit names for the three reviewed subcommands.** `systemctl
  status`, `is-active` and `is-enabled` take up to 8 operands, each matching
  `[A-Za-z0-9][A-Za-z0-9@_.:-]{0,63}` (first character a letter or digit, no
  `/`, no glob, no backslash). A path-shaped operand, a ninth name, a
  65-character name and an escaped name all stay with a human; other
  subcommands still refuse every operand. The `-n` cap on `status` is
  unchanged.
* **Q3 — bare `sudo` only.** `_unwrap` labels only the bare form `sudo`; any
  option or assignment gets `sudo(options)`, which the lane refuses through
  its existing `context != ['sudo']` rule. Tests cover `-E`, `-u root`, `-i`
  and `--`, and assert F1's contexts directly.
* **Merge integration.** The branch now merges `origin/feature-roadmap`
  (F8, F9, F14). `core/read_policy.py` keeps F4's full module (SSH rules and
  the guarded integration wrapper) and takes F9's docstring line about the
  freeze. F8's generated statistics-filter scan exposed F4's read-lane report
  queries; they now skip excluded rows like the other statistics, so the scan
  passes on the merged tree.

`bash scripts/verify.sh`: `2236 passed, 7 warnings in 419.39s (0:06:59)`.

### Round 8: security round 3 (SC10, SC11)

The final sign-off approved the lane on two small conditions; both are in, and
the feature reviewer checks them (no further security pass).

* **SC10 — loops and grouping are refused.** F1 now flags any command that
  contains a loop (`for`, `select`, `while`, `until`), a conditional (`if`,
  `case`), a function definition (`function` or `name()`), a brace group or a
  subshell with `compound`. The check runs on the **parsed atoms** in
  `_parse_text`, not on the raw text, so a keyword at a command position is
  caught while a keyword used as an argument (`grep for …`) is not; `{`/`}`
  and bare `(`/`)` atoms flag grouping, and `$( )`/`<( )` were already folded
  into word atoms. Every flag blocks the lane, so these commands are
  ineligible; plain chains (`uptime && df -h`, `uptime; free -h`,
  `uptime || whoami`) stay eligible. Tests (`TestRoundEight`): each construct,
  including a `while` whose condition and body are both eligible reads, a
  backgrounded read inside braces and inside parentheses, the plain chains,
  and `grep for …` staying clean. The four `loops.txt` fixtures now expect
  `flags=compound`.
* **SC11 — dated login-record rotations.** `_binary_log` matches a plain name,
  a dotted rotation (`wtmp.1`) and now a dated one (`btmp-20260101`, with or
  without `.gz`) for `lastlog`, `wtmp`, `btmp` and `faillog`, so `head`,
  `tail` and `grep` refuse all of them. Tests for each of the four names in
  both forms across the three verbs, plus the ordinary-log and metadata
  checks already in R5.

Both conditions are in commit **`9f06cb6`** (the F1 atom scan and its
fixtures, the `_binary_log` change and the tests).

`bash scripts/verify.sh`: `2243 passed, 7 warnings in 419.62s (0:06:59)`.

### Reviewer notes

**Round 1 (2026-11-08): changes requested. Not ready for the security
reviewer yet.** The structure is right. One notion of "read" lives in
`core/read_policy.py`, `read_lane.py` holds no rules of its own, and the hook
order is correct: after F3 and the session override, after freeze and the
encoded-payload block. Zero-Trust and freeze win, `on` is server-gated on
7 days of shadow, and the default is `off`. F1 also stood up well to a probe
set run in a scratch dir with synthetic commands. It refuses environment
dumps, `curl`/`wget`, `awk system()`, `sed e`/`w`, `find -exec`/`-delete`,
`xargs`, `dd`, `less`, `tar -c`, `docker inspect` and `kubectl get secret`.

The core premise of the spec's design rule 3 is wrong, and that is the
planner's mistake, not the implementer's. **The gateway runs every command as
root** (the key is in `/root/.ssh/authorized_keys`). "Elevated" (a `sudo`
prefix) therefore says nothing about privilege, and a plain `cat` reaches
everything. With a denylist (`sensitive-path`) as the only guard, these were
**eligible** in the probe:

* any credential-bearing file whose name F1 doesn't know (shell history, an
  app's `config.yml`, a relative `cat config.yml`)
* raw block devices (`head -c … /dev/sdX`)
* `/proc/<pid>/root/…`, `/proc/<pid>/cwd/…` (links into the real filesystem)
  and `/proc/kcore`
* a glob ahead of `..` (`/var/log/*/../../…`): `_elevated_path_allowed` cuts
  the token at `*` before it looks for `..`
* `systemctl show`/`systemctl cat` (they print `Environment=`) and
  `crontab -l`
* `git … log` with `repo-config` set, and `tail -f`/`follows` (never ends):
  F1 sets those flags, but they are not in `READ_BLOCKING_FLAGS`
* `sleep 100000` and `cat /dev/zero`

*Must fix (replaces design rule 3):*

* **R1 Allowlist on top of F1.** Add `READ_LANE_VERBS` to `read_policy.py`,
  a reviewed list of what may auto-run, in two groups:
  1. **Status verbs that take no file operands**: `uptime`, `df`, `free`,
     `ip` (`a`/`addr`/`route`/`link`), `ss`, `ps`, `uname`, `hostname`,
     `lsblk`, `lscpu`, `whoami`, `id`, `date`, `nproc`, `systemctl`
     (`status`/`is-active`/`is-enabled`/`list-units`/`list-timers` only),
     `journalctl` (no `-f`), `docker` (`ps`/`stats --no-stream`/`images`),
     `pct list`/`qm list`, `zpool status`/`list`, `zfs list`.
  2. **File readers** (`cat`, `head`, `tail` without `-f`, `grep`, `wc`,
     `ls`, `stat`, `du`): eligible only when **every** operand, including
     bare and relative words and `--opt=/path` values, is inside the safe
     trees.

  Anything not on the list is ineligible even if F1 says `read`. The list
  is in code and reviewed; it is not an operator setting.
* **R2 Safe trees, for every step.** Apply the path check whether or not the
  step is elevated, because every step is elevated.
  * **Allowed:** `/var/log/`, `/proc/` and `/sys/`, plus exactly
    `/etc/os-release`, `/etc/hostname`, `/etc/hosts` and `/etc/resolv.conf`.
  * **Refused inside `/proc`:** `*/root`, `*/cwd`, `*/fd`, `*/map_files`,
    `*/mem`, `*/environ`, `kcore`, `kmsg`.
  * **Refused anywhere:** `/dev/`.
  * **Refused shapes:** any glob or brace character (`*?[]{}`) in a path
    operand, and any `..` component, checked on the whole token.
* **R3 Flags fail closed.** Any F1 flag blocks unless it is on an explicit
  harmless list, which starts empty. `repo-config` and `follows` then block
  automatically, and so will any flag F1 adds later.
* **R4 No inner targets.** A command with an inner target (`pct exec`,
  `qm guest exec`, `docker exec`) is ineligible. The lane is opted in per
  gateway, and the guest never opted in.
* **Tests:** add every probe class above to the ineligible table, with
  synthetic paths and the IP range `192.0.2.x`. Also add a generated test:
  every eligible case's first verb is in `READ_LANE_VERBS`. Update the
  spec's design rule 3 to say "every step (the gateway runs as root)".

*Should fix:*

* **S1** Cap `journalctl` and `tail` at `-n ≤ 1000` (or require `-n`), so one
  auto-run read cannot dump a whole journal.

Once R1–R4 are in, F4 goes to the security reviewer (§ 2.6a). They asked for
a closer look because this lane runs with no human at all.

**Round 2 (2026-11-08): nearly there; small changes requested.** R1–R4 and S1
are in, and every round-1 probe is now ineligible. A second probe set ran
against the allowlisted verbs, with synthetic commands in a scratch dir. It
confirmed that every `ip`/`date`/`hostname`/`journalctl` write, `ss -K`,
`grep -f`/`--exclude-from`, `/proc/*/task/*/environ` and the
`/proc/self/root|cwd` links are refused. Five gaps remain eligible:

*Must fix:*

* **R5 Recursive grep under `/proc` and `/sys`.**
  * `grep -R` follows `/proc/<pid>/root` into the whole filesystem.
  * `grep -r` reads every `/proc/<pid>/environ` as a regular file, which
    leaks environment secrets.

  Allow `-r`/`-R`/`--recursive` (and `-d recurse`) only when every operand is
  under `/var/log/`, and use `-r` only (refuse `-R` everywhere, because it
  follows links). Apply the same rule to `ls -R` and `du` under `/proc`;
  names only are acceptable, but keep them to `/var/log` for one simple
  rule.
* **R6 `ps` environment output.** A BSD option word containing `e` (`ps e`,
  `ps eww`, `ps auxe`) or `--env`/`-O environ` prints every process's
  environment. Refuse BSD option clusters containing `e` and any
  `-o`/`-O`/`--format` value naming `environ`/`env`. `ps -e`/`-ef` (UNIX
  "all processes") stay eligible.
* **R7 Endless or interval forms.** These never return:
  * `free -s N` and `free -c N`
  * `zpool status|list` with a trailing interval or count, or `-T`
  * `ss -E`/`--events`

  Refuse them. Add a generated test that every eligible table case finishes
  under the sandbox's 2 s limit (or a static assertion per verb, if running
  them is impractical).
* **R8 Other journal sources.** `journalctl --file=`, `--directory=`,
  `--root=`, `--image=` and `-m`/`--merge` read journals outside the
  default; only `-D` is refused today. Refuse the whole set.

*Should fix:*

* **S2** Cap `head -c`/`tail -c` at 1 MiB, as `-n` is capped at 1000.
* **S3** Drop `/sys/firmware/` from the safe trees (EFI variables can carry
  vendor secrets).

*For the security reviewer (no change asked):*

* `ps aux` and `docker ps --no-trunc` show full command lines, which may
  contain secrets passed as arguments. I left them eligible as everyday
  reads.
* The `grep -e` only rule and the absence of `echo` are deliberate
  narrowings. Both are acceptable.

After R5–R8 (plus S2 and S3), F4 goes to the security reviewer.

**Round 3 (2026-11-08): approved by the feature reviewer; waiting on security
sign-off (§ 2.6a).** R5–R8, S2 and S3 are in. I re-ran all four probe sets,
about 130 synthetic commands in a scratch dir. The only eligible commands left
are everyday status reads and log reads under `/var/log`:

* `uptime`, `df`, `free`, `ip a`, `ss -tlnp`, `ps aux`/`-ef`
* `systemctl status`/`list-units`, `journalctl -n ≤1000`
* `docker ps`/`stats --no-stream`, `zpool status`, `lsblk`, `uname`
* `cat`/`grep -e`/`ls -R`/`du` under `/var/log`, and `stat` of the four
  `/etc` files

Pipelines into `grep` without an operand are ineligible. That is narrow but
safe.

**Do not merge until the security reviewer signs off below.**

#### Security reviewer sign-off

**2026-11-08: approved on conditions (security chat, reviewed at `7666adc`).**
This was a read-only design and code review of `core/read_policy.py`,
`core/read_lane.py`, the F4 hook in `receive_request`, the read-lane
functions in `db/gateways.py`, and the parts of F1 (`cmd_parse.py`,
`cmd_rules.py`) that the lane relies on. Nothing was run, because the feature
reviewer had already done the adversarial testing. Threat model: the caller
is an agent that may be prompt-injected, every command runs as root, and in
`on` no human sees the command before it runs.

**What holds:**

1. **Hook position.** The lane runs after the freeze check, the
   encoded-payload block, F3 plans and the session override, and before the
   host override. Freeze and Zero-Trust are checked before the lane and again
   inside `eligible()`. The gateway enforces the hard block and the
   blocklists before a request reaches the dashboard, and the lane checks
   the verdict a second time. The lane is never the first thing to say yes.
2. **Default, gate and audit.** The column defaults to `off`, and any unknown
   value reads back as `off`. The server refuses `on` unless the gateway has
   7 days in shadow, so the UI alone cannot turn it on. Entering shadow
   starts the clock and leaving it clears the clock. Every mode change is
   audited (`read_lane_<mode>`), and so is every shadow tag
   (`jit_read_lane_shadow`) and every auto-approval
   (`jit_read_lane_approved`). Auto-approved rows carry `reason="read-lane"`
   and stay attributable.
3. **One notion of "read".** `read_lane.py` holds gateway-state, size and
   verdict checks only, and it gets its read decision from
   `read_policy.is_read_ssh`.
4. **The outer structure fails closed.** An incomplete breakdown, any F1
   flag (the harmless list is empty), an inner target, a wrapper other than
   `sudo`, a step that is not `read` and a verb that is not on
   `READ_LANE_VERBS` are all refused. Substitution steps carry a `subst`
   context and are refused. F1 re-quotes any word that holds a shell-special
   character (`$`, quotes, backslash, `~`, globs), so such a word no longer
   starts with `/` and fails the path check. Variable and quoting tricks in
   paths therefore fail closed.
5. **Path checks apply to every step.** Elevation plays no part in the path
   check.

**What does not hold yet:** the verb list fails closed, but the options
*inside* each verb do not. Options are allowed unless they are on a short
deny list, matched by exact spelling. Several gaps follow from that. Some of
them let a "read" write a file as root or read any file:

* Some allowed status verbs have options that write a file, read an arbitrary
  file and echo it back in an error message, set the clock or set the
  hostname. Neither F1 nor `read_policy` knows about these options. The
  affected verbs are `journalctl`, `ss`, `date` and `hostname`.
* Long options can be abbreviated (the tools use getopt), and short options
  can be clustered. Both slip past exact-match deny lists. This affects the
  follow, interval, journal-source and clock-set checks.
* `systemctl` and `docker` take a "run against another host or container"
  option that the subcommand check does not see. That breaks R4: the other
  host never opted in.
* `_VALUE_FLAGS` is one list shared by every verb. An option that takes no
  value for *this* verb can therefore hide the next real file from the path
  check. The `grep` recursion check has the same hole.
* `grep` treats every word starting with `.` or `^` as a pattern and skips
  it. Relative dot-files in root's home are then read without a path check,
  and most of them are not on F1's sensitive-name list.
* F1 drops input redirections from the step text, so `read_policy` cannot
  see them. A lone `-` (standard input) is skipped as a flag. Together they
  let a file reader take any file as standard input. The same gap lets a
  command open a FIFO or a bash network pseudo-path.
* The `zpool` branch never checks the subcommand. Today F1 covers it, but
  the policy should not depend on that.
* The self-protection "must go to a human" verdict (`self_protection_jit`)
  is not refused. Only `blocked` and `fatal` are.
* Output is not bounded. `cat` has no cap, `head -n` has no cap, the tail
  cap misses the attached, `--lines=` and "from line N" forms, `journalctl`
  has an option that ignores `-n`, and `systemctl status` takes its own
  line count. The gateway has no execution timeout or output cap, so the
  parser is the only limit.

**Conditions (all before merge):**

* **SC1: options are allowlisted per verb.** Each verb (and each subcommand)
  may use only the options listed for it in `read_policy.py`. Any other
  option makes the command ineligible, and that includes abbreviations and
  unknown spellings. Short-option clusters are allowed only if every letter
  is on the list. Whether an option takes a value is decided per verb. There
  is no shared list. `--` and a lone `-` are refused. No status verb may take
  an option that names a file, a host, a machine, a context or a format
  template.
* **SC2: every other word is a file and is path-checked.** A word that is
  neither a listed option nor that option's value counts as a file operand
  and must pass the safe-tree check. The single exception is `grep`'s
  pattern: the first such word, and only when no `-e` is given.
* **SC3: no input redirection.** F1 must pass each step's redirections
  through to the breakdown, or keep them in the step text. A step with any
  input redirection, here-string or heredoc is ineligible.
* **SC4: the policy checks subcommands itself.** Every status verb with
  subcommands has its subcommand (and for `ip`, the action) checked in
  `read_policy.py`, so the policy does not rely on F1 alone. This includes
  `zpool`.
* **SC5: every eligible command has bounded output and finishes.** Line and
  byte caps apply to every spelling of the option, and "from line N" counts
  as uncapped. `head` gets the same cap as `tail`. Log files under
  `/var/log` are read only through `head`, `tail` or `grep` with a cap
  (`grep -m` ≤ 1000). `cat` stays for `/proc`, `/sys` and the four `/etc`
  files. Any option that undoes a line cap is refused.
* **SC6: tighten the safe trees.** Also refuse `/proc/*/exe` (a link into
  the real filesystem), `/proc/*/pagemap` and `/proc/kpage*` (huge), and
  `/sys/kernel/debug`, `/sys/kernel/tracing` and `/sys/fs/bpf` (some files
  block forever, others dump kernel data). Also refuse
  `/var/log/installer/` and the cloud-init logs, which can hold
  install-time password hashes and user-data.
* **SC7: any "needs a human" verdict wins.** The lane acts only when the
  policy verdict is plain `jit` or `auto_approved`. `self_protection_jit`
  and anything added later are refused.
* **SC8: tests.** Add each class above to the ineligible table, using
  synthetic paths and the `192.0.2.x` range, with no live data. Add a
  generated test that every option in every eligible case is on that
  verb's list.

**Who checks:** SC1–SC3 change how the policy reads a command, so send them
back to the security chat for a short look at the diff. The feature reviewer
can check SC4–SC8.

**Open questions:**

* **(a) `ps aux` and `docker ps --no-trunc`: acceptable.** Linux shows every
  process's command line to every local user by default, so command
  arguments are not a secret channel, and the same data is already readable
  at `/proc/<pid>/cmdline` inside the safe trees. On a Proxmox host this
  includes guest processes. That is metadata, and `pct list` is already
  allowed. Under SC1, `docker ps` may not take `--format`, because templates
  can print labels, and labels often hold auth hashes.
* **(b) The safe-tree list: acceptable with SC6.** The four exact `/etc`
  files are fine. `/var/log` is fine as a tree, with SC6's exclusions and SC5's
  caps. `/proc` and `/sys` are deny lists inside a root-readable tree, which
  is the weakest part of the design. They are acceptable with SC6. A follow-up
  should replace them with a positive list of the status files people
  actually read (`meminfo`, `loadavg`, `pressure/*`, `net/*`,
  `/proc/<pid>/status`, `/sys/class/*`, `/sys/block/*`, `/sys/fs/cgroup/*`).

**Accepted residual risks (write them in the operator docs):**

* Logs contain whatever services write to them, including secrets that apps
  log by mistake. Turning the lane on hands those logs to the agent with no
  human in the loop.
* A service account that owns a directory under `/var/log` can plant a
  symlink, and root follows it. The path check is textual. This needs a
  service account to be compromised first.
* `df` on a stale network mount, or `docker` with a hung daemon, can hang.
  A gateway-side execution timeout for read-lane runs is worth a follow-up
  (F5 or security Chunk 7).

**Not blocking:** `main.py` imports `evaluate_policy_verdict` twice.

**Round 4 (2026-11-08): changes requested (SC5 and SC6 gaps). SC1–SC3
ready for the security reviewer.** Reviewed at `43fd95e`; `bash scripts/verify.sh`: `2120 passed, 7 warnings in 341.83s` (the Round 5
commits `4b3a121`, `0861afe`, `c470d6e`, `43fd95e`). As agreed, this round
checks SC4–SC8; SC1–SC3 go back to the security chat.

**SC1–SC3 are present, each with tests.** They are `OPTION_SPECS`, the
`_scan_options` operand rule, and F1's `input-redirect` flag plus the
per-step `input_redirects`. Tests: `test_f4_sc1_*` (5), `test_f4_sc2_*` (2)
and `test_f4_sc3_*` (2). **SC1–SC3 ready for the security reviewer.**

**What holds:**

* **SC4.** `SUBCOMMANDS`/`ACTIONS` check every status verb's subcommand
  (and `ip`'s action) in the policy itself, `zpool` included. An option or
  value before the subcommand cannot hide one, because the first non-option
  word is taken as the subcommand and refused if it is not listed. "Another
  host" options in either position are refused.
* **SC5, mostly.** Line and byte caps apply to every spelling (`-n`,
  `--lines=`, attached and bare-digit forms). "From line N" and "all but the
  last N" are refused, and `head` matches `tail`. Logs under `/var/log` are
  refused for `cat` and `wc`, and `grep` on a log needs `-m`. `journalctl`
  needs `-n` and has no source or output options.
* **SC6, by spelling.** All the new names are refused as written.
* **SC7.** Only an `auto_approved` verdict or a `jit` verdict with no
  `detail_type` passes, so `self_protection_jit` and anything added later
  are refused. A stored `on` now degrades to shadow while
  `read_lane_on_enabled` is unset (`0861afe`, tested).
* **SC8.** Every class in the sign-off has a table row or a test, and the
  generated option test walks every eligible case. The README carries the
  accepted residual risks.
* **No live data.** The new fixtures use `192.0.2.x` and the existing
  `10.0.0.1` test gateway.

I ran about 140 synthetic commands through `breakdown` → `eligible` in a
scratch dir, with both a bare `jit` verdict and the real
`evaluate_policy_verdict`. Four classes are still eligible and break SC5 or
SC6.

*Must fix:*

* **R1 Paths are not normalised before the SC6 prefix checks.** A path
  spelled with an empty component (`//`) or a `.` component gets past every
  *prefix* denial: `/sys/kernel/debug`, `/sys/kernel/tracing`, `/sys/fs/bpf`,
  `/sys/firmware` and `/var/log/installer`. Only `cat` happens to catch the
  `/var/log` form, through `cat-tree`. The `/proc` checks are not affected,
  because they split on parts. **Fix:** in `_path_ok`, refuse any token with
  an empty or `.` component (or refuse any token where
  `posixpath.normpath(token) != token`, after allowing one trailing slash),
  before any prefix test. **Test:** for every entry in `SYS_DENY_PREFIXES`
  and `LOG_DENY_PREFIXES`, the `//` and `/./` spellings are ineligible for
  `cat`, `head`, `tail`, `grep -m`, `ls` and `stat`.
* **R2 Recursive `grep` on `/var/log` reads the SC6 exclusions.** `grep -r`
  is allowed on any operand under `/var/log`, including `/var/log` itself.
  It then walks into `/var/log/installer/` and the cloud-init logs, which
  SC6 refuses by name, so the check only covers files named on the command
  line. `-m` is also per file, so a recursive grep over the whole tree has
  no overall bound (SC5). **Fix:** refuse recursion on any operand that is
  `/var/log` itself or an ancestor of an SC6 exclusion. Or, more simply,
  take `-r`/`--recursive` out of the lane: `ls -R` and `du` still give the
  names. **Test:** a recursive grep with `/var/log` (with and without a
  trailing slash, and with the R1 spellings) as the operand is ineligible.
* **R3 `grep` context options undo the `-m` cap on logs.** `-A`/`-B`/`-C`
  are capped at 1000 each, independently of `-m`, so a capped log grep can
  still print about `m × (1 + A + B)` lines. That is up to a million, which
  is in effect the whole file. SC5 says "any option that undoes a line cap
  is refused". **Fix:** for a `/var/log` operand, either refuse the context
  options or require `m × (1 + A + B) ≤ 1000` (take the larger of `-C` and
  each of `-A`/`-B`). **Test:** a log grep with `-m` at the cap and a context
  option at the cap is ineligible, and one within the product is eligible.
* **R4 `head -z`/`tail -z` undo the line cap.** `-z`/`--zero-terminated`
  makes NUL the line separator. A text log has no NUL, so `-n ≤ 1000` then
  prints the whole file. **Fix:** remove `-z`/`--zero-terminated` from both
  specs and the `z` letter from both clusters. **Test:** each spelling (short,
  long, inside a cluster) is ineligible for both verbs, under `/var/log`.

*Should fix:*

* **S1 Status verbs with no operands should refuse every operand.** Today a
  word that passes `_path_ok` is accepted after any status verb. For
  `date`, `uptime`, `id`, `whoami`, `nproc`, `uname`, `free`, `lscpu`, `ps`
  and the `docker`/`pct`/`qm` subcommands this is harmless today (the tool
  errors out). But it lets an operand through where none is ever valid, and
  `date`'s operand is a set-the-clock argument. Add an `operands` field to
  `_spec` (none / safe paths), with paths only for `df`, `journalctl`,
  `systemctl status`, `zfs`/`zpool` and `ip … show`. **Test:** each no-operand
  verb with a safe-tree path operand is ineligible.
* **S2 The `zpool` short letters don't match its flags.** The letters
  include `G`, but the flag list has `-g`, so `-G` passes and a cluster with
  `g` does not. Make them the same set. **Test:** the SC8 generated test also
  asserts that every spec's `letters` equals the single-letter `flags`.
* **S3 Many log operands multiply the caps.** One `head -n 1000` with dozens
  of `/var/log` files is within the 8 kB limit and prints dozens of times
  the cap. Consider at most N file operands (say 4) for a `/var/log` read.
  **Test:** N+1 log operands are ineligible.
* **S4 `grep -P` and back-references on logs.** A pathological pattern can
  run for a very long time, and nothing at the gateway stops it (see F19).
  Consider dropping `-P` from the lane until F19 adds a time limit. **Test:**
  `-P`/`--perl-regexp` is ineligible.

*Questions for the security reviewer and the operator (no change asked yet):*

* **Q1 SC7 also refuses `shell_meta`, so a chained or piped read never
  auto-runs.** `evaluate_policy_verdict` returns `jit` with
  `detail_type="shell_meta"` for any command with `;`, `&&` or `|`. Round 5
  reads "plain `jit`" as "no `detail_type`", so every multi-step command is
  now ineligible in the real request path. §1 says about 40% of traffic is
  chained and 40% piped. The eligibility table does not show this, because
  its helper passes a bare `{'action': 'jit'}`. Both readings are safe:
  `shell_meta` only says the *gateway* will not auto-run the command, and
  the lane is decided per parsed step on the dashboard. The question is
  scope. If chained reads should stay eligible, SC7 becomes "the `jit`
  `detail_type` is `None` or `shell_meta`, nothing else". Either way, add an
  end-to-end shadow test through `POST /api/request` with a two-step read,
  so the table and the real path agree.
* **Q2 The implementer's open question (unit names under SC2).** I agree
  with implementing SC2 literally for now. If an exception is wanted,
  scope it to `systemctl status|is-active|is-enabled`, with one name word
  matching `[A-Za-z0-9@_.:-]{1,64}` (no `/`, no leading `-`, no glob).
* **Q3 Wrapper options.** The `sudo` context is accepted with any `sudo`
  options. A login-shell or shell form runs root's shell startup files
  before the command. Consider accepting only a bare `sudo`.

Once R1–R4 are in (S1–S4 are welcome in the same round), the feature
reviewer can approve SC4–SC8. SC1–SC3 go to the security chat now, in
parallel.

**Security round 2 (2026-11-09): SC1 and SC3 hold. SC2 holds as a rule, but
Round 5 opened a new gap (SC9), which comes back here.** Security chat,
reviewed at `24d3833` (code at `4b3a121`, tests at `c470d6e`). This was a
read-only review. Nothing was run.

**What holds:**

* **SC1.** `OPTION_SPECS` has one entry per verb or subcommand and no shared
  list. `_scan_options` matches exact spellings only, so an abbreviation is
  refused. A long flag with `=` is refused, and a cluster passes only when
  every letter is listed. A value-taking letter inside a cluster takes the
  rest of the cluster as its value, and every value goes through a
  validator. `--` and a lone `-` are refused. The "another host or
  container" options are refused in either position, because a word
  before the subcommand is taken as the subcommand and fails the check.
* **SC3.** F1 records input redirections and here-strings per step and
  flags the whole command. Heredocs still set the `heredoc` flag. Process
  substitution is refused through its `procsubst` context. Any flag blocks,
  so the early-return paths in `_parse_statement` (shells, `su`/`runuser`)
  are covered as well.
* **The `on` lock** (`read_lane_on_enabled`, set by hand only, and a stored
  `on` treated as shadow while it is unset) is stricter than I asked for.
  Keep it.

**New gap, must fix (SC9): the shell expands words the policy has already
approved.** Round 5 switched `_step_ok` from splitting on whitespace to
`shlex.split`. F1's lexer has already removed the original quoting, and
`_display` re-quotes any word that holds a shell-special character. In
round 3 such a word started with a quote, failed the path check and was
refused. That was the "variable and quoting tricks fail closed" point in my
round-1 note. `shlex.split` now strips that quote again, so the policy sees
a clean-looking word, while the gateway runs the *original* text through
`bash -c`, which expands it. Two classes are eligible today:

* **Parameter expansion in a path.** A `$` word inside a safe-tree path
  passes the textual checks. At run time it can add `..` components or split
  into several words, through the default `IFS` or an earlier bare
  assignment (an assignment-only statement produces no step at all). That
  gives a root read outside the safe trees.
* **Brace and glob expansion in a `grep` pattern.** Neither the `-e` value
  nor the first-word pattern is checked for shell characters. At run time
  one unquoted pattern can expand into several words, and `grep` reads all
  but the first as files, starting in root's home directory.

None of this sets a metacharacter (`$` alone, braces and globs are not on
the list), so neither SC7 nor the gateway's gate catches it. The tests have
no expansion case. I missed the pattern half in round 1, when the `-e` value
was skipped without any check.

* **SC9 rule:** no word in an eligible step may contain a character the
  shell expands when it is unquoted: `$`, a backtick, `{`, `}`, `*`, `?`,
  `[`, `]`, a leading `~`, or a backslash. This covers path operands, option
  values and patterns alike. The breakdown does not say whether a word was
  quoted, so the rule must hold for quoted words too. Patterns are then
  limited to plain text and `.`/`^` anchors, which is acceptable. If regex
  patterns are wanted later, F1 must first record per word that it was
  entirely single-quoted, and only such a word may carry those characters.
* **Also:** refuse any command with a bare variable assignment statement,
  since it changes what later words expand to and shows up as no step.
* **Tests:** one ineligible row per class (parameter expansion in a path, in
  an option value, and in a pattern; brace and glob expansion in a pattern; a
  bare assignment before an eligible read), using synthetic paths only.
* **Who checks:** this one comes back to the security chat.

**The feature reviewer's Round 6 list (R1–R4): confirmed, and extended.**

* **R1–R4 confirmed** as written. For R2, take the simpler fix: remove
  `-r`/`--recursive` from the lane. `ls -R` and `du` still list names.
* **R5 (add, must): line caps don't bound binary or sparse logs.** Like
  `-z`, a file with few newlines makes `-n ≤ 1000` or `-m ≤ 1000` print most
  of the file. The worst case is the sparse login-record file, which can be
  many gigabytes of zeros. For `head`, `tail` and `grep`, refuse the
  journal directory under `/var/log` (use `journalctl`) and the
  login-record files `lastlog`, `wtmp`, `btmp` and `faillog`. Also refuse
  `grep -a`/`--text`; without it `grep` prints only "binary file matches".
* **S1–S4: agree.** S1 matters most, because `date` takes an operand. S3:
  4 log operands is fine.

**Answers to the three questions:**

* **Q1: yes, allow `shell_meta`.** "Plain jit" meant "no rule says a human
  must look". `shell_meta` means "the gateway cannot vouch for this literal
  string", which is exactly the case the lane exists to parse step by step.
  SC7 becomes: the verdict is `auto_approved`, or `jit` with `detail_type`
  `None` or `shell_meta`, and nothing else. Two additions:
  * Refuse a step sent to the background with `&`, because it can outlive
    the session.
  * Add the end-to-end shadow test through `POST /api/request` with a
    two-step read, as the feature reviewer asked.

  This does not depend on SC9, because the expansion classes carry no
  metacharacter, but ship it in the same round.
* **Q2: yes, allow the narrow named-operand exception.** It applies only to
  `systemctl status`, `is-active` and `is-enabled`. Each name must match
  `[A-Za-z0-9][A-Za-z0-9@_.:-]{0,63}`: the first character is a letter or
  digit, so a name can never be read as an option, and there is no `/`, no
  glob and no backslash. Allow at most 8 names, and the `-n` cap still
  applies to `status`. Escaped unit names (with `\x2d`) stay with a human.
  SC9 must land first or in the same round.
* **Q3: yes, accept only a bare `sudo`.** Every command already runs as
  root, so `sudo` adds nothing. Its options add a login shell (root's
  startup files), a shell, a chroot, a different working directory,
  backgrounding and sudoedit. F1 must record whether the `sudo` wrapper had
  any option or assignment, for example by labelling only the bare form
  `sudo`. The lane refuses every other form.

**Precondition for unlocking `on` on any gateway: the gateway must stop
logging session signatures.** This is about the F12 note, and it is
confirmed. The gateway writes the whole `SSH_ORIGINAL_COMMAND` to syslog
before it strips the prefixes, so `ESHU_SESSION_SIG` ends up in the logs.
That signature covers the session id only, not the command, so it can be
replayed while it is valid. Today a human approves every log read. With
`on`, the agent could read syslog or the journal unattended, pick up
*another* session's signed id, and then use that session's F3 plan items
or session override. This does not block the merge, and shadow is
unaffected because nobody unlocks `on` before this is fixed:

* **Gateway fix (security work, before the unlock):** log the command
  without its signed prefixes (the id alone is fine). This is small and
  belongs in security Chunk 7.
* **Design fix (security Chunk 6):** bind the signature to the command, or
  make it single-use, so a leaked signature is worthless. F12 should take
  this into account.

**Status:** SC1 and SC3 are done. SC2 is done once SC9 is in. When SC9, Q1
and Q3 are implemented, send them back here. The feature reviewer checks
R1–R5 and S1–S4. Q2 is optional, and if it is done, the security chat
looks at it together with SC9.

**Round 7 (2026-11-09, main feature reviewer): feature side approved; SC9,
Q1 and Q3 go to the security reviewer.** I re-ran every probe set from
rounds 1–6 plus a Round 7 set, about 150 synthetic commands.

* **Still eligible:** only status reads (`uptime`, `df`, `free`, `ip a`,
  `ss`, `ps`, `systemctl status <unit>`/`list-units`, `journalctl -n`,
  `docker ps`/`stats --no-stream`, `zpool status`, `lsblk`, `uname`), log
  reads with caps (`tail -n 50 /var/log/syslog`), `ls`/`du`/`stat` of safe
  paths, and chains of these (`uptime && df -h`).
* **Refused:**
  * shell-expansion characters in any word (glob or brace grep patterns,
    `$` in paths);
  * bare and prefix assignments;
  * `&` background steps;
  * `sudo` with options;
  * `wtmp` (and its rotations) and the journal folder;
  * `grep -a`, every recursive grep spelling, `/./` paths, `grep -C`
    beyond the cap, `head -z`;
  * command substitution in a chained step.

The F8 merge also made the read-lane report skip excluded rows, which is
correct. **Still owed before merge:** the security reviewer's look at SC9,
Q1 and Q3 (commits `51f2e40` and `0ed276c`). **Still owed before `on` can be
unlocked:** security Chunk 7 (stop logging signatures), Chunk 6 (bind the
signature to the command) and F19 (gateway time and output limits).

**Security round 3 (2026-11-09): final sign-off. Approved on two small
conditions, which the feature reviewer checks. No return trip.** Security
chat, reviewed at `60045c8` (commits `51f2e40` and `0ed276c`). This was a
read-only review. Nothing was run.

**What holds:**

* **SC9.** Every raw step word is checked before `shlex.split`, so F1's
  re-quoting can no longer be undone. All the expansion characters are
  refused, a leading `~` included, even behind a quote. That covers paths,
  option values and both pattern forms. A bare assignment is now an
  `unknown` step. Prefix assignments were already refused, because the
  assignment word becomes the verb.
* **Q1.** SC7 accepts `auto_approved`, or `jit` with `detail_type` `None` or
  `shell_meta`, and nothing else, so `self_protection_jit` still wins. A
  statement ended by `&` carries `background` and is refused. In a
  backgrounded pipeline, the last member carries the marker, so the whole
  command is still refused.
* **Q3.** Only a bare `sudo` keeps the `sudo` context. Any option, `--` or
  assignment gives `sudo(options)`, and nested `sudo` gives a two-item
  context. The lane refuses both.
* **Q2.** The unit-name exception is scoped as agreed: three subcommands,
  at most 8 names, a letter or digit first, and no `/`, glob or backslash.
* **R1–R5** are in as specified. Paths are normalised before the prefix
  checks, recursive `grep` and `-P` are gone, the context product is
  capped, `-z` is gone, binary logs are refused and `grep -a` is gone.
  `ls -R` stays allowed under `/var/log` only, which lists names and is
  acceptable.

**Conditions (before merge, feature reviewer checks):**

* **SC10: refuse loops and grouping.** F1 drops shell keywords and keeps
  only the commands inside them. Q1 now allows `;` and newlines, so a
  `while` or `until` loop whose condition and body are both eligible reads
  is eligible today. It never ends and prints without limit, which is
  exactly what SC5 forbids. This is a consequence of my Q1 answer. Grouping
  with braces or parentheses also loses the `background` marker, because
  the `&` lands after the closing word. **Rule:** F1 flags any command that
  contains a loop (`while`, `until`, `for`, `select`), a conditional (`if`,
  `case`), a function definition, a brace group or a subshell, for example
  with a `compound` flag. Every flag blocks the lane, so these commands are
  refused. The lane is for plain chains of reads. **Tests:** synthetic
  ineligible rows for a `while` loop of two eligible reads, an `until` loop,
  a `for` loop over a fixed list, a grouped read sent to the background with
  braces, and the same with parentheses. Plain chains (`uptime && df -h`,
  `uptime; free -h`) stay eligible.
* **SC11: date-suffixed login-record rotations.** `_binary_log` matches
  `wtmp.1` but not the dated `name-YYYYMMDD` rotation that some
  distributions use. The failed-login file can hold passwords typed into
  the username prompt by mistake. **Rule:** also match `name-` followed by
  anything. **Test:** a dated rotation of each of the four names is
  ineligible for `head`, `tail` and `grep`.

**Verdict:** F4 may merge once SC10 and SC11 are in and the feature reviewer
has checked them. No further security pass is needed for that. `on` stays
hard-locked until security Chunk 7 (stop logging signatures), Chunk 6
(bind the signature to the command) and F19 (gateway time and output
limits) are merged and deployed. Unlocking it on any gateway is a separate
decision for the operator, made by hand.

**Round 8 (2026-11-09, main feature reviewer): approved and merged.** SC10
holds: every loop, `if`/`case`, function, brace group and subshell is
flagged `compound` and refused, including `{ … & }` and `( … & )`. Plain
chains (`&&`, `;`, `||`) stay eligible, and a keyword inside an argument
(`grep -e for …`) is not a construct. SC11 holds: dated login-record
rotations (`btmp-YYYYMMDD`, `.gz`) are refused. All earlier probe sets give
the same eligible set as in round 7.

**`on` stays hard-locked.** Unlocking it requires all of these to be merged
and deployed: security Chunk 7 (prefix-stripped logging), Chunk 6 (the
signature bound to the command), the session key off argv (the security
reviewer's third precondition) and F19 (gateway time and output limits).
Then the operator sets `read_lane_on_enabled` by hand, per the security
sign-off.


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

**Status: design note only — no code on this branch yet.** Waiting for the
reviewer to mark the design approved before implementing (chunk header).

#### 1. Data flow, end to end

1. **Gateway runs the command** through `run_sanitized` (all four execution
   points — exact allowlist, regex allowlist, JIT ticket/lockbox, and the
   window feature script — funnel through it). `"$@" 2>&1 | sanitize` still
   streams to the agent exactly as today; `sanitize` runs **before** the tee,
   so the captured bytes are already redacted by the gateway's existing rules.
2. **Capture (only when `capture_output=tail`)**: the sanitized stream is
   teed into a bounded FIFO reader that keeps the last 16 KiB (+1 byte, see
   § 3) in a 0600 file under `/run/eshu`. Stdout is untouched: `tee` writes to
   both the agent and the FIFO. The file is removed by an `EXIT` trap.
3. **Post**: after the pipeline, `run_sanitized` computes `rc` and
   `duration_ms`, prints `[ESHU exit=$rc]` exactly as today, then builds the
   JSON body and fires a **background** `POST /api/exec-result` with the
   gateway token. Fire-and-forget, like `log_auto_approve`: no retry, no
   output path that can block or fail the command.
4. **Dashboard receives** it on a new gateway-token endpoint, validates the
   body, runs `core/secret_scrub.py` over the decoded output, caps it, and
   attaches it to a request row:
   * `request_id` present → that row, if it belongs to this gateway and has no
     result yet (unknown/foreign/duplicate ids are rejected, never attached by
     guess).
   * otherwise → the newest `auto-approved` or `window-approved` row for
     `(target_ip, sha256(command))` in the last 120 s **without a result**.
   * nothing matches yet (the `/api/log` call raced the result) → a short-lived
     orphan row, attached when `/api/log` creates the matching request (see
     § 5).
5. **Store** 1:1 in `request_results`; `GET /api/requests` joins the summary
   columns (`exit_code`, `duration_ms`, `truncated`), and the output itself is
   fetched on demand by `GET /api/requests/{id}/output`.
6. **Cleanup** deletes results older than the retention setting in the
   existing cleanup loop; requests are untouched.

#### 2. The exact bash (design to implement)

Two helpers above `run_sanitized`, and a restructured `run_sanitized`. The
JSON-body rule from security chunk 1b is preserved: `TARGET_IP` is validated
at startup, `RESULT_REQUEST_ID` is digits-only or omitted, `rc`/`duration_ms`
are arithmetic integers, `encoded_output` is base64, `truncated` is a literal.

```bash
ESHU_RUN_DIR="${ESHU_RUN_DIR:-/run/eshu}"          # sandbox-relocatable
ESHU_CAPTURE_FILE="${ESHU_CAPTURE_FILE:-/etc/eshu-capture}"
ESHU_OUT_TAIL=""
ESHU_OUT_FIFO=""
ESHU_TAIL_PID=""

capture_mode() {
  # off | exit-only | tail. Anything else (missing file, garbage, a symlink)
  # is exit-only: an unknown value must never turn capture on.
  local v="exit-only"
  if [ -f "$ESHU_CAPTURE_FILE" ] && [ ! -L "$ESHU_CAPTURE_FILE" ]; then
    v=$(cat "$ESHU_CAPTURE_FILE" 2>/dev/null || echo "exit-only")
    case "$v" in off|exit-only|tail) ;; *) v="exit-only" ;; esac
  fi
  printf '%s' "$v"
}

post_exec_result() {   # $1=rc  $2=duration_ms  $3=mode
  local rc="$1" duration_ms="$2" mode="$3"
  local enc_cmd enc_out="" truncated="false" rid=""
  if [ "$mode" = "off" ]; then return 0; fi
  enc_cmd=$(printf '%s' "$cmd" | base64 -w 0)
  if [ "$mode" = "tail" ] && [ -s "$ESHU_OUT_TAIL" ]; then
    if [ "$(stat -c '%s' "$ESHU_OUT_TAIL")" -gt 16384 ]; then
      truncated="true"
      enc_out=$(tail -c 16384 "$ESHU_OUT_TAIL" | base64 -w 0)
    else
      enc_out=$(base64 -w 0 < "$ESHU_OUT_TAIL")
    fi
  fi
  # JSON body: all interpolated values are internal literals, validated
  # (TARGET_IP at startup, request id is digits-only) or base64. Any new field
  # must be validated to a safe charset or JSON-escaped before it is added.
  case "${RESULT_REQUEST_ID:-}" in
    ''|*[!0-9]*) rid="" ;;
    *) rid=',"request_id":'"${RESULT_REQUEST_ID}" ;;
  esac
  { curl --connect-timeout "$HTTP_CONNECT_TIMEOUT" -m "$HTTP_TIMEOUT" -s \
         -X POST "$DASHBOARD_URL/api/exec-result" \
         -H "X-Gateway-Token: ${GATEWAY_TOKEN:-}" \
         -H "Content-Type: application/json" \
         -d '{"target_ip":"'"$TARGET_IP"'","encoded_command":"'"$enc_cmd"'",\
"exit_code":'"$rc"',"duration_ms":'"$duration_ms"',\
"encoded_output":"'"$enc_out"'","output_truncated":'"$truncated"''"$rid"'}'
  } >/dev/null 2>&1 &
}

run_sanitized() {
  local rc=0 mode start_ns end_ns duration_ms
  mode=$(capture_mode)
  if [ "$mode" = "tail" ]; then
    # § 2.7: only the private 0700 work dir; refuse a missing, foreign-mode or
    # symlinked one rather than writing capture files somewhere else.
    if [ ! -d "$ESHU_RUN_DIR" ] || [ -L "$ESHU_RUN_DIR" ] \
       || [ "$(stat -c '%a' "$ESHU_RUN_DIR")" != "700" ]; then
      logger -t eshu-gateway "CAPTURE DISABLED: unsafe $ESHU_RUN_DIR"
      mode="exit-only"
    else
      ESHU_OUT_TAIL=$(mktemp -p "$ESHU_RUN_DIR" eshu-out.XXXXXX) || {
        mode="exit-only"; ESHU_OUT_TAIL=""; }
      if [ -n "$ESHU_OUT_TAIL" ]; then
        ESHU_OUT_FIFO=$(mktemp -u -p "$ESHU_RUN_DIR" eshu-fifo.XXXXXX)
        trap 'rm -f "$ESHU_OUT_TAIL" "$ESHU_OUT_FIFO"' EXIT
        # 16 KiB + 1 tells truncation apart without counting the stream; the
        # reader only ever holds the tail, so a runaway command cannot fill
        # the tmpfs, and `wait` makes the read race-free.
        if mkfifo "$ESHU_OUT_FIFO" 2>/dev/null; then
          tail -c 16385 < "$ESHU_OUT_FIFO" > "$ESHU_OUT_TAIL" &
          ESHU_TAIL_PID=$!
        else
          ESHU_OUT_FIFO=""
          mode="exit-only"
        fi
      fi
    fi
  fi

  start_ns=$(date +%s%N)   # GNU date (coreutils) — the gateway already needs it
  if [ "$mode" = "tail" ] && [ -n "$ESHU_OUT_FIFO" ] && [ -p "$ESHU_OUT_FIFO" ]; then
    "$@" 2>&1 | sanitize | tee "$ESHU_OUT_FIFO" || rc=$?
    wait "$ESHU_TAIL_PID" 2>/dev/null || true
  else
    "$@" 2>&1 | sanitize || rc=$?
  fi
  end_ns=$(date +%s%N)
  duration_ms=$(( (end_ns - start_ns) / 1000000 ))

  if [ "$rc" -ne 0 ]; then
    echo "[ESHU exit=$rc]"
  fi
  post_exec_result "$rc" "$duration_ms" "$mode"
  exit "$rc"
}
```

**Why these choices**

* **`tee` inside the pipeline, not after it.** The chunk asks for the
  sanitized stream, so the tap sits after `sanitize`. `sanitize` and `tee`
  are the last two stages; with `set -o pipefail` the pipeline status is the
  rightmost non-zero — i.e. the command's status in normal operation — so the
  existing `|| rc=$?` and the `[ESHU exit=N]` sentinel keep working. The
  golden test pins this.
* **FIFO + `tail -c 16385` instead of `tee "$file"`.** A plain tee file grows
  with the command (a `journalctl` can emit megabytes) on tmpfs under
  `/run/eshu`; the reader keeps only the tail and the file stays ≤ 16 KiB + 1
  byte. It also avoids `tee >(tail …)`: a process-substitution child is not
  reliably waitable, so reading the file could race the writer. `wait
  "$ESHU_TAIL_PID"` is the fence.
* **16 KiB + 1 byte.** `tail -c 16385` lets the poster decide truncation from
  `stat` alone (`> 16384` → truncated, then take the last 16384) without
  counting the whole stream.
* **The sentinel is outside the capture.** `echo "[ESHU exit=$rc]"` runs after
  the pipeline, so `[ESHU exit=N]` is never part of the stored output (the
  agent still sees it, as today).
* **`set -eo pipefail`.** `|| rc=$?` is the existing guard (no unguarded
  failure can trip `set -e`); `wait … || true` keeps a reader hiccup from
  aborting before the sentinel; the trap removes temp files on every exit
  path, including `exit "$rc"`.
* **`RESULT_REQUEST_ID`.** Set only where the id is in scope: the JIT poll
  path (`REQUEST_ID_INT`, before the `TICKET_FOUND=yes` call). The
  lockbox-claim path, both allowlist paths and the window script do not know
  it — the ticket file is `ts|cmd` only — so they omit it and the dashboard
  matches by `(target_ip, sha256)`. That is a deviation from the chunk's
  "request_id is known for JIT/ticket paths": the *claim-and-burn* path cannot
  know it without changing the ticket format, which is out of scope here.
* **No `python3` on the hot path.** `date`, `mktemp`, `mkfifo`, `tail`,
  `stat`, `base64` are all coreutils; JSON is string concatenation with the
  validated/base64 fields above.
* **Temp files (§ 2.7).** Both names come from `mktemp -p "$ESHU_RUN_DIR"`;
  the data file is created 0600 by `mktemp`, the FIFO by `mkfifo` in the same
  private dir (a `mktemp -u` name is race-free there because the dir is 0700
  root-owned and `mkfifo` fails if anything already exists). Both are removed
  by the `EXIT` trap. The dir is refused if missing, symlinked or not 0700,
  and capture degrades to `exit-only` (never to an unredacted path).

#### 2b. Design round 1: D1–D8 folded in

The design reviewer approved with changes; these are now part of the design.

* **D1 — capture must never cut the agent's output.** `tee -p`
  (`--output-error=warn-nopipe`) keeps stdout flowing when the FIFO reader
  dies (OOM, kill) and keeps the pipeline status; without the option (non-GNU
  `tee`) capture degrades to `exit-only` at startup. The `tee` diagnostic is
  sent to `/dev/null` so a dead reader never injects a line into the agent's
  stream. Test: kill the reader mid-stream; stdout stays byte-identical and
  `rc` is the command's.
* **D2 — output and command off the process list.** The body (up to ~22 KB) is
  piped to `curl --data-binary @-` on **stdin**, never `-d` in argv. The
  token header and `/api/log`'s command in argv are pre-existing exposures and
  go to the § 11 backlog, not this chunk.
* **D3 — the lockbox path must match too.** The gateway sends an
  internal-literal `path` (`allowlist` | `window` | `lockbox` | `jit`). The
  matcher's status set depends on it: `allowlist` → `auto-approved`,
  `window` → `window-approved`, `lockbox`/`jit` → `approved`/`consumed`
  (JIT, session override, F3 plan after a reconnect). An unknown `path` is a
  400 and stores nothing. Test: a lockbox result attaches to an `approved`
  row.
* **D4 — first result wins.** `INSERT OR IGNORE`; a second post returns
  `200 {attached: false, reason: "already_has_result"}` and never overwrites
  the stored output.
* **D5 — the request must be executable.** A `request_id` must also be
  `approved`/`consumed`; a result for `pending`/`denied`/`blocked` is a 400
  (`not_executable`), so a forged post cannot make a denied command look run.
* **D6 — bound the orphan table and rate-limit the endpoint.** At most 50
  orphans per gateway (oldest dropped), `/api/exec-result` goes through
  `_check_rate_limit`, and the endpoint is gateway-token-only via
  `_require_gateway(request, target_ip)` — a dashboard session cannot post.
* **D7 — the run-dir check mirrors the poller**: missing, symlink, mode ≠ 700
  **or owner uid ≠ 0** disables capture (`exit-only`); the gateway never
  creates `/run/eshu` itself.
* **D8 — small bash points.** The setting is read with `IFS= read -r v <
  file` (a trailing newline cannot turn `tail` into `exit-only`); if the FIFO
  exists but the pipeline takes the no-FIFO branch the reader is killed
  (`kill "$ESHU_TAIL_PID"`) so it never sits blocked on `open()`; the `EXIT`
  trap is the first in the script and carries a comment that any later trap
  must extend it.

Corrected capture/post snippet (the `tee` and body lines are the deltas):

```bash
# startup, once
TEE_NOPIPE="0"
if tee -p /dev/null </dev/null >/dev/null 2>&1; then TEE_NOPIPE="1"; fi

capture_mode() {
  local v="exit-only"
  if [ "$TEE_NOPIPE" = "1" ] && [ -f "$ESHU_CAPTURE_FILE" ] \
     && [ ! -L "$ESHU_CAPTURE_FILE" ]; then
    IFS= read -r v < "$ESHU_CAPTURE_FILE" || true   # D8: no newline surprise
    case "$v" in off|exit-only|tail) ;; *) v="exit-only" ;; esac
  fi
  printf '%s' "$v"
}

post_exec_result() {   # $1=rc  $2=duration_ms  $3=mode  $4=path
  ...  # same body build as § 2, plus "path":"'"$path"'"
  # D2: stdin, not argv.
  { printf '%s' "$body" | curl ... --data-binary @- ... ; } >/dev/null 2>&1 &
}

# inside run_sanitized, the FIFO branch (D1, D8)
if [ -d "$ESHU_RUN_DIR" ] && [ ! -L "$ESHU_RUN_DIR" ] \
   && [ "$(stat -c '%a' "$ESHU_RUN_DIR")" = "700" ] \
   && [ "$(stat -c '%u' "$ESHU_RUN_DIR")" = "0" ]; then
  ...
  "$@" 2>&1 | sanitize | tee -p "$ESHU_OUT_FIFO" 2>/dev/null || rc=$?
  wait "$ESHU_TAIL_PID" 2>/dev/null || true
else
  if [ -n "$ESHU_TAIL_PID" ]; then kill "$ESHU_TAIL_PID" 2>/dev/null || true; fi
  "$@" 2>&1 | sanitize || rc=$?
fi
```

#### 3. `capture_output` setting (policy → `/etc/eshu-capture`)

* New per-gateway policy field `capture_output: "off" | "exit-only" | "tail"`,
  default `exit-only`, delivered in the policy poll like `zero_trust`.
* Poller: after the policy JSON is validated, write the value atomically
  (`/etc/eshu-capture.tmp` → `mv`) — same pattern as `/etc/eshu-zero-trust` —
  and delete the file when the field is absent (old dashboard), which the
  gateway treats as `exit-only`.
* The dashboard validates the setting (the three literals) on write and in the
  policy response; the gateway re-validates (it is the enforcement point).
* `off` posts nothing at all (not even an exit code); `exit-only` posts the
  row with `encoded_output: ""`, `output_truncated: false`.

#### 4. Storage, matching and size caps

**Table** (`db/core.py` init, new `db/exec_results.py`):

```sql
request_results(
    request_id INTEGER PRIMARY KEY,   -- one result per request
    exit_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    output TEXT NOT NULL DEFAULT '',
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
)
```

`INSERT OR IGNORE` on `request_id` (D4): the first stored result wins and a
retry is a harmless `attached: false`.

**Caps**

| Where | Cap |
|---|---|
| Gateway stream → file | 16 384 B (+1 sentinel byte) |
| Gateway body | base64 output ≈ 21.9 KB + envelope < 24 KB |
| Dashboard request body | 64 KB (`Content-Length` checked before parse → 413) |
| Stored output | 16 384 B (the dashboard re-caps, keeping the tail, and forces `truncated=1` if it had to cut) |
| Retention | 30 days default, 1–365, `meta` key `exec_result_retention_days` (Settings input) |

**Matching rules** (`db/exec_results.py`)

1. With `request_id`: the request must exist, be `target_ip`-owned, still have
   no result, and be `approved` or `consumed` (D5). Unknown → 400
   `unknown_request`; wrong state → 400 `not_executable`; a second post →
   200 `{attached: false, reason: "already_has_result"}` (D4, `INSERT OR
   IGNORE`, the stored output is never overwritten). Never attach by guess.
2. Without it: newest row for `(target_ip, sha256(command))` created in the
   last 120 s with the status set selected by `path` (D3): `allowlist` →
   `auto-approved`; `window` → `window-approved`; `lockbox`/`jit` →
   `approved`/`consumed`; with no `request_results` row. `path` is an
   internal literal; an unknown value is a 400. `sha256` is computed on the
   decoded command — the same bytes `/api/log` stored.
3. Nothing yet: insert an `exec_result_orphans` row
   (`target_ip, cmd_sha, exit_code, duration_ms, output, truncated, created_at`).
   `/api/log` calls `attach_orphan(target_ip, sha256(cmd), new_request_id)`
   right after it creates an `auto-approved`/`window-approved` row, oldest
   orphan first, deleting it on attach. Orphans are also swept by the cleanup
   loop after 120 s.

The orphan buffer exists because `log_auto_approve` and the result post are
both background curls: on a fast command the result can arrive before
`/api/log` has created the row it belongs to. Dropping it would lose exactly
the executions the operator most wants to see.

**Why a separate output endpoint.** Output is up to 16 KiB per row; the
history list can return 200 rows. `GET /api/requests` joins only the summary
columns; `GET /api/requests/{id}/output` (session auth) returns
`{request_id, exit_code, duration_ms, truncated, output}`.

#### 5. Redaction

1. **Gateway (`sanitize`, unchanged, first pass)** — the tap is after it, so
   the temp file never contains the secrets the gateway already strips. This
   is why the capture cannot be taken from the raw command stream.
2. **Dashboard (`core/secret_scrub.scrub_body`, second pass)** — applied to the
   decoded output before storage, catching gateway-side misses (broader
   patterns, tokens in JSON blobs). Stored bytes are the scrubbed bytes; the
   API returns the stored bytes; the response never re-scrubs (idempotent).
3. The audit event records `request_id`, `exit_code`, byte length and whether
   it was truncated — never the output, and never the command text (the
   request row already has it).
4. The output is agent-controlled text: the UI escapes it (`escapeHtml`) and
   renders it in a `<pre>`-style block with a max height; a node-harness test
   proves the escaping, like F2/F7.

#### 6. Retention

* `meta.exec_result_retention_days` (default 30, clamped 1–365) read by the
  existing cleanup loop (`_stale_gateway_cleanup_loop`), which already runs on
  a timer: `DELETE FROM request_results WHERE created_at < now - days*86400`
  and the same for orphans older than 120 s.
* Deleting a result never deletes or changes the request row; the UI simply
  loses the badge.
* The setting gets a small field in Settings next to the other dashboard
  settings; the default works with no UI interaction.

#### 7. UI

* **History rows**: `exit 0` (calm) / `exit N` (warning) badge and a duration
  (`1.2 s`) next to the status chip, only when a result exists; a `show output`
  toggle on the row's detail area (or the context panel) fetches the output
  once and renders it escaped in monospace with a max height, next to a
  `truncated` note when the tail was cut.
* **Session modal**: the same badge/duration per command row, and the plan /
  session timeline keeps working with no result.
* **Session summary**: `get_session_summary` adds `failed` — the count of this
  session's requests with a result and `exit_code != 0` — next to the existing
  outcome counters.
* No new auto-approve path, no change to what runs; display only.

#### 8. Compatibility (old gateway ↔ new dashboard, and back)

* **Old gateway + new dashboard**: it never calls `/api/exec-result`, so no
  results, no badge, nothing else changes. `/api/requests` still returns the
  same fields (the result columns are additive, `NULL` when absent).
* **New gateway + old dashboard**: `POST /api/exec-result` 404s; the post is
  fire-and-forget (`>/dev/null 2>&1 &` alongside `|| true`), so the command,
  its output, its exit code and the sentinel are unchanged. No retry queue.
* **New dashboard + old poller**: the old poller never writes
  `/etc/eshu-capture`; the gateway defaults to `exit-only`, so results carry
  exit/duration but no output until the fleet updates.
* **`capture_output` in the policy JSON** is additive; old gateways ignore
  unknown keys. The dashboard must send it on every policy response (default
  `exit-only`) so a gateway that used to capture is told to stop when the
  operator changes it.
* **Installers**: `dashboard/eshu-gateway.sh` changes → `gen_installer.py`
  regenerates both `dashboard/eshu-gateway-install.sh` and
  `dashboard/static/eshu-gateway-install.sh` in the **same commit**;
  `dashboard/static/dev/` is never touched (§ 2.2). The golden installer test
  must stay green.

#### 9. Privacy (§ 2.5)

Captured output is the most sensitive data this system will hold: real command
output can contain hostnames, paths, tokens and personal data. Therefore:

* Fixtures and tests are **synthetic** only: `192.0.2.x` / `198.51.100.x`,
  `web01`, invented `journalctl` lines, no real captures, no copy of a live
  database.
* `eshu.db` is never committed (already ignored); the new table must not
  appear in any fixture dump.
* The design stores the tail only, scrubbed twice, for 30 days by default —
  the shortest useful window, not "forever".
* Screenshots for this chunk (if any) use the synthetic sandbox output.

#### 10. Test list (every item becomes `test_f5_*`)

Gateway sandbox (`tests/test_gateway_script.py` extended: the fake `curl`
records the body, `ESHU_RUN_DIR` and `ESHU_CAPTURE_FILE` are relocated to
`tmp_path`, no root/network):

1. `test_f5_gateway_exit_zero_posts_result` — exit 0, `exit_code=0`, duration
   present, no sentinel in the body.
2. `test_f5_gateway_nonzero_exit_posts_result` — `false`; `exit_code=1`; the
   agent still sees `[ESHU exit=1]` and the body's output does not contain it.
3. `test_f5_gateway_tail_is_last_16k` — 20 KiB output; posted output is the
   last 16 384 bytes, `output_truncated=true`; 16 KiB exactly is not flagged.
4. `test_f5_gateway_capture_off_posts_nothing` — `/etc/eshu-capture=off`; no
   `/api/exec-result` call at all.
5. `test_f5_gateway_exit_only_has_no_output` — empty `encoded_output`,
   `truncated=false`, exit/duration still correct.
6. `test_f5_gateway_agent_output_unchanged` — the golden regression: stdout +
   stderr bytes for a mixed command are identical with capture on/off
   (byte-compare against the pre-change output).
7. `test_f5_gateway_jit_result_carries_request_id` — the JIT poll path sends
   `request_id`; the lockbox/allowlist paths omit it.
8. `test_f5_gateway_sanitize_before_capture` — a token-shaped value is
   `[REDACTED]` in the posted output (the gateway pass runs first).
9. `test_f5_gateway_temp_files_removed` — no `eshu-out.*` / `eshu-fifo.*`
   left in the run dir after the command; and
   `test_f5_gateway_unsafe_run_dir_disables_capture` — missing/symlinked/
   0700-missing dir → no capture, command still runs and prints normally.
10. `test_f5_gateway_bad_capture_value_is_exit_only` — garbage/link content
    never enables `tail`.
11. `test_f5_gateway_body_fields_are_safe` — hostile session/execution ids
    are refused before any post (security chunk 1 rules still hold).

Dashboard (FastAPI fixtures, hermetic):

12. `test_f5_match_allowlist_by_sha` — result without `request_id` attaches to
    the newest matching `auto-approved` row for that gateway.
13. `test_f5_match_window_row` — same for `window-approved`.
14. `test_f5_no_double_attach` — two identical commands: each result lands on
    its own row, newest-without-result first.
15. `test_f5_unknown_request_id_rejected` — unknown id → 400, stored nowhere;
    another gateway's id → rejected.
16. `test_f5_duplicate_result_is_idempotent` — same `request_id` twice keeps
    one row (no duplicate, no error).
17. `test_f5_result_before_log_is_orphaned_then_attached` — post first,
    `/api/log` second: the orphan attaches to the new row and is deleted.
18. `test_f5_scrub_applied` — a token in the output is scrubbed before
    storage; the API returns the scrubbed text.
19. `test_f5_output_cap_server_side` — oversized output is cut to the tail and
    `truncated=1`.
20. `test_f5_retention_cleanup` — old results and old orphans deleted, fresh
    ones kept; the request rows survive.
21. `test_f5_output_endpoint_requires_session` — 401/403 without a session;
    404 for a request with no result.
22. `test_f5_session_summary_failed` — `failed` counts only `exit_code != 0`.
23. `test_f5_old_gateway_still_works` — a dashboard with no results serves the
    history/session views unchanged (additive fields `NULL`/absent).
24. `test_f5_new_gateway_against_old_dashboard` — a 404 from the endpoint does
    not alter the command's output or exit code (sandbox: fake curl 404).
25. UI (node harness on `appjs_loader.js`, F2/F7 pattern):
    `test_f5_output_escaped` (agent output with `<img onerror>` is escaped in
    the rendered block) and `test_f5_exit_badge_rendered` (exit 0 / exit 7 /
    no result).

Also required by the acceptance criteria: the golden installer test stays
green after `gen_installer.py`, and `bash scripts/verify.sh` is green with the
new tests.

#### 11. Deliberately not done / open for the reviewer

* No change to the ticket-lockbox format, so the claim-and-burn path still
  matches by `(target_ip, sha256)` instead of carrying a request id.
* No live streaming of output to the dashboard (post-execution tail only).
* No per-result encryption at rest beyond the existing SQLite file
  permissions; the retention window is the control.
* No UI for the retention setting beyond one number field (default 30).
* `capture_output` is per gateway, not per session; a session can't opt in.
* Backlog for the security owner (D2): the gateway token header and the
  command in `/api/log`'s argv remain visible in `/proc/*/cmdline`; F5 moves
  only the exec-result body to stdin. Streaming output live, encrypting it at
  rest and per-session capture are also backlog (§ 7 of the design).


### Implementation (folded in after the design review)

The D1–D8 points are part of the design now (§ 2b); this is what landed.

**Gateway** (`dashboard/eshu-gateway.sh`, installers regenerated in the same
commit)
* `capture_mode()` reads `/etc/eshu-capture` with `IFS= read -r` (D8) and
  accepts only `off | exit-only | tail`; a missing/garbage/symlinked file or a
  `tee` without `-p` is `exit-only` (D1).
* `run_sanitized` tees the sanitized stream through a 0600 `mktemp` file and a
  FIFO read by `tail -c 16385` (bounded tmpfs, race-free `wait`), checks the
  run dir is 0700 **and** uid 0 (D7), keeps the `EXIT` trap first with a
  comment that later traps must extend it (D8), kills the reader if it has to
  skip the FIFO branch (D8), and never touches stdout: `tee -p` ignores a dead
  reader, with its diagnostic on `/dev/null` (D1).
* `post_exec_result` builds the body with `path` (D3) and the digits-only
  `request_id`, then pipes it to `curl --data-binary @-` on **stdin** (D2).
  Call sites set `RESULT_PATH` (`allowlist`, `lockbox`, `jit`, `window` in the
  feature script) and the JIT poll path sets `RESULT_REQUEST_ID`.
**Poller**: writes `/etc/eshu-capture` atomically from the policy field,
defaulting to `exit-only` for absent/garbage. **Dashboard**: policy response
carries `capture_output`; `POST /api/gateways/{ip}/capture` sets it; the
gateway table has a small `off/exit/tail` select.

**Dashboard**
* `db/exec_results.py` + tables in `db/core.py`; gateway-only
  `POST /api/exec-result` (`_require_gateway` is not enough — D6 requires the
  token outright), rate-limited.
* Matching (D3): `request_id` → must belong to the gateway and be
  `approved`/`consumed` (D5); otherwise the `path` picks the status set
  (`allowlist` → `auto-approved`, `window` → `window-approved`,
  `lockbox`/`jit` → `approved`/`consumed`) and the newest unresulted row for
  `(ip, sha256(exact command))` in 120 s wins. No match → a bounded orphan
  (50/gateway, 120 s) attached by `/api/log` when the row appears. First result
  wins (`INSERT OR IGNORE`, `attached: false` on a retry — D4).
* Output is scrubbed with `secret_scrub.scrub_body` before storage, capped at
  16 KiB (the tail, `truncated=1`), and cleaned by the existing loop with
  `meta.exec_result_retention_days` (default 30, 1–365).
* `get_all_requests` / `search_requests` / `get_session_requests` join the
  summary columns (`exit_code`, `duration_ms`, `output_truncated`); the output
  itself is `GET /api/requests/{id}/output` (session auth, on demand).
* UI: `exit N` + duration on history rows and in the session modal, an
  "output" button opening an escaped monospace modal (`execOutputHtml`), a
  capture select on the gateway row. `get_session_summary` adds `failed`.

**Two existing tests changed (with reasons in the commit message)**
* `tests/test_gateway_script.py::test_sec_chunk1_allowlist_log_body_is_well_formed`
  now selects the `/api/log` calls only — the gateway legitimately makes a
  second post (`/api/exec-result`) with a different body shape.
* `tests/test_poller.py::test_first_run_parses_once_per_cycle` expects 17
  python starts instead of 16: the new `capture_output` field is parsed in the
  same stale-policy branch; the steady-state test still proves a clean cycle
  starts none.

**Surprises / decisions**
* `auto-approved` rows (the allowlist path) are created by the *background*
  `/api/log` call, so they cannot carry a `request_id`; that path always
  matches by sha. The JIT poll path is the only one that knows the id; the
  lockbox claim path cannot (ticket is `ts|cmd`), which D3's `path` handles.
* The result post is fire-and-forget: a 404 (old dashboard) or a dead
  connection changes nothing about the command (tested).
* The retention setting has no Settings input yet; the default 30 days and
  `set_retention_days()` are in place, and the cleanup loop reads it.

**Deliberately not done**
* No live streaming, no encryption at rest beyond file permissions, no
  per-session opt-in (all listed as backlog in the design).
* No Settings number field for retention (see above).
* The token header/argv exposure noted in D2 stays as backlog (only the exec
  body moved to stdin).

**Tests** — `tests/test_f5_exec_results.py` (37): the whole § 10 list, plus
D1 (dead reader keeps stdout), D3 (lockbox attaches to an `approved` row),
D4 (idempotent), D5 (non-executable rejected), D6 (orphan cap, session cannot
post), and the poller capture tests (3) in `tests/test_poller.py`.

**Screenshots** (synthetic `web01` / `192.0.2.x` fixtures)

* The exit badge and duration on a history row (`exit 1 · 3.1 s` with the
  output link):
  ![Exit badge](../screenshots/f5/exec-badge-history.png)
* The "show output" view (escaped, monospace, max height):
  ![Command output](../screenshots/f5/exec-output-modal.png)

`bash scripts/verify.sh` (pre-commit green):

```
1779 passed, 7 warnings in 358.58s (0:05:58)
```

(1739 before this chunk + 37 F5 tests + 3 poller tests.)

### Round 2 — fixes for code review round 1 (E1–E4)

**E1 — capture no longer keeps commands alive after the agent disconnects.**
Reverted `tee -p` to plain `tee`, and made the reader unable to stall the pipe
instead: `{ tail -c 16385 > "$ESHU_OUT_TAIL" || true; cat > /dev/null; } <
"$ESHU_OUT_FIFO" &` — if `tail` dies, `cat` keeps draining. The `tee -p`
startup probe is gone (one fewer fork per command), and the disconnect
behaviour is back to today's: the next write after the agent leaves gets
SIGPIPE. Tests: `test_f5_gateway_disconnect_ends_command_in_tail_mode` and
`test_f5_gateway_disconnect_ends_command_in_exit_only_mode` (endless `yes`,
the reader closed after 16 bytes, the process exits within 2 s with 141), and
`test_f5_gateway_dead_reader_keeps_stdout` (the `tail` process dies, `cat`
drains, stdout stays byte-identical). Installers regenerated in the same
commit; `static/dev/` untouched.

**E2 — request body caps.** `ExecResultPayload.encoded_output` is capped at
24 576 chars (16 KiB raw + base64 envelope) and `encoded_command` at 16 384 —
the `/api/log` limit. Test: `test_f5_oversized_body_is_422` (both fields).
The server-side re-cap test now sends 17 KiB raw, because anything over the
base64 cap is a 422 before it can be truncated.

**E3 — no audit row per execution.** Dropped the `exec_result` audit event;
the results table is the record (a command's execution is not a decision).
Test: `test_f5_no_audit_event_per_result`.

**E4 — a wider lockbox window.** The `lockbox` path matches within 300 s
(JIT wait up to 90 s + ticket life 90 s + reconnect slack); `allowlist` and
`window` stay at 120 s. Test: `test_f5_lockbox_match_window_is_wider` — a
200-second-old `approved` row attaches via the lockbox path, while an equally
old `auto-approved` row is orphaned by the allowlist path.

Full gate:

```
1784 passed, 7 warnings in 360.54s (0:06:00)
```

(1779 before this round + 5 new tests: two disconnect, oversized body, no
audit event, lockbox window.)

### Reviewer notes

**Design round 1 (2026-11-08): approved with changes.** Fold D1–D8 into the
design section, then implement on this branch. No second design round is
needed; I check D1–D8 in the code review. The design is sound. The
sanitize-then-tee position, the FIFO plus `tail -c 16385` bound, the sentinel
staying outside the capture, the `/etc/eshu-capture` fail-safe and the orphan
buffer are all the right calls.

*Must address:*

* **D1 Capture must never cut the agent's output.** With plain `tee`, if the
  FIFO reader dies (OOM, killed), tee's next write to the FIFO raises SIGPIPE
  and tee exits, so the agent's stream is truncated and `rc` becomes 141.
  * Use `tee --output-error=warn-nopipe` (`-p`), so a dead FIFO is ignored and
    stdout keeps flowing.
  * Check at startup that the option exists. If it doesn't (non-GNU tee),
    capture degrades to `exit-only`.
  * Test: kill the reader mid-stream; stdout stays byte-identical and `rc` is
    the command's.
* **D2 Output and command off the process list.** `curl -d '…'` puts the
  base64 output, up to ~22 KB of sanitized command output, into argv, and any
  local user can read argv in `/proc/*/cmdline`. Send the body on stdin
  (`printf '%s' "$body" | curl --data-binary @- …`). The token header and
  `/api/log`'s command in argv are an existing exposure and out of scope.
  Note them in §11 for the security backlog.
* **D3 Lockbox-claim results must match.** That path runs rows that are
  `approved`/`consumed` (JIT, session override, F3 plan after a reconnect).
  Rule 2 only looks at `auto-approved`/`window-approved`, so those results
  would be orphaned, then dropped. The acceptance criterion "visible for JIT …
  and plan paths" fails.
  * Send an internal-literal `path` field: `allowlist` | `window` | `lockbox`
    | `jit`.
  * Rule 2 then picks the status set per path: `lockbox` matches
    `approved`/`consumed` rows for `(ip, sha256)`, decided in the last 120 s.
  * Unknown `path` → 400.
  * Add the test.
* **D4 First result wins.** §4 says `INSERT OR REPLACE` and rule 1 says 409,
  but test 16 says idempotent; these contradict each other. Use
  `INSERT OR IGNORE`, return 200 `{attached: false, reason: "already_has_result"}`,
  and keep the first stored output. A later post must never overwrite a
  stored result.
* **D5 Rule 1 checks status too.** A `request_id` must also be in an
  executable state (`approved`/`consumed`). A result for a `pending`,
  `denied` or `blocked` row is rejected, so a forged post cannot make a denied
  command look as if it ran.
* **D6 Bound the orphan table.** Cap orphans per gateway (e.g. 50, oldest
  dropped). Apply the existing `_check_rate_limit` to `/api/exec-result`. The
  endpoint is gateway-only via `_require_gateway(request, target_ip)`, so the
  token is bound to the IP like `/api/log`. A dashboard session cannot post
  results.
* **D7 Run dir checks mirror the poller.** Besides missing, symlink and mode
  700, also require owner uid 0 (`stat -c '%u'`), as `eshu-poller.sh` does.
  The poller creates `/run/eshu`. If the gateway runs before the poller has
  started after a boot, capture is simply `exit-only`; the gateway must not
  create the dir itself.
* **D8 Small bash points.**
  * Read the setting with `IFS= read -r v < "$ESHU_CAPTURE_FILE"`, so a
    trailing newline from any writer doesn't turn `tail` into `exit-only`.
  * If `mkfifo` succeeds but the pipeline later takes the no-FIFO branch, kill
    the reader (`kill "$ESHU_TAIL_PID"`) so it doesn't sit blocked on `open()`
    forever.
  * The `EXIT` trap is the first in the script. Put a comment there saying any
    later trap must extend it, not replace it.

*Answers to §11:*
* **Ticket format:** keep it as is; D3 covers it.
* **Streaming, encryption, per-session opt-in:** agreed out of scope; list
  them in §7 backlog.
* **Single retention field:** fine.
* **Security sign-off:** not required for F5 (§2.6a list), since it adds no
  new approval path. The gateway change runs as root on every command, though,
  so I review the bash line by line.

**Code round 1 (2026-11-08): changes requested.** I read the gateway bash
line by line. D2–D8 are done well: stdin body, `path` literal, first result
wins, executable-only ids, bounded orphans, gateway-token-only endpoint, the
uid-0 run dir and `read -r`.

*Must fix:*

* **E1 `tee -p` keeps commands alive after the agent disconnects.** This is
  my D1 advice going wrong. With `--output-error=warn-nopipe`, tee also
  ignores the broken pipe to the agent's stdout (it is a pipe too), so it
  keeps reading and the command never receives SIGPIPE. In `tail` mode an
  agent that disconnects from `journalctl -f` or `tail -f` leaves a root
  process running forever. Today it dies at its next write. Reproduced with
  synthetic commands:
  * `yes | sed '' | tee -p FIFO | head -c 10` was still running at the 8 s
    timeout.
  * The same pipeline with plain `tee` stopped at once.

  Fix:
  * Go back to plain `tee "$ESHU_OUT_FIFO"`, and make the reader unable to
    stall the pipe instead:
    `{ tail -c 16385 > "$ESHU_OUT_TAIL" || true; cat > /dev/null; } < "$ESHU_OUT_FIFO" &`.
    If `tail` dies, `cat` keeps draining.
  * Drop the `tee -p` startup probe (one fewer fork per command).
  * Tests:
    * **Disconnect test:** in `tail` mode, an endless command whose stdout
      reader closes after a few bytes ends within 2 s, with `rc` 141. Add the
      same check for `exit-only`, to pin today's behaviour.
    * **Dead-reader test:** kill the `tail` process, not the group; stdout
      stays byte-identical.
* **E2 The 64 KB request cap from the design is missing.** No body limit
  exists for `/api/exec-result`. Add `Field(max_length=…)`:
  * `encoded_output`: 24 576
  * `encoded_command`: the same limit `/api/log` uses (or 16 384 if there is
    none)

  Test that an oversized body gets 422.

*Should fix:*

* **E3 Drop the per-result audit event.** Every executed command, allowlist
  ones included, now writes an `exec_result` audit row. That doubles audit
  volume with no decision in it; the results table is the record. Remove
  the event, or keep it only for `exit_code != 0` (your choice), and update
  the test.
* **E4 Lockbox match window.** Rule 2 uses `created_at >= now - 120`. A JIT
  request can be created up to `JIT_WAIT_SECONDS` (90 s) before approval,
  and its ticket lives another 90 s, so a lockbox claim after a reconnect can
  miss. For the `lockbox` path, use 300 s. Add a test with a row created
  200 s ago.

**Code round 2 (2026-11-08): approved and merged.** E1–E4 are fixed as
asked. I re-ran the disconnect check with synthetic commands: the drained
reader with plain `tee` stops immediately. 181 F5, gateway-script, poller
and golden tests pass locally; implementer's full run 1784 passed. Installers
regenerate with no diff, and `static/dev/` is untouched. No security sign-off
is needed (§ 2.6a). The backlog keeps the argv exposure of the gateway token
and of `/api/log`'s command (§ 11).

---

## Chunk X1 — Sidebar actions and pending-only decisions · slug `ctx-actions` · dashboard · BUG

### Problem (found by the operator, 2026-11-08)

Open a request in the right-hand sidebar (`openContextPanel`) and press
Approve: the sound plays and nothing happens. Two causes in `app.js`:

1. **Wrong branch for integration calls.** The action buttons use
   `if (req.status === 'pending' || !req._type)`. Pending integration calls
   have `status: 'pending'`, so they take the SSH branch:
   `handleAction(<integration id>)` POSTs `/api/approve/<integration id>`, the
   **SSH** endpoint. Usually that id matches nothing, so nothing happens.
2. **F2's two-press rule from the sidebar.** For a long command with an
   unknown step, the first press highlights that step in the queue card
   behind the panel; the sidebar itself shows nothing.

Behind cause 1 is a backend gap. `POST /api/approve/{id}` and
`/api/deny/{id}` call `update_request_status` **without checking the current
status**. An approve can therefore move a `denied`, `expired`, `consumed` or
`blocked` row back to `approved`. If an integration-call id happens to equal
an old SSH request id, the sidebar re-approves that old command without
saying so.

### Changes

* **UI:** dispatch on `req._type` first (`integration` / `window` / SSH), then
  on status. For integration calls use `approveIntegrationCall` and
  `denyIntegrationCall`.
* **UI:** when the sidebar's approve press arms F2's two-press rule, the
  sidebar shows it on its own button ("Press again to approve") and
  highlights the unknown step in its own breakdown, not only in the card.
* **UI:** surface a failed approve/deny (non-2xx) as an error toast in
  `handleAction`; today a failure is silent.
* **API:** approve and deny act only on a `pending` row. Do it with a
  conditional update (`UPDATE … WHERE id = ? AND status = 'pending'`) and
  return 409 `not_pending` otherwise. Audit only real transitions.

### Tests to add

* Node harness (F2 pattern): opening a pending **integration** call and
  pressing Approve calls the integration endpoint, never `/api/approve/`.
  The same check for window requests and SSH requests.
* Harness: a long-unknown SSH request in the sidebar needs two presses, and
  the sidebar button shows the armed state.
* API: approving or denying a `denied`, `expired`, `consumed` or `blocked`
  row returns 409 and leaves the status unchanged; pending → approved still
  works; there are no audit events for refused transitions.
* API: a request approved twice gets 409 on the second call.

### Acceptance criteria

- [ ] All tests above pass.
- [ ] No other approval path changes: bulk approve, the session override and
  F3 plans keep their own routes. Check that bulk approve and the history
  table still work against the 409 rule.
- [ ] Reviewer notes tell the security reviewer about the backend gap (FYI;
  no sign-off needed).

### Implementer notes

**What I did**

* **UI dispatch (`app.js`).** The sidebar buttons now come from
  `contextActionsHtml(req)`, which dispatches on `_type` first:
  integration → `approveIntegrationCall`/`denyIntegrationCall`, window →
  `approveWindowReq`/`denyWindowReq`, SSH pending → `handleAction`, anything
  else → Close. Pending integration calls no longer fall into the SSH branch
  and no longer POST to `/api/approve/<integration id>`.
* **Two-press in the sidebar.** `contextApproveLabel(req)` returns
  "Press again to approve" once F2's arming press has set
  `_bdUnknownConfirmed[id]`; `handleAction` calls `armSidebarConfirm(id)`,
  which re-renders the sidebar buttons and flashes the sidebar's **own**
  unknown step (`#ctx-breakdown-steps`), not only the queue card behind it.
* **Visible failures.** `handleAction` now shows an error toast on any
  non-2xx from approve/deny (`Could not approve request #N: not_pending`) and
  refreshes the queue, instead of silently doing nothing.
* **API (`main.py`, `db/requests.py`).** `POST /api/approve/{id}` and
  `/api/deny/{id}` act only on a `pending` row, via
  `update_request_status_if_pending`
  (`UPDATE requests SET status = ? WHERE id = ? AND status = 'pending'`,
  `rowcount` checked). Unknown id → 404; any other state → 409 `not_pending`;
  the audit event is written only after a real transition.

**Notes**

* Bulk approve (one POST per selected pending id) and the history table are
  unchanged and still work against the 409 rule — covered by
  `test_x1_bulk_approve_sequence_still_works`.
* No other approval path changed: session overrides, F3 plans and window
  approvals keep their own routes; only the generic approve/deny handlers
  became pending-only.

**For the security reviewer (FYI, no sign-off needed).** The backend gap: the
old handlers called `update_request_status` unconditionally, so a stale click
or a colliding integration-call id could move a `denied`, `expired`,
`consumed` or `blocked` SSH row back to `approved` (and, once approved, the
gateway would claim and run it). It is now a conditional update with a 409 and
no audit on refusal.

**Tests** — `tests/test_x1_ctx_actions.py` (18): the sidebar harness
(dispatch for integration/window/SSH/closed, and the two-press armed label
with zero then one POST) plus the API cases (pending approve/deny still work;
approve refused for denied/expired/consumed/blocked/approved/auto-approved;
deny refused for approved/denied/expired/consumed/blocked; double approve;
unknown id 404; bulk sequence; no audit events for refused transitions).

`bash scripts/verify.sh` (pre-commit green):

```
1835 passed, 7 warnings in 371.15s (0:06:11)
```


### Reviewer notes

**Round 1 (2026-11-08): approved and merged.**

* **Sidebar:** dispatch goes by type first, so a pending integration call
  can no longer reach `/api/approve`.
* **Backend:** approve and deny use one conditional UPDATE, with 404/409 and
  audit only on a real transition.
* **No other callers:** nothing else posts to these endpoints apart from
  `handleAction`.
* **Tests:** 30 X1 + F2 tests pass locally.

Nit, for the UI pass: `contextApproveLabel` reads `_bdUnknownConfirmed` for
integration calls too. The id spaces are shared, so an armed SSH request
with the same number would relabel an integration button. It is cosmetic
only, because the integration handler has no two-press rule.

FYI for the security reviewer: before X1, `/api/approve/{id}` moved a row of
any status to `approved`. It is now pending-only.

---

## Chunk F18 — Request context in the sidebar · slug `request-context` · dashboard

### Goal

Make the sidebar answer "should I approve this?" with what Eshu already
knows, instead of repeating the card.

### Design

One endpoint, `GET /api/requests/{id}/context` (session auth), computed on
demand when the panel opens (never in the 3 s poll). Every block has a hard
cap and is computed from indexed columns. Sections, in this order:

1. **Why it is waiting for you.** The first rule on the approval path that did
   not let it through, reusing the existing checks:
   * metacharacter gate
   * no allowlist match
   * Zero-Trust
   * read lane off, shadow, or ineligible with F4's reason
   * no plan or override match
   * encoded payload

   One line, e.g. "Waiting because: contains `&&` (metacharacter rule)".
2. **This session.** The goal and agent, then the session's last 10 commands
   with status and F5 exit badge, plus any active plan or override.
3. **Seen before.** The exact command on any host in the last 90 days:
   count, last time, the operator's decisions (approved/denied counts), last
   exit code. It matches on exact bytes via sha256, so add an indexed
   `command_sha` column with a backfill (the F7 pattern).
4. **Like this.** Requests with the same F1 *shape*: the sequence of
   `(verb, kind)` over steps, with arguments dropped. Show the counts
   approved and denied, and up to 3 examples. Store a `shape_sha` column as
   well, filled at creation from the breakdown.
5. **Host.** Hostname, inner target (F7), Zero-Trust, read-lane mode, the
   gateway's last-seen time, and the count of failed commands in 24 h (F5).

### Changes

* **DB:**
  * `requests.command_sha` and `requests.shape_sha`, nullable and indexed;
    filled at creation.
  * A batched background backfill with a meta flag, as in F7's
    `inner_targets`.
* **API:** the context endpoint. Responses are capped: 10 session rows,
  3 examples, commands cut to 300 chars. Agent text is returned unescaped
  for the UI to escape.
* **UI:**
  * The sidebar renders the sections, each collapsible, with its state
    remembered in `localStorage` (wrapped in try/catch).
  * A "Seen before" or "Like this" row opens History filtered to it.
  * Every string goes through `escapeHtml`.

### Tests to add

* Each section from synthetic rows: the session list order and cap, exact
  matches across hosts, decision counts, a shape match ignoring arguments,
  and the "why waiting" reason for each rule.
* Backfill: batched, idempotent, and skipped on the second start.
* Performance: on 50k synthetic rows the endpoint answers in < 150 ms (local
  test, generous bound).
* Node harness: escaping of agent text in every section.
* Session auth required.

### Acceptance criteria

- [ ] Opening the sidebar costs one request; the poll is unchanged.
- [ ] Synthetic data only; screenshots use the sandbox.

### Implementer notes

**What I did**

* **DB** (`db/requests.py`): nullable `command_sha` and `shape_sha` columns,
  both indexed. They are filled inside `create_request` itself, so every call
  site (request, log, fleet run, window) gets them with no extra plumbing.
* **Fingerprints** (`core/fingerprint.py`): `command_sha` is the sha256 of the
  exact command bytes; `shape_sha` hashes the sequence of `verb:kind` over
  F1's steps, so arguments are dropped.
* **Backfill** (`core/request_fingerprints.py`): the F7 pattern — batches of
  500 **distinct commands** where `command_sha IS NULL`, one `meta` key
  (`request_fingerprint_backfill_done`), daemon thread from `on_startup`,
  idempotent and skipped on a second start.
* **Context** (`core/request_context.py`): `build_context(request_id)` returns
  the five sections, all capped (10 session rows, 3 examples, 300-char
  commands) and computed on demand:
  1. `waiting` — the first rule that applies, in approval-path order:
     encoded payload → metacharacter (naming the token, e.g. `&&`) →
     Zero-Trust → read lane (off / shadow / ineligible with F4's reason) →
     no allowlist match → no plan or session override matched. (The chunk
     lists the rules; this is the precedence used, so a read that the lane
     would cover reports the lane, not "no allowlist").
  2. `session` — goal/agent, the last 10 commands newest-first with status and
     F5 exit code, plus the active plan and session override.
  3. `seen` — exact `command_sha` across hosts in 90 days: count, last time,
     approved/denied counts, last exit code (includes the current row: it is
     the full record).
  4. `like` — same `shape_sha` in 90 days, excluding the current row: counts
     and up to 3 examples.
  5. `host` — hostname, inner target, Zero-Trust, read-lane mode, last seen,
     and failed commands in 24 h.
* **API**: `GET /api/requests/{id}/context` (session auth, 404 unknown).
* **UI** (`app.js`): `renderRequestContext` renders the five `<details>`
  sections; each open/closed state is stored in `localStorage` inside
  try/catch. "Seen before" has a **show in history** link (and like-this rows
  are commands) that sets the History search filter. Fetched only when the
  panel opens — the 3 s poll is unchanged. Every agent string goes through
  `escapeHtml`; the node harness proves it for every section.
* **Tests** — `tests/test_f18_request_context.py` (15 + 3 skipped): session
  order and cap, exact matches across hosts, decision counts, shape match
  ignoring arguments, host section, every "why waiting" rule, the backfill
  (batched, idempotent, flagged), new rows carrying fingerprints, session
  auth, 404, the 50k-row performance bound (< 150 ms) and the escaping
  harness.

**Surprises and decisions**

* **The base did not have X1 or F4.** `feature-roadmap` was at `5b79914`
  (neither merged) when this branch was cut, as instructed. F18 does not
  depend on X1 — it appends a container and the section renderers to
  `openContextPanel` and leaves the action buttons alone. Without F4 the
  read-lane reasons and the host's mode are inert: the three read-lane
  "why waiting" tests skip until `core.read_lane` exists (`needs_f4`), and the
  host section reports `read lane off`. Both switch on automatically when F4
  merges.
* `command_sha`/`shape_sha` are computed in `create_request` rather than at
  each call site, so a future path cannot forget them; the cost is one cached
  `breakdown()` per request, which the request path already computes for F7.
* The "like this" section excludes the current row; "seen before" includes it
  (the row is part of its own history).

**Screenshot** (synthetic `web01` / `10.0.0.1`, session `hermes`, all five
sections open):

![Sidebar request context](../screenshots/f18/sidebar-context.png)

`bash scripts/verify.sh` (pre-commit green):

```
1832 passed, 3 skipped, 7 warnings in 373.92s (0:06:13)
```

(1817 before this chunk + 15 F18 tests; the 3 skips are the F4-dependent
read-lane reasons above.)


### Round 2 — fixes for reviewer round 1 (C1–C4)

* **C4 — merged `feature-roadmap` (X1) first.** The only conflict was
  `openContextPanel` in `app.js`: kept X1's `contextActionsHtml` /
  `renderContextActions` / `armSidebarConfirm` / `contextApproveLabel` code and
  added F18's container and section renderers (one shared `_ctxReq`
  declaration). The X1 harness tests (`tests/test_x1_ctx_actions.py`, 18) run
  green on the merge. The three F4-dependent read-lane reasons still skip.
* **C1 — operator decisions are not automatic ones.** `seen` (and `like`)
  now return `operator_approved` (`approved`/`consumed` with no
  `session-plan:`/`read-lane` reason), `auto` (`auto-approved`,
  `window-approved`, and plan/read-lane approvals) and `denied`, computed in
  SQL. The UI reads "approved by you N · auto N · denied N". Test:
  `test_f18_seen_splits_operator_from_auto` with one row of each kind.
* **C2 — "Like this" refuses generic shapes.** A one-step shape whose verb is
  `bash`/`sh`/`python*` returns `generic: true` with no counts and the UI says
  "Too generic to compare."; everything with ≥ 2 steps (or a non-interpreter
  verb) keeps its counts. The counts and the 3 examples are now one `COUNT`/
  `SUM` plus one `LIMIT 3` query instead of fetching every row. Tests:
  `test_f18_generic_shape_has_no_counts` and
  `test_f18_like_this_counts_in_sql` (exact numbers from the aggregates).
* **C3 — "why waiting" only for pending rows, worded as a guess.** A
  non-pending row returns `waiting: null` and the UI hides the section; for a
  pending row it says **"Likely waiting because: …"**. Test:
  `test_f18_non_pending_has_no_waiting_reason`, plus a harness check for the
  label and the empty body.

**Round-2 tests** — 4 new in `tests/test_f18_request_context.py` (one per C
item) and one harness case; the F18 file is 23 tests, 3 of them skipped until F4 merges.

**Screenshot** — refreshed with the new wording and split counts:
![Sidebar request context](../screenshots/f18/sidebar-context.png)

`bash scripts/verify.sh` (pre-commit green):

```
1855 passed, 3 skipped, 7 warnings in 378.14s (0:06:18)
```


**Round 1 (2026-11-08): changes requested (small).** The structure is good.
It computes on demand, the caps are in place, fingerprints are filled in
`create_request`, the backfill follows the F7 pattern, every agent string is
escaped (harness), and the endpoint requires session auth. 15 tests pass
locally. This panel helps the operator decide, so it must not overstate the
case for approving:

* **C1 Split "you approved" from "it was auto-approved".** `_approved`
  counts `auto-approved` and `window-approved` (and, once F3/F4 merge, plan
  and read-lane rows) as approvals. "Seen before: approved 50" then reads as
  50 operator decisions when the allowlist made them. Return three counts,
  `operator_approved` (`approved`/`consumed` with no `session-plan:` or
  `read-lane` reason), `auto` (everything automatic) and `denied`, and label
  them that way in the UI. Test with mixed rows.
* **C2 "Like this" must not over-match.** A one-step shape such as
  `cat:read` or `bash:unknown` matches thousands of unrelated commands, so
  its counts are noise that looks like evidence. Show the section only when
  the shape has ≥ 2 steps, or when its verb is not `bash`/`sh`/`python*`;
  otherwise show "too generic to compare". Also compute the counts in SQL
  (`COUNT`/`SUM(...)` plus `LIMIT 3` for examples) instead of fetching every
  matching row. Tests: a generic shape gets no counts, and the SQL path
  returns the same numbers.
* **C3 "Why waiting" only for pending rows, worded as a best guess.** It
  re-derives the reason, so it can differ from the real path (host
  override, freeze, order changes). Return it only for `pending` rows, and
  label it "Likely waiting because: …". Test that a non-pending row has no
  reason.
* **C4 Merge `feature-roadmap` into this branch.** It now contains X1, and
  both touch `openContextPanel`. Keep X1's `contextActionsHtml`/two-press
  code and add the F18 container. Re-run the X1 harness tests. The three F4
  skips stay until F4 merges, which is fine.

**Round 2 (2026-11-08): approved and merged.** C1–C4 are done. 38 F18 and X1
tests pass locally, and the X1 sidebar actions survived the merge.

* **Fixed during review (C1 gap):** rows approved under a host or session
  override (`override`, `session-override:%`) still counted as "approved by
  you". They now count as `auto`. Added
  `test_f18_override_approvals_are_automatic`, which fails before the fix
  and passes after.
* **Accepted as is:** C2 marks only one-step *interpreter* shapes as generic
  (as the note was worded). One-step shapes such as `cat:read` keep their
  counts, which is useful enough.
* **For the UI pass:** consider showing "similar" only for ≥ 2-step shapes.

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

**What I did**

* **`core/approval_buckets.py`**: one mapping, `bucket_for(status, reason)`,
  and the order the UI shows: allowlist · human · plan · read lane · session
  override · host override · window · fleet run. Status wins where the status
  alone is the answer (`auto-approved`, `window-approved`, `fleet-run`), so a
  gateway's free-text reason on an allowlist row cannot move it between
  buckets. Everything else on an approved row goes by reason prefix;
  `session-plan:` and `session-override:` are prefixes, `override` and
  `read-lane` are exact. **The read-lane reasons are matched by string** —
  this branch has no F4 code to import. Refusals return `None`; a read-lane
  *shadow* row is pending, and if a human approves it the bucket is `human`
  (the lane did not clear it).
* **`requests.decided_at`** (`db/requests.py`): a nullable column, set by the
  first decision (`update_request_status_if_pending`, the approve/deny path),
  by `update_request_status` (`COALESCE`, so a later consume never rewrites
  it), and by the expiry sweep (`expired`/`abandoned`). Nullable on purpose:
  pre-F6 rows stay unknown instead of being given an invented wait.
* **`get_session_summary`** grows `by_approval` (all eight buckets, zeros
  included), `waited_seconds` (`SUM(decided_at - created_at)`) and `decided`.
  The MCP `session_summary` tool returns the dict as JSON, so it carries the
  same breakdown without a second code path.
* **`GET /api/sessions/{sid}/detail`** adds `summary`: the above plus `goal`,
  `started_at`, `duration_seconds`, `failed` (F5), and `writes` /
  `writes_by_host`, counted from F1's breakdown per row — a session can span
  hosts, and the card names the host. Encoded commands count zero writes
  (their steps are unknown; they never get this far).
* **`GET /api/statistics`** adds `approval_breakdown`: `buckets`, `series`
  (one row per day in the selected window, all buckets), `totals`,
  `approved`, `override_share` (session + host override ÷ every approval,
  per cent). Grouped in SQL by `(day, status, reason)` and bucketed in Python,
  so the row count stays small.
* **UI**: the session modal gets a wrap-up card under the meta line — how long
  it ran, command/failed counts, the wait for a human, the eight approval
  chips (zero counts greyed, so the absence of overrides is visible) and the
  writes per host. Statistics gets a "How approved" widget: a stacked bar per
  day, oldest first, with the override share as the chip headline and a legend
  that keeps empty buckets visible. Bucket colours are tokens in `:root`
  (`--appr-*`), not literals in rules.
* **Tests**: `tests/test_f6_session_wrapup.py` (53) — the mapping table, the
  generated reason-literal scan, the summary (counts, wait, MCP JSON),
  `decided_at` on approve/deny/expire/consume/second-decision, the detail
  summary (goal/duration/writes per host/failures), the statistics block
  (series, totals, share, gateway filter, empty window), and four node-harness
  checks for the card and the widget (escaping, zero buckets, duration
  formatting).

**Decisions and surprises**

* `approval_breakdown` follows the page's day selector (7–365, default 14)
  rather than being pinned to 14 — the chart and the KPI cards then describe
  the same window.
* A status's own meaning beats the reason in three places: `auto-approved`
  (allowlist reason text is free-form from the gateway), `window-approved`
  (no reason at all today) and `fleet-run` (the operator's own reason is free
  text). Everything else is a reason prefix on an approved row.
* `integration-approved`/`integration-denied` appear in the requests table
  historically but are not SSH approvals; they map to `None` and are counted
  as MCP calls, which is what the modal already did.
* The wait is `decided_at - created_at` per row, summed. Rows created before
  the column existed contribute nothing, and the card says "wait not
  recorded" when no row has one, instead of showing a confident zero.

`bash scripts/verify.sh`: `1909 passed, 3 skipped, 7 warnings in 383.81s (0:06:23)`. The three skips are F18's read-lane tests: F4 is not on this branch.

### Round 2: fixes for reviewer round 1

* **R1 — a row created already decided starts decided.** `create_request` now
  stamps `decided_at = created_at` for every non-`pending` status (allowlist,
  host/session override, plan, read lane, window, fleet), and `pending` keeps
  NULL. The expiry sweep's `COALESCE` therefore never rewrites a creation
  decision, so an unclaimed override swept to `abandoned` adds **0** to
  `waited_seconds` and 1 to `decided`. Tests:
  `test_f6_a_decided_row_starts_at_its_creation` (six status/reason pairs),
  `test_f6_a_pending_row_has_no_decision`,
  `test_f6_an_unclaimed_override_waits_zero` (override,
  `session-override:` and `session-plan:` swept to abandoned) and
  `test_f6_an_auto_approved_log_row_waits_zero` (an `/api/log`
  `auto-approved` row through the gateway endpoint).
* **R2 — writes mean what changed.** `session_detail_endpoint` now skips a row
  whose `bucket_for(status, reason)` is `None`, so denied, blocked and expired
  commands are not counted as writes; the card uses the same mapping as the
  approval chips. `test_f6_writes_count_only_decided_rows`: one approved, one
  denied, one blocked and one expired write give `writes == 1` on the approved
  row's host.
* **R3 — the generated reason test can fail now.**
  `core/approval_buckets.KNOWN_REASON_PREFIXES` maps each prefix to its bucket
  or to `None` ("not an approval": `encoded-payload`, `exhausted or
  disabled"), and `bucket_for` goes through `known_reason()` instead of
  hard-coded `startswith` checks. The scan is now a function over a list
  (`_unmapped_prefixes`), the test asserts the scanned code prefixes are all
  keys, and `test_f6_the_reason_check_can_fail` proves a synthetic
  `brand-new-prefix` fails while known ones pass.
  `test_f6_the_table_agrees_with_bucket_for` keeps the table and
  `bucket_for` in step.
* **S1 — the goal is on the card.** The facts line now starts with
  `goal "…"` (escaped), so a named session no longer hides what it was for;
  the node harness carries a goal and asserts it renders.
* **S2 — known mismatch, not changed.** The approval series groups by SQLite
  `DATE(created_at, 'unixepoch')` (UTC) while `filled` uses
  `time.localtime`, exactly like the existing daily chart. On a non-UTC
  dashboard the newest day's approvals can land in no bar; fixing it means
  changing both charts' day boundary, so it is written down here rather than
  half-fixed. **S3** (per-open `cmd_breakdown`) is likewise left as a known
  cost; the modal is opened by a human, not in a loop.

`bash scripts/verify.sh`: `1921 passed, 3 skipped, 7 warnings in 383.60s (0:06:23)`.

### Round 3: merge with F8, F9 and F14

`git merge origin/feature-roadmap` brings the approved F8 (test traffic), F9
(freeze + HA Supervisor reads) and F14 (inventory) onto this branch. Four
files conflicted; each was resolved by keeping both features, whole:

* **`main.session_detail_endpoint`** keeps **one** summary dict: F6's
  `by_approval`, `waited_seconds`, `decided`, `failed`, `duration_seconds`,
  `writes` and `writes_by_host` plus F8's `excluded` and `all_excluded`. The
  write count still uses the bucket mapping (R2), and the exclusion counts
  still cover every SSH row of the session (F8 S3).
* **`index.html`** keeps both `sm-summary` (F6's wrap-up card) and
  `sm-test-hint` (F8's suggestion), in that order, under `sm-meta`.
* **`app.js`** keeps the F6 block (`sessionSummaryHtml`, `renderSessionSummary`,
  the approval chips, the goal line) and the F8 block (`looksLikeTestGoal`,
  `excludedMarkHtml`, `excludeRequests`, `renderSessionTestHint`) whole; the
  modal calls `renderSessionSummary(...)` then `renderSessionTestHint(...)`.
* **`style.css`** keeps the F6, F14 and F8 blocks whole and once each: the F6
  `--appr-*`/`.appr-*`/`.session-summary*` rules, the F14 `.inv-*` rules and
  the F8 `.excluded-*`/`.sm-test-hint` rules. `ui_check.py` stays green and
  `node --check` passes.

**F8's merge note is implemented**, not just recorded: the F6
`approval_breakdown` query now adds `COALESCE(r.excluded, 0) = 0` (the same
filter as the other statistics queries), so an excluded approval is not in the
"how approved" series. F8's generated statistics-filter scan walks
`get_statistics` and now covers that query too, and
`test_f6_approval_breakdown_skips_excluded_rows` checks the behaviour (one
approved row plus one excluded approved row reports `human == 1`). The F6 and
F8 test files both pass on the merged tree.

`bash scripts/verify.sh`: `2010 passed, 3 skipped, 7 warnings in 410.61s (0:06:50)`.

### Reviewer notes

**Round 1 (2026-11-08): changes requested.** Reviewed at `6b35d99`;
`bash scripts/verify.sh`: `1909 passed, 3 skipped, 7 warnings in 331.10s (0:05:31)`.

**What holds.** One mapping (`core/approval_buckets.py`) feeds the session
card, the Statistics series and the MCP `session_summary`. "Status wins over
a free-text reason" is the right call. `decided_at` is nullable, so old rows
report no wait instead of a made-up one, and the first decision wins. The UI
escapes every server string: host names, labels and dates. Bucket keys come
from the server's fixed list. Colours are `:root` tokens. No approval path
or order changed: this chunk only reads. The fixtures use `192.0.2.x`, and
there is no live data.

*Must fix:*

* **R1 Rows that no human saw get counted as waiting for a human.** A row
  created already approved (host override, session override, plan, and later
  the read lane) has `decided_at = NULL`. If the gateway never claims it, the
  expiry sweep marks it `abandoned` and stamps `decided_at = now`
  (`COALESCE`). The agent then appears to have "waited" the whole TTL plus
  the sweep delay. In a scratch test, one host-override row with a 90 s TTL,
  swept 600 s later, added 600 s to `waited_seconds` and 1 to `decided`.
  **Fix:** stamp `decided_at = created_at` in `create_request` for any status
  other than `pending` (the decision *was* the creation), and keep the
  `COALESCE` in the sweep. **Test:** create an `approved` row with
  `reason="override"`, sweep it to `abandoned`, and check that it adds 0 to
  `waited_seconds`. Do the same for `session-override:`, `session-plan:` and
  `/api/log` `auto-approved` rows.
* **R2 Denied, blocked and expired commands are counted as writes.** The
  `writes_by_host` loop in `session_detail_endpoint` counts F1 write steps
  in every SSH row of the session. The card says "writes", meaning what
  changed, but a denied `systemctl restart` and a blocked `rm` both count. In
  a scratch test, a session with one denied and one blocked write showed
  `writes: 2`. **Fix:** count only rows whose bucket is not `None` (they ran,
  or were cleared to run). Use `bucket_for(row['status'], row['reason'])`,
  so the card uses the same mapping. **Test:** a session with one approved,
  one denied, one blocked and one expired write reports `writes == 1` on the
  approved row's host.
* **R3 The "new reason prefix" test can never fail.** The spec asks for "a
  test that fails when a new reason prefix is added without a bucket".
  `test_f6_code_reasons_have_a_bucket` asserts
  `bucket_for('approved', prefix) is not None`, but `bucket_for` returns
  `human` for *any* unknown reason on an approved row, so every new prefix
  passes. **Fix:** add a `KNOWN_REASON_PREFIXES` table to
  `approval_buckets.py` that maps each prefix to its bucket, or to "not an
  approval" (`encoded-payload`). Then assert that every scanned prefix is a
  key in that table, and that `bucket_for` agrees with the table. **Test:**
  the scan itself. Also add a negative case: a synthetic prefix that is not
  in the table makes the check fail. Run the check function on a list, not
  on the tree.

*Should fix:*

* **S1 The goal is not on the card once the session has a name.** The spec
  lists the goal first. The card relies on the modal title, which shows
  `meta.name || goal`, so a named session hides its goal. Show the goal in
  the facts line when the session has a name. **Test:** the node harness
  renders a named session, and the goal appears, escaped.
* **S2 The day buckets mix UTC and local time.** The new series keys
  `approval_by_day` by SQLite `DATE(created_at, 'unixepoch')` (UTC), but
  `filled` uses `time.localtime`. The existing daily chart has the same
  mismatch, so this is not new. On a non-UTC dashboard, the newest day's
  approvals can land in no bar. Either use the same clock on both sides, or
  write the mismatch down as known. **Test:** with `TZ` set to a non-zero
  offset, today's approvals appear in the last series entry.
* **S3 Writes are counted on every detail open.** `cmd_breakdown` runs once
  per SSH row (up to 500) on every modal open. That is fine today. If it is
  ever slow, cache the count per row, or reuse F18's stored breakdown.

F6 lists F4 as a dependency. The read-lane bucket is matched by string and
tested, which is acceptable. When F4 merges, re-run
`test_f6_code_reasons_have_a_bucket` (after R3), so the `read-lane` literal
is picked up.


**Round 2 (2026-11-09, main feature reviewer): approved and merged.** The overnight reviewer approved R1–R3. The merge with F8 keeps one `summary` dict, and `approval_breakdown` skips excluded rows (covered by F8's generated scan). No duplicate functions in `app.js`; CSS braces balance.

---

## Chunk F7 — Inner target attribution · slug `inner-target` · dashboard

### Why

About 100 recorded commands ran inside a container or VM (`pct exec`,
`docker exec`) but are recorded against the host that ran the wrapper. History,
statistics and overrides should know the real target, and an override on a
Proxmox host silently covers every container it can reach.

### Changes

* **Parser:** F1 already reports `inner_targets` for `pct exec <id>` (`pct:<id>`)
  and `docker exec` / `docker compose exec` (`docker:<name>`). Add
  `qm guest exec <vmid> [--] cmd…` as a wrapper in `core/cmd_parse.py` (context
  `qm <vmid>`, inner target `qm:<vmid>`, the inner command classified as usual).
  Add a fixture case and keep F1's tests green.
* **Column:** `requests.inner_target TEXT DEFAULT ''` (migration in the same
  style as `db/requests.py`'s existing `ALTER TABLE` loop), indexed together with `target_ip`.
  Value: the first inner target, `multi` when there are several, `''` for none.
  Filled in `create_request` callers for both `/api/request` and `/api/log`
  (compute `breakdown(cmd)['inner_targets']`; it is cached and fast).
* **Backfill:** a one-off background job at startup fills `inner_target` for
  existing rows where it is `NULL` (rows added before the migration), in batches
  of 500 distinct commands, idempotent, with a `meta` key recording completion.
  It must never block startup or the request path. Without it the history filter
  and the warning below would be empty for weeks.
* **API:**
  * `/api/requests` rows and `/api/requests/{id}/breakdown` callers already
    have the data; add `inner_target` to request rows.
  * `GET /api/gateways/{ip}/inner-targets?days=30` (session auth) →
    `{"pct": ["103", "106"], "qm": [], "docker": ["sonarr"], "count": 3}`.
  * `/api/requests?inner_target=pct:103` filter (exact match), alongside the existing `search`/`session` filters.
* **UI:** an inner-target chip on history rows and pending cards (`pct 103`,
  `docker sonarr`), escaped, clicking it filters history; Statistics gets a
  "busiest inner targets" list (top 10, 14 days).
* **Override warning:** when the host-override dialog opens for a gateway
  whose `inner-targets` count (30 days) for `pct`/`qm` is > 0, show:
  "This host reaches N containers/VMs via pct/qm. An override here also covers
  commands run inside them." Warning only; no behaviour change.
* **Optional (skip if it grows the chunk):** label `pct:<id>` with the guest name
  from the Proxmox integration (read-only, cached 10 min; nothing shown if not configured).

### Tests

* Parser: `qm guest exec 201 -- uptime` → context `qm 201`, inner `qm:201`, step `read`.
* Fill-in on `/api/request` and `/api/log` for pct, docker, qm, `multi` and none.
* Backfill: fills old rows, skips filled ones, sets the completion key, safe to run twice.
* `inner-targets` endpoint counts and the `days` window; `inner_target` filter.
* The override-dialog warning appears only when pct/qm targets exist (node harness or API test).
* Chip text is escaped (node harness, same pattern as F2).

### Compat impact

Additive column and fields; no gateway change.

### Implementer notes

**What I did**

* **Parser** (`core/cmd_parse.py`): `qm guest exec <vmid> [--] cmd…` is now a
  wrapper like `pct exec` — context `qm 201`, inner target `qm:201`, the inner
  command classified as usual. New fixture `wrappers.txt::qm-guest-exec`; F1's
  table still passes.
* **Column** (`db/requests.py`): `inner_target`, added by the same migration
  loop, indexed `(target_ip, inner_target)`. **Deliberately nullable** rather
  than `TEXT DEFAULT ''`: NULL marks a row that predates the column so the
  backfill can find it, while `''` means "this command has no inner target".
  The API and UI normalise NULL to `''`.
* **Fill-in**: `_inner_target_for(cmd)` (a thin wrapper over
  `core.inner_targets.inner_target_for`, which uses F1's cached breakdown) is
  passed by all four `/api/request` creation paths (encoded-payload block,
  session override, host override, pending) and by `/api/log`. Value is the
  first target, `multi` for several, `''` for none.
* **Backfill** (`core/inner_targets.py`): `backfill_inner_targets()` walks
  NULL rows in batches of 500 **distinct commands**, recomputes each command
  once, and sets the `meta` key `inner_target_backfill_done` when done. It is
  idempotent (only NULL rows are touched; the key short-circuits a second
  run) and runs in a daemon thread from `on_startup`, so it never delays
  startup or the request path.
* **API**: `inner_target` is on every request row (`SELECT r.*`), a new
  `GET /api/gateways/{ip}/inner-targets?days=30` returns
  `{pct, qm, docker, count}` (distinct targets, `multi` excluded), and
  `GET /api/requests?inner_target=pct:103` filters exactly, composing with
  `search`/`session`. `/api/statistics` gains `inner_targets` (top 10 for the
  window, `multi` and empty excluded).
* **UI**: an inner-target chip on pending cards (hero) and on history rows;
  clicking it switches to History with a removable `inside pct 103` filter
  chip in the header. Statistics gets a "Busiest inner targets" widget (top
  10, same 14-day window) whose chips filter too. The host-override dialog
  fetches the endpoint and shows "This host reaches N containers/VMs via
  pct/qm. An override here also covers commands run inside them." — warning
  only, no behaviour change, and nothing appears when the host has no pct/qm
  targets.
* **Tests**: `tests/test_f7_inner_target.py` (17 tests) — parser, fill-in on
  `/api/request` and `/api/log` (pct, docker, qm, `multi`, none), the backfill
  (fills, skips filled, sets the key, safe twice, batches, thread), the
  endpoint (counts, `days` window, unknown gateway, session auth), the filter,
  the statistics list, and the two node-harness checks (chip escaped;
  override warning only with pct/qm). The node harnesses now share
  `tests/fixtures/appjs_loader.js`; the F2 harness was moved onto it and its
  12 tests still pass.

**Surprises and decisions**

* The nullable column is the one deviation from the chunk's literal
  `TEXT DEFAULT ''`: with a default, pre-migration rows would read `''` and
  the backfill would have nothing to select. `''` vs NULL keeps "no inner
  target" and "not computed yet" apart, which is what the one-off job needs.
* `multi` rows are recorded (the card shows a `multi` chip) but excluded from
  the gateway endpoint and the statistics list — a command that reached
  several guests cannot be attributed to one.
* The override warning counts **pct + qm only** (as the chunk specifies);
  docker targets do not trigger it.
* Out of scope, found while testing: `/api/statistics?...gateway_ips=…` 500s
  with `no such column: r.target_ip` — a pre-existing unaliased query in the
  daily aggregation (the UI does not pass that parameter today). My new
  inner-targets query uses the `r` alias so it does not add a second one; I
  left the pre-existing bug alone per § 2.1.3.
* Only `/api/request` and `/api/log` fill the column at write time, as the
  chunk asks. Rows created by Fleet Run and window logging keep `''` rather
  than a computed target (the backfill only fills NULL). Say so if you want
  those two paths added.
* The optional guest-name labelling (`pct:<id>` → guest name from the Proxmox
  integration) was skipped — the chunk marks it optional and it would have
  grown the chunk.

**Screenshots** (synthetic gateway `web01` / `10.0.0.1`)

* The inner-target chip on a pending card and on a history row:
  ![pending card chip](../screenshots/f7/pending-card-chip.png)
  ![history row chip](../screenshots/f7/history-row-chip.png)
* The host-override warning:
  ![override warning](../screenshots/f7/override-warning.png)
* Statistics "Busiest inner targets":
  ![statistics inner targets](../screenshots/f7/stats-inner-targets.png)

`bash scripts/verify.sh` (pre-commit green):

```
1735 passed, 7 warnings in 345.69s (0:05:45)
```

(1715 before this chunk + 17 new F7 tests + 3 parser-table tests from the new
fixture case.)


### Reviewer notes

**Round 1 — 2026-11-08 — approved and merged.**

Clean chunk. All new SQL is parameterised; the nullable column is the right call
(NULL = not yet backfilled, `''` = no inner target), and only `create_request`
inserts into `requests`, so every new row gets a value. The backfill always makes
progress (each update clears that command's NULL rows), commits in small
transactions, and is retried on the next start if it fails part-way.

Measured on a **copy** of the live database (taken with `.backup` into a temp
dir on the dashboard host, deleted afterwards; the live file was not touched):
migration 0.01 s; backfill 7.1 s in the background for 1,365 distinct commands,
attributing 123 rows (87 docker, 26 pct, 1 qm, 9 multi); second run skipped;
no NULL rows left. Override-warning screenshot checked. Gate re-run by the
reviewer: pre-commit OK, `1735 passed`.

Nit for a later chunk (not blocking): cap `inner_target_for()`'s value at 64
characters, since a docker container name is agent-controlled. The out-of-scope
`/api/statistics?gateway_ips=` 500 you found is logged in `docs/KNOWN_ISSUES.md`.

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

**What I did**

* **`requests.excluded`** (`db/requests.py`): `INTEGER DEFAULT 0`, added by the
  usual migration loop. Every existing row reads 0 — **no migration or rule
  excludes anything by itself**, which is the chunk's one-off requirement.
  `mark_requests_excluded(ids, session_id)` is the single writer: idempotent
  (only rows currently at 0 are touched) and it returns how many it changed.
* **`POST /api/requests/exclude`** `{ids[] | session_id}` (session auth,
  audit `requests_excluded`, 400 when neither is given, ids capped at 1000).
  Unknown ids are not an error; they simply count 0. Rows stay in History and
  in `/api/requests` — only the aggregate views skip them.
* **Statistics** (`/api/statistics` and `/api/statistics/export`): every
  request-based aggregate adds `COALESCE(r.excluded, 0) = 0` — per-gateway,
  daily, top commands, inner targets, hourly, weekday×hour, per-gateway
  sparkline, automation trend and top denied (9 + 2 queries).
* **Learning gaps** (`core/learning.py`, both the approval and the denial
  query) and **anomaly profiles** (`core/cmd_profiles.compute_profiles`) skip
  excluded rows, so a smoke test can no longer trigger an allowlist
  suggestion or make a command look "already seen".
* **Inner-target warning** (`/api/gateways/{ip}/inner-targets`) skips them
  too: a test `pct exec` should not widen the host-override warning.
* **UI**: a row marked excluded gets an `excluded` chip and is dimmed;
  unexcluded rows carry an `exclude` link next to `breakdown`. The session
  modal shows a dashed hint when the goal matches `/test|smoke/i` — the
  suggestion, never an automatic action — with an "Exclude from stats"
  button; the same action is available as a plain **Exclude** button in the
  modal header for sessions whose goal does not say it. Both go through the
  same endpoint and confirm first.
* **Tests**: `tests/test_f8_test_traffic.py` (17) — the endpoint (ids,
  session, idempotence, 400, auth, audit, rows still listed, unknown ids),
  statistics (daily, per-gateway, top commands, top denied, export,
  inner-target warning), learning (approvals and denials), profiles, that
  nothing is excluded automatically, and a node harness for the suggestion
  predicate and the excluded marker.

**Decisions and surprises**

* The chunk says statistics, learning and anomaly profiles; I also applied the
  flag to the inner-target warning (it feeds an operator decision) and to the
  CSV export (it is the same statistic leaving the app). Both are additive.
* The policy **preview** (`/api/policies/preview`) still replays excluded
  commands. It answers "what would this draft policy do to this traffic", and
  the operator may deliberately want test traffic in that what-if; leaving it
  alone also keeps this chunk from changing a decision tool. Say so if you
  want it filtered.
* Exclusion is one-way: there is no "include again" in the API or the UI. The
  flag is a single boolean, so a future chunk can add it without a migration.
* The session hint matches any `test`/`smoke` in the goal, as specified. It is
  a hint with a confirm button, so a false positive costs one ignored line.

`bash scripts/verify.sh`: `1873 passed, 3 skipped, 7 warnings in 383.49s (0:06:23)`. The three skips are F18's read-lane tests (F4 is not on this branch).

### Round 2: fixes for reviewer round 1

* **R1 — the override warning sees every row again.** Removed
  `COALESCE(excluded, 0) = 0` from `GET /api/gateways/{ip}/inner-targets`: it
  feeds F7's host-override warning ("an override here also covers these
  containers"), which is a safety prompt, not a statistic. The Statistics
  `inner_targets` table keeps the filter. The old test is replaced by
  `test_f8_inner_target_warning_keeps_excluded_rows`, which shows the excluded
  `pct exec` guest is still in the warning and gone from `/api/statistics`.
* **S1 — "test" as a word.** `TEST_GOAL_RE` is now `/\b(smoke|tests?)\b/i`,
  so "update to the latest kernel" no longer gets the hint while "smoke test"
  and "run the tests" do; the harness asserts all five cases.
* **S2 — a generated guard for the statistics filter.**
  `test_f8_every_statistics_query_filters_excluded` splits
  `get_statistics`/`export_statistics` into `cursor.execute(` blocks, and every
  block that reads `FROM requests` must mention `excluded` (9 blocks checked).
  A future aggregate cannot forget the filter.
* **S3 — an excluded session says so.** The session detail now carries
  `summary.excluded` and `summary.all_excluded`; the modal hint shows "This
  session is excluded from statistics and learning" instead of the button
  when every row is excluded, and the confirm text says rows the session adds
  later are not excluded. `test_f8_detail_reports_an_excluded_session` covers
  the done state and the new row that turns it off.

**Merge note (F6 ↔ F8), recorded not fixed:** F6's `approval_breakdown`
query exists only on `feat/f6-session-wrapup`; whichever merges second must add
`COALESCE(r.excluded, 0) = 0` to it and add it to the S2 scan's coverage.

`bash scripts/verify.sh`: `1875 passed, 3 skipped, 7 warnings in 384.64s (0:06:24)`.

### Reviewer notes

**Round 1 (2026-11-09): changes requested (one small item).** Reviewed at
`d544096`; `bash scripts/verify.sh`: `1873 passed, 3 skipped, 7 warnings in 331.04s (0:05:31)`.

**What holds.** `excluded` defaults to 0, and the migration flags nothing. A
test proves nothing is excluded automatically. The endpoint needs a session
(the CSRF middleware covers the POST), writes an audit event, is idempotent,
and caps `ids` at 1000. Excluded rows stay in History. Statistics, the CSV
export, both learning-gap queries and the anomaly profiles skip them.
Leaving rows out of the profiles makes a command look *newer*, not older,
so the anomaly signal can only get louder. The UI escapes the session goal,
and the row id goes through `Number()`. No approval path or order changed.
The fixtures are synthetic.

*Must fix:*

* **R1 Keep the inner-target warning on every row.** The chunk asks for
  statistics, learning gaps and anomaly profiles. The branch also filters
  `GET /api/gateways/{ip}/inner-targets`, which feeds F7's host-override
  warning ("this override also covers these containers"). That warning is
  a safety prompt, not a statistic. Excluding a session that ran `pct exec`
  into a guest would make the next host override look narrower than it is.
  §0 rule 4 also says not to widen scope. Revert that one filter. Keep the
  filter on the Statistics "inner targets" table, which is a statistic.
  **Test:** replace `test_f8_inner_target_warning_skips_excluded` with one
  showing that an excluded `pct exec` row *still* appears in the
  inner-targets warning, while it is gone from `/api/statistics`
  `inner_targets`.

*Should fix:*

* **S1 Match "test" as a word.** `/test|smoke/i` also matches "latest",
  "contest" and "attestation", so a goal like "update to the latest kernel"
  gets the hint. The spec says "contains", so this is not wrong, but a
  word match (`/\b(smoke|tests?)\b/i`) gives fewer false positives.
  **Test:** the harness predicate is false for "latest" and true for
  "smoke test" and "run the tests".
* **S2 A generated test for the statistics filter.** Nine queries in
  `get_statistics` got the filter by hand, and the tests cover four of the
  surfaces (daily, per gateway, top commands and denied, export). Hourly,
  the heatmap, the sparkline and the automation trend have no test. A
  scan test would make sure a future aggregate cannot forget the filter:
  every `FROM requests` in `get_statistics`/`export_statistics` must
  mention `excluded`.
* **S3 The session hint after excluding.** Reopening an excluded session
  shows "This looks like test traffic" again, with the button (the call
  then reports 0). Have the session detail say whether every row is
  excluded, and show the done state instead. Rows the session adds later
  are not excluded. That is fine, but say so in the confirm text.

**Merge note (F6 ↔ F8).** F6's new `approval_breakdown` query on
`feat/f6-session-wrapup` is not filtered, because the branches are
independent. Whichever of the two merges second must add
`COALESCE(r.excluded, 0) = 0` to that query and to the S2 scan.


**Round 2 (2026-11-09, main feature reviewer): approved and merged.** R1 is right: the inner-target warning counts excluded rows again, and Statistics still drops them. S1–S3 are fine.

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

**What I did**

* **Freeze covers integrations** (`core/tool_runner.py`): with the freeze flag
  set, a call that is not a read is refused before any pending row, preflight
  or upstream request, with `{"error": {"code": "frozen", "message": …,
  "status_code": 409}}`, an `integration_calls` row (`outcome=refused`,
  `reason_code=frozen`, method/path only) and an audit event
  `mcp_frozen_refused`. Reads keep working; a handler **dry-run** also runs,
  because it changes nothing upstream (the freeze protects state, not the
  validation path). Whether a call is a read comes from
  `core/read_policy.is_read_integration_call` (§2.6a), not from a second local
  rule — the module is the one the task asked for: the integration half only,
  with a note that F4's branch holds the full file (SSH rules) and that the
  `read_only` guard must survive the merge.
* **Approvals** (`POST /api/integration-calls/{id}/approve`): while frozen the
  approval is refused with **409** and an audit event
  `integration_call_refused_frozen`; the call **stays pending**, so unfreezing
  lets the operator approve it (a freeze pauses the queue, it does not destroy
  it). Denying is still allowed — removing work is never a write.
* **`core/ha_supervisor.py`**: the fixed endpoint table
  (`SUPERVISOR_READS`: tool → endpoint, method, shaper), `endpoint_for(tool,
  args)` (the only place an endpoint is built; the add-on slug is validated
  against `^[a-z0-9_]{1,64}$` **before** formatting, so `/`, `..`, `?` and
  uppercase never reach the upstream), per-shape field allowlists (lists
  capped at 200 rows), `ha_addon_info` shaped without `options`,
  `run_supervisor_read` (audit via `ha_ws_exec` with the tool name, then
  `secret_scrub`), and the 401/403 → `ha_admin_required` mapping.
  `SUPERVISOR_ACTIONS` is an empty table for F10, kept separate so
  `SUPERVISOR_READS` stays "reads only".
* **Tools** (`core/ha_seed.py`, `handler: ha_supervisor_read` for the eight
  reads, `handler: ha_whoami` for the WS `auth/current_user`):
  `ha_supervisor_info`, `ha_core_info`, `ha_os_info`, `ha_host_info`,
  `ha_addons`, `ha_addon_info(slug)`, `ha_backups`, `ha_resolution`,
  `ha_whoami`, and `ha_check_config` (REST `POST
  /config/core/check_config`, `read_only: True` — it validates and changes
  nothing, so it runs without approval). Seeding is idempotent by name, so an
  existing HA integration gets the ten new tools after "Seed" without
  duplicates.
* **UI**: the freeze banner, the freeze pill tooltip and the freeze toast now
  say "SSH and integration writes rejected".
* **Docs**: `docs/INTEGRATIONS.md` documents the Supervisor tools, the
  admin-token requirement, `ha_whoami`, the dedicated-`eshu`-admin-user
  recommendation (a long-lived token carries all of its user's rights) and
  that freeze now covers integration writes. CHANGELOG entries for both
  behaviour changes.
* **Tests**: `tests/test_f9_ha_supervisor_read.py` (37) — freeze (write
  refused, nothing queued, read allowed, dry-run allowed, approval refused and
  kept pending, approval after unfreeze, the read policy is consulted),
  each tool's exact endpoint and method, slug validation (7 bad forms never
  reach the upstream), `options` never returned, compact add-ons, non-admin
  mapping, upstream error, `ha_whoami`, `ha_check_config` as a read, scrubbing,
  the allowlist (table == tools; no other module builds a `supervisor/api`
  payload; every supervisor tool is read-only), and idempotent seeding. Two
  pre-existing count assertions moved from 18 to 28 seeded tools, explained in
  the commit.

**Decisions**

* The tool names are the chunk's literal `ha_*` names. MCP prefixes the
  integration name, so on the shared surface an integration named "Home
  Assistant" exposes `home_assistant_ha_supervisor_info`. The alternative is
  to drop the `ha_` prefix in the seed (then it reads
  `home_assistant_supervisor_info`); say which reads better and it is a
  one-line rename per tool.
* `ha_check_config` is a POST but `read_only: True`: `_read_only_refusal` only
  guards generic passthrough tools, so a curated read-only POST runs directly —
  which is the intent ("validates and changes nothing").
* The Supervisor read shapers are allowlists, not denylists: a new upstream
  field is dropped until someone adds it, which is the safe default for a
  surface that can carry secrets.
* An add-on's `options` would be caught by `secret_scrub` for the obvious
  password-shaped keys, but not for arbitrary ones; the shaper dropping
  `options` entirely is the real protection.

**Open questions**

1. **Tool naming** (above): keep the `ha_` prefix in the seed, or drop it so
   the MCP-visible name is `<integration>_supervisor_info`? Nothing depends on
   either choice except the docs and the existing-name test.

`bash scripts/verify.sh`: `1893 passed, 3 skipped, 7 warnings in 394.38s (0:06:34)`. The three skips are F18's read-lane tests (F4 is not on this branch).

### Round 2: fixes for reviewer round 1

* **R1 — only a handler dry-run is exempt from the freeze.** The freeze check
  used `not args.get('dry_run')`, so an agent-supplied `dry_run: true` let a
  curated REST tool, a generic tool or `ws_write` past the freeze (it is just
  another argument for those; only handlers honour it). The exemption is now
  `handler and args.get('dry_run')`, the same condition the gate below uses.
  Tests: `test_f9_a_curated_rest_dry_run_is_still_a_write`,
  `test_f9_a_ws_write_dry_run_is_still_a_write` and
  `test_f9_a_generic_write_dry_run_is_still_a_write` — each is refused with
  `frozen`, queues nothing and makes no upstream request; the handler dry-run
  test stays green.
* **R2 — the generic WS tools cannot reach the Supervisor.** `ws_write` could
  still forward an agent-chosen `supervisor/api` (or `hassio/*`) endpoint, and
  HA has no allowlist of its own. Two guards, no socket before the decision:
  * `core/tool_runner._run_tool` refuses a WS command whose name starts with
    `supervisor/` or `hassio/` **before gating**, so no pending call is
    created either (error `supervisor_command_refused`, 403, an
    `integration_calls` row with `reason_code=supervisor_command_refused` and a
    `ws_supervisor_refused` audit event);
  * `core/ha_ws.execute_ws_call` refuses the same prefixes, which covers the
    approval executor and any direct caller.
  `core/ha_ws.is_supervisor_command` is the one predicate; the curated
  handlers still use `ha_ws_request` with constants, so `ha_supervisor.py`'s
  table remains the only path. Tests: `test_f9_ws_write_refuses_supervisor_before_connecting`,
  `test_f9_ws_write_refuses_hassio_before_connecting`,
  `test_f9_an_approved_supervisor_ws_call_is_refused` (a pending call created
  before the guard is refused on approval, its stored result carries the
  message) and `test_f9_a_normal_ws_command_still_reaches_the_transport`.
  The HA test fixture now seeds the generic tools too, since those are the
  tools under test.
* **S1 — the one POST that keeps working.** `docs/INTEGRATIONS.md` now says
  that `ha_check_config` still runs while frozen, because it only validates.
* **S2 — tool naming** is the operator's call and is left as the open
  question from round 1 (see the round-1 Reviewer notes).

`bash scripts/verify.sh`: `1900 passed, 3 skipped, 7 warnings in 398.68s (0:06:38)`.

### Reviewer notes

**Round 1 (2026-11-09): changes requested.** Reviewed at `d43147f`;
`bash scripts/verify.sh`: `1893 passed, 3 skipped, 7 warnings in 346.84s (0:05:46)`.

**What holds.**

* **The endpoint table.** `ha_supervisor.py` is a fixed table. The slug is
  validated before formatting, and the response shapers are allowlists:
  `options` is never returned, and lists are capped.
* **401/403 mapping.** A 401 or 403 becomes `ha_admin_required`.
* **`ha_whoami`.** It returns only name, `is_admin` and `is_owner`.
* **Generic reads.** Chunk H's `DENY_PREFIXES` already keep `supervisor/`
  and `hassio/` out of the generic `ws_read`.
* **Freeze routing.** The freeze decision goes through
  `read_policy.is_read_integration_call`, and its `read_only` guard is the
  right addition.
* **Approval while frozen.** It is refused with a 409 and audited, and the
  call stays pending.
* **Seeding.** It is idempotent.
* **No live data.** The fixtures use `192.0.2.x`.

*Must fix:*

* **R1 An agent-set `dry_run` flag gets a write past the freeze.** The
  freeze check skips any call whose args contain `dry_run: true`. Only
  *handler* tools honour that flag, though, and the gating code further down
  checks `handler and args.get('dry_run')`. For a curated REST tool, a
  generic tool or a WS tool, `dry_run` is just one more argument. In a
  scratch test, a frozen `call_service` with `dry_run: true` was **queued
  for approval** instead of refused, against "queues nothing". A tool that
  its gate policy leaves ungated would go on to run upstream while frozen.
  **Fix:** exempt only `handler and args.get('dry_run')`, the same
  condition the gate uses. **Test:** while frozen, `dry_run: true` on a
  curated REST write, on `ws_write` and on the generic `write` tool is
  refused with `frozen`, queues nothing and makes no upstream request. The
  existing handler dry-run test stays green.
* **R2 `ws_write` can still send any Supervisor endpoint.** The acceptance
  criterion is "no code path can send an endpoint that is not in
  `ha_supervisor.py`'s table". The approval-gated generic `ws_write` tool
  still forwards `supervisor/api` (and `hassio/*`) with an endpoint and
  method chosen by the agent. Because HA has no allowlist of its own, one
  tired approval reaches host reboot, backup restore or add-on options. The
  static scan in `test_f9_no_other_module_builds_a_supervisor_payload` does
  not see this, because the command comes from the agent's arguments at run
  time. **Fix:** refuse `supervisor/` and `hassio/` commands in `ws_write`
  before connecting, with a reason that points at the curated tools. F10
  will add the reviewed action endpoints to the table. **Test:** `ws_write`
  with `supervisor/api` (and a `hassio/` command) is refused before any
  WebSocket opens, both at call time and if a pending call is approved.

*Should fix:*

* **S1 `ha_check_config` and the freeze.** It is a `read_only` POST, so it
  runs while frozen. That matches "validates and changes nothing", which is
  fine. Say so in `INTEGRATIONS.md` next to the freeze note, so the operator
  knows the one POST that keeps working.
* **S2 Tool naming (the implementer's open question).** Drop the `ha_`
  prefix: the MCP name already starts with the integration's name, and
  `home_assistant_supervisor_info` reads better than
  `home_assistant_ha_supervisor_info`. This is the operator's call. It is
  one rename per tool, plus the name test.

**Merge note: `core/read_policy.py` exists on three branches.** F9, F14 and
F4 each create it. F9 and F14 have the same function, with different
docstrings. F4's version has no `read_only` guard in
`is_read_integration_call`. When F4 merges, keep F4's SSH half, take F9's
guarded integration wrapper, and add a test that a curated write tool is not
a read (`test_f9_a_curated_write_tool_is_not_a_read`). Whichever of F9 and
F14 merges second only needs to resolve the docstring.


**Round 2 (2026-11-09, main feature reviewer): approved and merged.** R1 is right: the freeze exemption now uses the same `handler and dry_run` condition as the gate. R2 is right: `supervisor/` and `hassio/` are refused before gating and again in `execute_ws_call`. Merge: `read_policy.py` takes F9's docstring. `tool_handlers.py` holds one `HANDLERS` table with F14's Pi-hole handler and F9's two handlers.

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

**Status: implemented, awaiting review.** The acceptance criteria include a
reviewer security pass (try to reach every FORBIDDEN endpoint), so this note
lists where to push and what the tests already prove.

**Commit:** the code and tests are in `1e14827` (`feat(f10): HA Supervisor
actions (approval-gated)`) on `feat/f10-ha-supervisor-actions`. This note is
a separate docs commit.

#### 1. What was checked against the HA Core source

Checked on 2026-11-09 against `home-assistant/core@dev`:

* `homeassistant/components/hassio/websocket_api.py` — the `supervisor/api`
  command requires `endpoint` (string) and `method` (string), accepts optional
  `data` (dict), `params` (dict) and `timeout` (number), is `@require_admin`,
  and hands `data` to the Supervisor passthrough client; the WS result is
  `result["data"]`. `hassio/update/addon` requires `addon` (str) **and**
  `backup` (bool); `hassio/update/core` requires `backup` (bool). Both are
  `@require_admin` and delegate to `update_helper.update_addon/update_core`.
* `homeassistant/components/hassio/const.py` — `WS_TYPE_API = "supervisor/api"`.
* `homeassistant/components/hassio/update.py` / `update_helper.py` — the
  update WS commands schedule the same work the Supervisor's
  `/addons/{slug}/update` and `/core/update` endpoints do; the command returns
  after the Supervisor call completes, which is the source of the timeout note
  in §5.

**Not verifiable from that source:** the Supervisor itself is closed-source,
so the REST endpoint names and payloads (`/backups/new/full`,
`/backups/new/partial`, `/core/restart`, `/host/reboot`,
`/addons/{slug}/{action}`) come from the Supervisor API docs and this chunk's
spec, not from HA Core. That is recorded as open question 1 rather than
assumed silently.

#### 2. The table

`core/ha_supervisor.SUPERVISOR_ACTIONS` maps each tool to one fixed
(endpoint, method) pair; `WS_ACTIONS` holds the two update commands. The agent
supplies arguments only, and only as validated placeholders:

| Tool | Fixed target | Arguments | Notes |
|---|---|---|---|
| `ha_addon_action` | `POST /addons/{slug}/{action}` via `supervisor/api` | `slug`, `action` | `action` must be one of start/stop/restart (the only `{action}` values in the table) |
| `ha_addon_update` | WS `hassio/update/addon` | `slug`, `backup` | |
| `ha_core_update` | WS `hassio/update/core` | `backup` | |
| `ha_backup_create` | `POST /backups/new/full` or `/partial` | `name`, `partial_addons`, `partial_folders` | the *payload* decides partial vs full; nothing agent-supplied reaches the path |
| `ha_core_restart` | `POST /core/restart` | — | refused unless a valid `ha_check_config` ran on this integration in the last 10 min |
| `ha_host_reboot` | `POST /host/reboot` | — | seeded **disabled**; summary is `REBOOTS THE WHOLE HA HOST` and the card renders it in red |

All six are `read_only: False`, `always_gate: True`, `handler:
ha_supervisor_action`, so each call creates a pending approval and **nothing
runs before the operator approves it**. Freeze refuses before queueing (the
generic F9 path, tested). `ha_check_config` now has a handler so its result is
recorded in `meta` under `ha_check_config_<integration>` (`result`, `at`): a
valid check on one HA instance never vouches for another.

#### 3. FORBIDDEN

`FORBIDDEN` lists restore (`/backups/`), add-on options/install/uninstall
(`/addons/`), `/supervisor/options`, `/host/shutdown`, `/store/repositories`,
`/auth*`, `/security*`, `/os/datadisk*` and `/os/boot-slot`. A table test
walks every endpoint the read and action tables can produce and every WS
command, and fails if one starts with a forbidden prefix. The one deliberate
exception is documented in the test: `/backups/new/full|partial` are the only
`/backups/` paths reachable, and `/addons/{slug}/{start|stop|restart}` the only
`/addons/` ones. The generic WS tools remain unable to send
`supervisor/`/`hassio/` commands at all (F9 R2), and an approved pending call
runs the *handler*, not a raw command, so that does not change here.

#### 4. `call_service(domain='hassio')`

Refused with an audited `hassio_domain_refused` (403) before any queue entry,
unless the agent's own policy lists `hassio` in the `ha_call_service`
`domain_allow` list — the operator's explicit, audited escape hatch. The
refusal message points at the F10 tools. Tested both ways, plus the "other
domains are untouched" case.

#### 5. Preflight, the summary, and timeouts

* Each action is validated by `_preflight_ha_supervisor_action` *before* the
  approval is created: bad slug, bad action, bad backup name, an update with
  no version available (`ha_update_not_available`) and a restart without a
  recent valid check are refused instead of queued.
* A viable action returns `{'_summary': …}`. `tool_runner` stores it in the new
  `pending_integration_calls.summary` column, and the approval card renders it
  (a red line for the reboot). The add-on update summary is
  `addon_update | <slug> | <current> -> <target>`, read from
  `/addons/{slug}/info`; core update reads `/core/info`.
* **Timeouts (found while checking the source):** `supervisor/api` defaults
  its per-call `timeout` to **10 s**, and updates/backups can run for minutes.
  The action path therefore sends `timeout: 600` for backups and `120` for the
  other supervisor/api calls, and raises the WebSocket read timeout to 620 s
  (backup/update) or 60 s (everything else). Reads keep the 30 s default.

#### 6. Plan items of type `mcp`

`session_plan_items` gains `kind` (`ssh` default, so pre-F10 rows are
unchanged), `integration`, `tool` and `args_hash`. An `mcp` item is proposed as
`{kind: "mcp", integration, tool, args: {...}, max_uses}` and the dashboard
stores the **hash only** — an HA service payload can carry a secret, and a plan
row is rendered in the UI. Matching in `tool_runner` is integration + tool +
the exact argument hash, one use per claim, through the same conditional
UPDATE as the SSH claim; a different argument hash never matches. The SSH
claim now filters `kind='ssh'`, so the two item types cannot cross-claim.

#### 7. Tests

`tests/test_f10_ha_supervisor_actions.py` (39) covers every case in the chunk:
right endpoint/payload per action; unknown action and bad slug refused; restart
without a recent valid check refused and queued after one; the reboot tool
seeded disabled; the FORBIDDEN table walk; `call_service` with `hassio`
refused by default and allowed only with an explicit policy; the preflight
summary; an `mcp` plan item approving exactly one matching call and nothing
else; freeze queueing nothing. `tests/test_ha_mcp.py` and
`tests/test_integrations.py` were updated for the six new seeded tools.

`bash scripts/verify.sh`: `2370 passed, 7 warnings in 445.42s (0:07:25)`.

#### Open questions

1. The Supervisor REST payloads (backup create, restart, reboot, add-on
   action) are closed-source; they come from the Supervisor API docs and this
   chunk's spec. Please confirm `/backups/new/partial` takes
   `{name, addons, folders}` against a live Supervisor before this ships.
2. A slow add-on/Core update can exceed the 620 s read timeout. The Supervisor
   continues the update; the tool then reports a timeout, which reads as a
   failure to the agent. Options: a fire-and-poll mode (start, then read
   `ha_addons`/`ha_core_info` for `version`), or a larger cap. This is a UX
   decision, not a security one.
3. The reboot warning is a red meta line driven by the tool name + summary.
   A dedicated "destructive" card style belongs to the UI pass.


### Reviewer notes

**Round 1 (2026-11-09, main feature reviewer): changes requested.** 177 F10,
F9, F3, HA and integration tests pass locally.

*What holds:*

* Every action is always-gated and audited, and freeze blocks it.
* `SLUG_RE` (`^[a-z0-9_]{1,64}$`) makes path tricks in slugs impossible.
* Shapers pass only named fields, so `options` never reaches an add-on.
* `ha_host_reboot` is seeded disabled.
* `ha_core_restart` needs a recent valid config check.
* `call_service(domain='hassio')` is refused unless allowed (domain check
  is trimmed and case-folded).

*Must fix:*

* **M1 The generic HA REST tools reach the Supervisor.** F9 R2 closed
  `ws_write`, but the HA integration's generic REST `read`/`write` are not
  checked for:
  * `/api/hassio/…`, HA Core's Supervisor proxy, which admin tokens can use;
  * `/api/services/hassio/…`, the same services `call_service` now refuses.

  So "FORBIDDEN endpoints are unreachable from every tool" does not hold:
  a backup restore or host shutdown is one approval away through the
  generic write. Fix: in `tool_runner`, for HA-kind integrations, refuse a
  generic REST call whose normalised path (percent-decoded, lowercased,
  `//` collapsed, leading `/` and an `api/` prefix stripped) starts with
  `hassio/` or `services/hassio/`, before gating and when a pending call is
  approved (mirror F9 R2).
  * Extend the FORBIDDEN table test to walk **every** HA tool, including the
    generic read and write, with each spelling.
  * Confirm against HA Core's `components/hassio/http.py` which `/api/hassio`
    paths exist, and record it in the notes.
* **M2 MCP plan items are not bound to the caller.** `claim_mcp_plan_item`
  matches on `session_id`, integration, tool and the argument hash. But an
  MCP caller can name **any** session id (`resolve_session` takes the
  explicit value as is). Agent B could therefore spend agent A's approved
  "update add-on X" item. Require the session's owner to equal the calling
  agent (fail closed for ownerless sessions, as in F3's SR2) before
  claiming. Tests: B cannot claim A's item, and A claims it exactly once.

*Should fix:*

* **S1** `ha_host_reboot` should never be plan-coverable. Each reboot gets
  its own approval card with the red line. Refuse `mcp` plan items naming
  it when a plan is proposed.
* **S2** Answer to the open questions: keep the partial-backup payload
  behind a "verify against a live instance" note. Raise long-update
  timeouts as their own setting, not the shared read timeout. The
  "destructive" card style goes to the UI pass list.

**Needs the security reviewer before merge (§ 2.6a).** F10 adds a new
auto-approve path for integration calls (plan items of kind `mcp`), in
addition to the Supervisor actions.

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

**What I did**

* **MCP `session_conclude`** (`core/mcp_server.py`, `core/conclusions.py`):
  outcome in `solved|partial|failed|abandoned`, summary required, worked /
  did_not_work items as `{"ref": "req:2594", "note": "..."}` (or a plain
  note); every ref is checked against **this** session — a request or
  integration call from another session, or an unknown id, is
  `ref_not_in_session`. The session's owner (when it has one) must match the
  caller. One conclusion per session per agent: a second call replaces the
  draft; a **confirmed** one is final (`already_confirmed`). Everything is
  length-capped and `secret_scrub`bed on write.
* **Storage** (`db/conclusions.py`): `session_conclusions` (session, agent,
  goal copy, all fields, status `draft|confirmed|rejected`, operator note,
  timestamps) with an **FTS5** table over summary, root cause, fix, tags and
  the session goal, kept in step by rowid (no external-content table, no new
  dependency). Status transitions are SQL-conditional, so a confirmed
  conclusion cannot be edited or re-drafted.
* **Operator review** in the session modal, under F6's wrap-up card: outcome,
  tags, summary, worked / did-not-work (refs labelled `request #N` /
  `call #N`), root cause, fix, follow-ups and the operator note, with
  **Confirm**, **Edit** (inline textareas for summary / root cause / fix) and
  **Reject** for a draft; a confirmed runbook candidate shows **Promote to
  runbook**, and a promoted one shows `Runbook #N`. All agent text is escaped;
  nothing is ever executed. Endpoints: `POST /api/conclusions/{id}/confirm`
  and `/reject`, `PATCH /api/conclusions/{id}`,
  `POST /api/conclusions/{id}/runbook`, each audited; `GET
  /api/sessions/{sid}/detail` now also returns `conclusions` (scrubbed again
  on read) and `runbooks`.
* **Runbooks**: promotion is server-side and operator-only. Each cited
  command is copied **verbatim** from the request row with F1's breakdown; a
  cited call keeps integration/tool/method/path; an uncited note becomes an
  instruction; did_not_work becomes `dead_ends`. Steps are capped at 20, and
  runbooks are documents, not executable (F16 can turn one into a recipe).
* **Retrieval**: `find_runbooks(query, tags?)`, `similar_incidents(query)` and
  `get_runbook(id)` return **confirmed** material only, top 5 (with a `more`
  flag), each incident under ~1,500 tokens (6,000 chars, trimmed with
  `truncated`). `session_summary` now carries `conclusion` (id/status) and a
  `next_step` reminder to call `session_conclude`.
* **Secrets**: every text field is scrubbed on write, and the stored payload
  is scrubbed again as JSON on read (`scrub_read`, `_scrub_payload`), so a
  token that slipped past one pass is still masked.
* **Screenshot**: `docs/screenshots/f13/conclusion-review.png` (source
  `conclusion-review.svg`) shows the draft review card with synthetic data
  (`sess-f13-nginx`, `web01`, `192.0.2.10`). The build box has no browser, so
  it is rendered from the shipped markup/CSS palette through SVG + sharp
  rather than captured from a live session; the strings match the node
  harness fixture.
* **Tests**: `tests/test_f13_session_conclusions.py` (22) — refs (foreign,
  unknown, malformed), outcome/length/tag limits, ownership (ownerless
  sessions stay usable), draft replacement, confirmed finality, reject then
  re-draft, confirm/edit only on a draft, only-confirmed retrieval, the FTS
  ranking smoke test, write/read scrubbing, the 5-item cap, promotion
  (verbatim command + breakdown + refs, tool steps, instructions, dead ends,
  requires confirmed and candidate), runbook search/tags/get, the MCP tools
  and the summary reminder, the API review/promote path and its 404/409s,
  session auth, and a node harness for escaping and the card's states and
  buttons. `tests/test_omada_mcp.py`'s per-integration tool set gains the
  four new built-ins.

**Decisions**

* A confirmed conclusion is immutable for its agent; the operator can still
  edit a draft before confirming. Rejecting a draft lets the same agent try
  again (a new draft, same row).
* `session_conclude` requires the session to exist and, when it has an owner,
  requires the caller to be that owner. Ownerless sessions (an adopted chat
  id) stay usable, matching the pre-SR2 plan rule.
* The four knowledge tools are in `_ALWAYS_ALLOWED` with the session tools:
  they are session-scoped or read only confirmed, operator-approved material,
  and an allowlist that cannot see `session_conclude` could never record a
  conclusion. `session_conclude` writes a *draft*, never an approval.
* `similar_incidents` quotes its FTS terms (implicit AND) so a query with FTS
  syntax cannot error the tool; an empty query returns nothing rather than
  everything.
* The FTS index is standalone (not `content=` external) so `rowid` is always
  the conclusion id; the database module keeps it in sync on write and edit.
  A SQLite build without FTS5 would fail at table creation, which is correct
  for a chunk that requires it (the venv's SQLite has it).

`bash scripts/verify.sh`: `2032 passed, 3 skipped, 7 warnings in 415.73s (0:06:55)`.

### Reviewer notes

**Round 1 (2026-11-09): approved and merged.** 178 F13, F6 and MCP tests
pass locally. What I checked and what holds:

* Refs are validated against the session.
* Only `confirmed` items are retrievable.
* FTS terms are quoted (`_fts_match`), so agent text cannot inject FTS
  syntax.
* Secrets are scrubbed on write and on read.
* Agent text in the review UI is escaped (harness).
* Status changes are conditional.

Follow-ups (S, non-blocking):

* **S1** Ownership in `save_conclusion` is `owner and agent != owner`, so an
  ownerless session takes a draft from anyone. That is harmless (the
  operator reviews every draft), but make it fail closed for consistency
  with F3's SR2.
* **S2** `search_runbooks` matches the whole query as one LIKE phrase.
  Search per term (or through FTS) so that "nginx 502" finds a runbook
  mentioning both words apart.
* **S3** Confirmed runbooks are later read by other agents, so a confirmed
  text is effectively an instruction to them. The operator's Confirm is the
  only gate; say so in the review UI ("agents will follow this").

---

## Chunks F14–F17 — Local-model support

Goal: let a small local model (e.g. a 14–20B model in LM Studio on a 16 GB GPU)
handle routine homelab support by moving the intelligence to **authoring time**:
a strong model or the operator works something out once, Eshu stores it as
reviewed building blocks, and the local model picks one and fills in parameters.
Small models do well with **few tools, short descriptions, small structured
results, and no free-form shell**. Every chunk below is built around that.

Shared rules for F14–F17:

* **Token budget.** Every result an agent sees is compact JSON under a stated
  cap. When it is cut, it says so (`"truncated": true`) and says how to ask
  for more.
* **Reads go through `core/read_policy.py`** (§ 2.6a): integration reads
  through `is_read_integration_call`, and SSH reads through `is_read_ssh`
  (F4).
* **No new execution path.** F14 reads integrations. F15 and F16 run SSH
  only through the F12 runner and the normal approval path. Nothing here
  bypasses freeze, Zero-Trust, the blocklists or JIT.

---

## Chunk F14 — Inventory (`whereis`) · slug `inventory` · dashboard + MCP

### Goal

One lookup answers "where is X?" for a service, host, container, IP or name:
which host it runs on, its container or VM id, its IPs, the URLs and FQDNs that
point at it, its proxy entry and DNS records, and whether an Eshu gateway is
on it.

### Design

* **Sources**, read-only and each optional (skipped if the integration is not
  configured):
  * **Eshu:** gateways (ip, hostname), F7 inner targets.
  * **Proxmox:** `get_cluster_resources` (node, vmid, name, type, status).
    Guest IPs come from `get_container_status` / agent data where present.
  * **NPM:** `proxy_hosts` (domain names → forward host:port, SSL).
  * **Pi-hole:** local DNS records. **Add** a read tool `local_dns_records`
    to `pihole_seed.py` (v6 `GET /api/config/dns/hosts` and
    `cnameRecords`). It must pass Chunk H's read check.
  * **Omada:** `list_known_clients` (name, MAC, IP, last seen).
  * **HA:** optional, add-on slugs only, after F9.
* **Refresh:** a background job every 15 min (setting, 5–120) plus a manual
  "refresh now". Each source is cached in `inventory_items(source, kind, key,
  name, ips, fqdns, host, vmid, url, extra_json, seen_at)`. A failed source
  keeps its last good rows and is reported as stale.
* **Join** on IP first (gateway ↔ Proxmox guest ↔ Omada client ↔ NPM forward
  host ↔ Pi-hole record), then on a normalised name. The result is an
  **entity** with the fields above and the list of sources that contributed.
  Conflicts (two sources disagree on an IP) are shown, not resolved.
* **MCP tool** `whereis(query)`: exact or substring match over name, FQDN, IP,
  vmid and MAC. It returns up to 5 entities, each ≤ 600 chars, with `sources`
  and `stale` flags. It is always allowed, the same as for `session_begin`.
  It goes through agent policy like any tool.
* **UI:** an Inventory page (or a tab under Integrations). It has a search
  box, an entity list showing source chips, and a stale badge on each source.
  Every string is escaped.

### Tests to add

* A join fixture with synthetic data across all five sources (`192.0.2.x`,
  `web01`, `app.example.test`), plus a conflict case.
* A source that fails keeps its previous rows and is reported stale.
* `whereis` caps: 5 entities, each cut at 600 chars with `truncated`.
* `local_dns_records` passes `is_read_integration_call`.
* Escaping in the UI (node harness). The refresh interval is clamped.

### Compat impact

Additive. With no integrations configured, `whereis` only returns Eshu's own
gateways.

### Acceptance criteria

- [ ] Every source read goes through `read_policy.is_read_integration_call`.
- [ ] No live data in fixtures or screenshots.

### Implementer notes

**What I did**

* **`core/read_policy.py` (§2.6a)**: the same minimal, integration-half module
  the task asks for — `is_read_integration_call(tool, transport, args)` wraps
  Chunk H's `_read_only_refusal` and adds the `read_only` guard. F4's branch
  holds the full file (SSH rules); this one will be merged with it, and the
  guard must survive that merge. **Every source read goes through it**:
  `core.inventory._call_tool` refuses a source tool that is not a read before
  anything is sent.
* **`local_dns_records`** (`core/pihole_seed.py` + a handler in
  `core/tool_handlers.py`): two fixed GETs, `config/dns/hosts` and
  `config/dns/cnameRecords` (Pi-hole v6; the integration's base URL ends in
  `/api`). The payload is accepted in both shapes FTL uses — nested under
  `{"config": {"dns": {...}}}` and `"<ip> <name>"` / `"<name>,<target>"`
  strings — and returned as `{hosts: [{name, ips}], cnames: [{name, target}]}`.
  It is `read_only`, so `is_read_integration_call` passes; the description says
  it needs a v6 integration.
* **`core/inventory.py`**: five source adapters — Eshu (gateways + F7 inner
  targets), Proxmox (`get_cluster_resources`), NPM (`proxy_hosts`), Pi-hole
  (`local_dns_records`), Omada (`list_sites` then `list_known_clients` per
  site, capped at 10 sites). Each is optional; a missing integration is
  skipped, a failed one records its reason. `refresh_inventory()` swaps one
  source's rows in one transaction and records the result in
  `inventory_sources`; a failure **keeps the last good rows** and marks the
  source stale (last error, or no success for two intervals).
* **Join** (`build_entities`): union-find over `ip:<ip>`, `name:<normalised>`
  (the name, each FQDN, and an FQDN's first label when it is at least three
  characters) and `vmid:<host>:<vmid>`. An entity carries name, IPs, FQDNs,
  hosts, vmids, URLs, kinds, the contributing sources (with their staleness)
  and `conflicts` — an IP claimed by two different names is listed, never
  resolved silently.
* **`whereis(query)`**: exact matches first, then substrings, over name, IPs,
  FQDNs, vmids, hosts, URLs and the MAC/inner-target extras. Up to **5**
  entities, each trimmed under **600 characters** with `truncated` set.
* **API**: `GET /api/inventory` (snapshot + `?q=` search), `POST
  /api/inventory/refresh` (audited, synchronous — the operator pressed it),
  `POST /api/inventory/settings` (interval clamped to 5–120, default 15,
  audited). The background loop sleeps one interval before its first refresh,
  so a boot never fires source calls while the dashboard starts.
* **MCP**: `whereis` is a built-in on the shared and per-integration servers
  (and in `_ALWAYS_ALLOWED`, like `session_begin`) — it answers from Eshu's own
  cache and makes no upstream call on the request, so there is nothing for the
  agent policy to gate.
* **UI**: an Inventory page (sidebar) with a search box, the per-source chip
  row (counts and stale dashes), the interval control, "Refresh now", and one
  card per entity: name, ip/name/host/guest/url/mac facts, source chips and
  the conflict line. Every string is escaped; a stale entity is dimmed.
* **Tests**: `tests/test_f14_inventory.py` (18) — the five-source join (one
  entity, all sources), the read-only source calls, a conflict, a failed
  source keeping its rows and turning stale, Eshu-only with no integrations,
  the 5-entity and 600-character caps, matching by IP/vmid/MAC/name, interval
  clamping, `local_dns_records` passing the read check, the v6 parser on both
  payload shapes, the upstream-error path, the three endpoints (session auth
  included), and two node-harness checks (escaping; stale + conflict markers).

**Decisions**

* **HA add-on slugs are skipped**: the chunk says "after F9", and F9 is not on
  `feature-roadmap`. Adding the source later is one adapter.
* The **manual refresh is synchronous** so its result is real (the operator
  pressed a button and gets counts). The background loop sleeps first and then
  refreshes on the interval, so it never competes with a cold start.
* The **join is deliberately loose on FQDNs** (first label) — `app.example.test`
  should reach a guest named `app`. A wrong join is visible as a conflict
  rather than hidden, and an entity lists every source that contributed.
* Omada is the only source needing two calls (sites, then clients); sites are
  capped at 10 so one controller cannot multiply the refresh cost.
* The inventory stores **only read-only metadata** (names, addresses, ids,
  URLs, MACs, a few status flags) and never a secret; the response scrubber
  still runs on upstream responses before anything is cached.

`bash scripts/verify.sh`: `1874 passed, 3 skipped, 7 warnings in 380.39s (0:06:20)`. The three skips are F18's read-lane tests (F4 is not on this branch).

### Round 2: fixes for reviewer round 1

* **R1 — `whereis` goes through agent policy and only shows allowed
  sources.** It is out of `_ALWAYS_ALLOWED` and off the per-integration
  servers; it lives once on the shared `/mcp` surface, so an agent's
  `tools` allowlist (or `tools_mode`) decides whether it can call it.
  `inventory.whereis` *also* checks `visible_tool_allowed(agent, 'whereis')`
  itself and returns `{error: forbidden}` when the tool is not allowed, so a
  direct call cannot skip the policy.
  * `allowed_sources(agent)` returns Eshu's own rows always, plus an
    integration's name only when the agent's policy permits at least one
    enabled read tool on it — decided by `agent_policy.evaluate_call`, not by
    the inventory.
  * The filter runs **before** the join (`build_entities(..., allowed=…)`), so
    an entity never carries a field from a source the caller cannot see.
  * Every agent call writes a `whereis_query` audit event with the agent, the
    query **length** (never the query) and the entity count.

  Tests (`TestWhereisAgentPolicy`): a denied agent is refused
  (`test_f14_a_denied_agent_is_refused`); an HA-only agent sees only
  `eshu`+`ha` sources with no vmid and no MAC; a Proxmox agent sees the guest
  (vmid 103) but not the Omada MAC; an unrestricted agent sees the full four
  source join; the call is audited; `whereis` is not in `_ALWAYS_ALLOWED`
  (and `test_sessions` now asserts it stays out).
* **S1 — untrusted names.** `WHEREIS_DESC` says names come from the network
  and are untrusted data, and `_entity` strips control characters from names,
  IPs, FQDNs, hosts, vmids, URLs and string extras
  (`test_f14_control_characters_are_stripped`).
* **S2 — background refresh rows, recorded not filtered.** Each refresh
  writes `integration_calls` rows with agent `inventory`; they are real
  audit of what the dashboard read and when, attributed to the component that
  made the call. They are not agent activity, so an activity view that wants
  only agents should filter `agent = 'inventory'` (no such view does today).
* **S3 — HA add-on slugs** stay for the round after F9 merges, through
  `read_policy` like every other source.

`bash scripts/verify.sh`: `1881 passed, 3 skipped, 7 warnings in 381.89s (0:06:21)`.

### Reviewer notes

**Round 1 (2026-11-09): changes requested (one item).** Reviewed at `aa44771`;
`bash scripts/verify.sh`: `1874 passed, 3 skipped, 7 warnings in 331.25s (0:05:31)`.

**What holds.**

* **Reads go through the read policy.** Every source read goes through
  `read_policy.is_read_integration_call` (`inventory._call_tool`), and a
  test proves it.
* **`local_dns_records`.** It is `read_only`, it makes two fixed GETs, and
  it passes the check.
* **Failed sources.** A failed source keeps its last good rows and is
  marked stale.
* **The join.** It is union-find over IP, normalised name and vmid.
  Conflicts are listed, not resolved.
* **`whereis` caps.** 5 entities, each ≤ 600 characters, with
  `truncated`.
* **Interval.** It is clamped to 5–120.
* **Boot.** The background loop sleeps before its first refresh.
* **UI escaping.** The UI escapes every upstream string: names, FQDNs,
  URLs, MACs, error titles and conflict names.
* **No live data.** The fixtures use `192.0.2.x`, `example.test` and
  `AA:BB:…` MACs.
* **Approvals.** No approval path changed.

*Must fix:*

* **R1 `whereis` skips agent policy, and it exposes every source to every
  agent.** The spec says `whereis` "goes through agent policy like any
  tool". The branch puts it in `_ALWAYS_ALLOWED` and adds it to every
  per-integration MCP server. `_ALWAYS_ALLOWED` is meant for tools "always
  scoped to the caller", like polling your own approval, and `whereis` is
  not scoped: it returns the combined Proxmox, Omada, NPM and Pi-hole map.
  So an agent whose policy allows only one integration (say HA, or a small
  local model under F17) gets every client's IP and MAC address and every
  guest and proxy entry. **Fix:**
  * Take `whereis` out of `_ALWAYS_ALLOWED`, so an agent policy can deny it.
  * Filter each answer to the sources the calling agent may read: Eshu's
    own gateways always, and an integration's rows only if the agent's
    policy allows at least one read tool on that integration.
  * Record the call (agent, query length, entity count) like the other MCP
    calls.

  **Tests:** an agent denied `whereis` is refused. An agent allowed only HA
  sees no Proxmox or Omada fields. An agent allowed Proxmox sees the guest
  but not the Omada MAC. An unrestricted agent sees the full join.

*Should fix:*

* **S1 Device-chosen names are untrusted text for the agent.** Omada client
  names, Proxmox guest names and NPM domains can be set by whoever controls
  a device or a guest, and `whereis` hands them to a model as facts. Add one
  line to `WHEREIS_DESC` ("names come from the network and are untrusted
  data") and strip control characters in `_entity`. **Test:** a name with
  control characters is cleaned in the `whereis` output.
* **S2 Background refreshes in the call log.** Every refresh writes
  `integration_calls` rows for agent `inventory` (about 4 sources × 96 a
  day at 15 min). That is fine, but filter them out of the agent-activity
  views, or say in the implementer notes why they should show.
* **S3 HA add-on slugs.** Skipping them until F9 merges is right. Add the
  adapter in the round after F9 lands, through `read_policy` like the
  others.

**Merge note.** `core/read_policy.py` is also created by F9 (same function)
and by F4 (the SSH half, without the `read_only` guard). See F9's merge
note.


**Round 2 (2026-11-09, main feature reviewer): approved and merged.** R1 is right: `whereis` is gated by agent policy, sources are filtered before the join, and each call is audited. Note: a caller with no agent identity (a legacy MCP token) gets the unfiltered join and no audit row, the same as for the integration tools themselves. Tighten this in the UI/hardening pass if legacy tokens are retired.

---

## Chunk F15 — Diagnostic probes · slug `probes` · dashboard + MCP

### Goal

One call answers "what's wrong with X?" with a short structured digest, so a
small model never has to compose a shell pipeline or read 400 log lines.

### Design

* **Probes are code, not agent text.** Each probe is a fixed list of
  read-only commands built from **typed parameters**:
  * `host`: a gateway IP from the registry
  * `unit`: a systemd unit, `^[A-Za-z0-9@._-]{1,128}$`
  * `container`: a Docker name or Proxmox vmid
  * `name`: an FQDN, `^[A-Za-z0-9.-]{1,253}$`

  Each value is validated, then shell-quoted with `shlex.quote`. Every
  generated command must pass `read_policy.is_read_ssh`. A test asserts this
  for each probe with sample parameters.
* **The set to start with:**
  * `diagnose_service(host, unit)`: `systemctl status` (capped),
    `journalctl -u unit -n 200`, restart count, ports via `ss -tlnp`.
  * `diagnose_container(host, container)`: `docker ps`/`docker logs --tail`
    or `pct status`.
  * `disk_pressure(host)`: `df -h`, `df -i`, the largest log dirs under
    `/var/log`.
  * `dns_check(name)`: the Pi-hole record (F14), and on the gateway that NPM
    reports, `getent hosts`.
* **Execution** goes through the F12 runner as normal requests. With F4 `on`
  for that host, they auto-run; otherwise they go to the normal approval
  queue as **one** grouped approval (an F3 plan the probe proposes itself).
* **Digest:** the dashboard parses the outputs (F5 results) into
  `{status, active_since, restarts_24h, errors: [{msg, count, last}],
  ports, disk, recent_changes}`.
  * Errors are de-duplicated by message after stripping numbers and hex.
  * `recent_changes` lists Eshu's own write-approved requests on that
    host in the last 24 h.
  * Hard cap 2,000 tokens, with `truncated`.
* **MCP tools:** one per probe, with short descriptions (≤ 200 chars each).

### Tests to add

* Every probe, for each sample parameter set, passes `is_read_ssh`. Hostile
  parameters (quotes, `;`, `$(`, newlines, `..`) are rejected by the type
  check before any command is built.
* Digest parsing on synthetic outputs: dedup, caps and `truncated`.
* Without F4 `on`, a probe creates one plan, not N pending requests.

### Dependencies

F12 (runner), F4 (read lane), F5 (outputs), F3 (grouped approval), F14 (for
`dns_check`).

### Implementer notes

_(implementer)_

### Reviewer notes

_(reviewer)_

---

## Chunk F16 — Recipes · slug `recipes` · dashboard + MCP · **design first + security reviewer sign-off**

### Goal

A reviewed, parameterised procedure (for example "add an internal FQDN": a
Pi-hole record, an NPM proxy host, then check with `dig` and `curl`) that
any agent, including a small local one, can run with one approval and
almost no inference.

### Design questions the design note must answer

1. **Shape.** Named, versioned procedures. A step is either an Eshu tool call
   (an integration tool with arguments) or an SSH command template on a
   named host. Each step has a check, plus an optional undo step. How are
   steps, checks and undo represented, and how is a check's pass or fail
   decided without free-form parsing?
2. **Typed parameters only.** Types are a closed set: `hostname`, `ipv4`,
   `port`, `fqdn`, `slug`, `int` and `enum`, each with a regex and
   normalisation. Values are substituted only into marked slots. In SSH
   templates the slots are `shlex.quote`d; in tool calls they are JSON
   values. Free text never goes into a shell. Show the substitution code.
3. **Approval model.** The operator approves a recipe **version** once,
   pinned to its content hash (any edit is a new, unapproved version). Each
   **run** then needs one approval showing the filled-in steps (reusing F3,
   so a run is a plan). A recipe whose every step passes `read_policy` may run
   without a run approval when F4 is `on` for those hosts. Is that right, or
   should read-only recipes still need approval in the first version?
4. **Authoring.** Who can create recipes? Hermes drafts them through
   `recipe_propose` (often from an F13 runbook), and only the operator
   approves. How is a draft shown for review, with each step rendered through
   the F2 breakdown?
5. **Failure.** When a step's check fails, does the recipe stop, run the undo
   steps in reverse, or ask? What is recorded (F5 results and audit)?
6. **Blast radius.** Limits on host count per run and on steps per recipe,
   plus a list of integration tools a recipe may never call (deletes,
   restarts of core services).

### Example to specify end to end

`add_internal_fqdn(fqdn, target_ip, port)` →
1. Pi-hole: add a local DNS record.
2. NPM: create the proxy host.
3. Check: `getent hosts fqdn` on a named gateway.
4. Check: an HTTP status via the generic read tool.

Undo: delete the NPM host, then delete the Pi-hole record.

### Implementer notes

_(design note goes here first)_

### Reviewer notes

_(reviewer)_

---

## Chunk F17 — Local-agent profile · slug `local-agent` · dashboard + docs

### Goal

A local model in LM Studio connects to Eshu over MCP in minutes, sees a small
toolset it can use well, and hands over to Hermes cleanly when a job is beyond
it.

### Design

* **Preset agent policy** `local-agent`, applied when the operator creates its
  token:
  * `tools_mode` allowlist: `session_begin`, `session_summary`,
    `session_conclude`, `whereis`, the F15 probes, `find_runbooks`,
    `get_runbook`, `recipe_list`, `recipe_run`, `ssh_exec` (read-only mode),
    `escalate`.
  * Generic reads are GET/HEAD only (Chunk H).
  * Every write needs approval.
* **Short descriptions.** A "compact" description variant per tool (≤ 160
  chars). The MCP server serves it when the agent's policy sets
  `compact_descriptions: true`.
* **`escalate(summary, session_id)`** notifies the operator and attaches the
  F6 session digest, plus a "for Hermes" hand-off record that Hermes can
  read with `get_escalation(id)`. It is always allowed.
* **Setup guide** `docs/LOCAL_AGENT.md`:
  * LM Studio's `mcp.json` entry (URL plus `Authorization: Bearer` header);
  * recommended models and context size for a 16 GB GPU (stated as
    guidance, not tested claims);
  * a system prompt template ("look it up with `whereis`, diagnose with
    probes, follow runbooks, run recipes, escalate when unsure");
  * what the agent cannot do.
* **Eval script** `scripts/eval_local_agent.py`: a fixed set of 10 synthetic
  support tasks run against a sandbox dashboard. It checks which tools the
  model chose, against the expected ones, so the toolset can be tuned. It is
  run manually and never in CI.

### Tests to add

* The preset creates the allowlist above; `ssh_exec` is read-only for this
  agent.
* Compact descriptions are served only with the flag set.
* `escalate` notifies the operator and stores the hand-off; `get_escalation`
  is scoped to the operator and the agents allowed to read it.

### Dependencies

F12–F16, F13.

### Implementer notes

_(implementer)_

### Reviewer notes

_(reviewer)_

---

## Chunk F19 — Time and output limits for unattended runs · slug `unattended-limits` · gateway + dashboard · **design first + security reviewer sign-off**

### Why

The F4 security sign-off listed this as a residual risk. The read-only lane's
parser is the only bound on run time and output: `df` on a stale mount, or
`docker` with a hung daemon, can hang, and the gateway has no execution timeout
or output cap of its own. The same applies to F15 probes and read-only F16
recipes. Any command that runs with no human watching should have a hard
bound at the gateway.

### Design (to confirm in the design note)

* The dashboard's `request_status` / ticket response for rows approved with
  no human (`reason` = `read-lane`, later probe or recipe runs) adds
  `run_limit: {seconds, bytes}`. Defaults are 60 s and 1 MiB; settings range
  over 5–300 s and 64 KiB–8 MiB.
* The gateway validates both as digits within the range. It wraps the run
  as `timeout --kill-after=5 "$secs" bash -c "$cmd"` and caps the stream
  after `sanitize` with `head -c "$bytes"`, printing
  `[ESHU limit: output cut at N bytes]` or `[ESHU limit: stopped after N s]`.
  It records the limit hit in F5's result (`limit: time|bytes`).
* Rows approved by a human are unchanged.
* The design note must show the exact bash and how it interacts with
  F5's `tee`/FIFO capture, `pipefail` and the `[ESHU exit=N]` sentinel. It
  must also cover old gateways: they ignore the field, and the dashboard
  refuses to auto-run on a gateway too old to enforce limits. That last rule
  is decided per gateway version.

### Tests to add (outline)

Gateway sandbox: a command that sleeps past the limit is stopped at N s
(rc 124); output past N bytes is cut, with the marker; human-approved runs
have no limit; a bad `run_limit` value means no auto-run. Dashboard: the limit
is sent only for unattended rows; old gateway versions are refused for `on`.

### Implementer notes

**Status: design note only — no code on this branch.** F19 is design first
and needs the security reviewer's sign-off before any code. It is also one of
the preconditions for unlocking F4's `on` mode, so it is the next gateway
change after F12.

#### 1. What is bounded, and what is not

A **run limit** exists only for a row the dashboard approved **without a
human**: today that is the read lane (`reason = read-lane`), later F15 probes
and read-only F16 recipes. The row's `reason` is the switch, and it is set by
the dashboard, never by the gateway.

* A human-approved run (JIT, plan, session override, host override, window,
  allowlist) keeps today's behaviour: no timeout, no cap. A human saw the
  command and can watch it. Changing that would be a different feature with
  its own review.
* A run limit is **advisory on the dashboard and authoritative on the
  gateway**: the gateway refuses a limit it cannot parse, ignores one it did
  not get, and never runs unbounded when it was told to bound.
* A limit hit is a *completed* run, not a refusal: `status: ran`,
  `exit_code: 124` for time (or the command's own code for a byte cut), and
  a marker line. The ticket is burned either way; a bounded run cannot be
  retried by replaying the ticket.

#### 2. Where the limit travels

The limit is a JSON field next to the payload, **not** part of the ticket
line. The ticket line is `v2|ts|agent|session|cmd` and is consumed by
gateways that may predate F19; adding a field there would either break them
or need a `v3` tag for no benefit. Three responses carry it, all on the
dashboard side:

* `POST /api/request` → `{"status":"ok","request_id":…,"run_limit":{…}}`
  when the row it just created is unattended. This is the path the read lane
  uses (the gateway gets `approved` immediately and runs without polling).
* `GET /api/request_status/{id}` → `run_limit` while `status` is `approved`,
  so the polling path and a late claim both see it.
* `GET /api/ticket/{id}` → the same field beside `ticket`, for a gateway that
  claims the ticket in one call.

A locked-box ticket claimed from the poller has no limit beside it, by
construction: those rows were approved by a human (or by a plan), and §1 says
they keep today's behaviour.

Shape and ranges (defaults 60 s / 1 MiB):

```
run_limit: {"seconds": 60, "bytes": 1048576}
seconds  5..300       bytes  65536..8388608
```

The values live in a `meta` key (`run_limit`) so the operator can set them
without a deploy, and a settings endpoint (`GET`/`PUT /api/settings/run-limit`,
session auth) validates the range. A missing key means the defaults; a
corrupt key also means the defaults (a broken setting must not remove the
bound). The dashboard never sends a value outside the range; the gateway
still checks.

#### 3. The gateway: parse, wrap, cap

The limit arrives as a JSON body the gateway already parses for
`request_id`. Two variables hold it, and `RUN_LIMIT_*` empty means "no limit"
(human-approved):

```bash
# F19: run limits for a row the dashboard approved with no human watching.
# Empty means "no limit" (a human approved this). Anything unusable also
# means no auto-run: never a partially applied bound.
RUN_LIMIT_SECS=""
RUN_LIMIT_BYTES=""

parse_run_limit() {   # $1 = dashboard JSON body
  local secs="" bytes=""
  secs=$(printf '%s' "$1" | python3 -c '
import json, sys
d = (json.load(sys.stdin) or {}).get("run_limit") or {}
sys.stdout.write("%d %d" % (int(d.get("seconds") or 0), int(d.get("bytes") or 0)))
' 2>/dev/null || echo "0 0")
  set -- $secs
  case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac
  case "${2:-}" in ''|*[!0-9]*) return 1 ;; esac
  if [ "$1" -lt 5 ] || [ "$1" -gt 300 ] || [ "$2" -lt 65536 ] || [ "$2" -gt 8388608 ]; then
    return 1
  fi
  RUN_LIMIT_SECS="$1"
  RUN_LIMIT_BYTES="$2"
  return 0
}
```

Called right after the `/api/request` response is parsed; a `run_limit` that
is present but unusable **refuses the call** (`[LOCKED] Invalid run_limit …`)
instead of running unbounded. A 200 response without the field leaves both
variables empty (human-approved path).

The run itself is wrapped inside `run_sanitized`, so every run path (read
lane, window, lockbox, JIT) is bounded by the same code and a future path
cannot forget it:

```bash
run_sanitized() {
  local rc=0 ... time_limited="0" byte_limited="0"
  local -a run_argv=("$@")
  if [ -n "$RUN_LIMIT_SECS" ]; then
    # -k (not --kill-after) is what both GNU and BusyBox timeout understand;
    # the extra 5 s only decides how long a stuck process gets to die.
    run_argv=(timeout -k 5 "$RUN_LIMIT_SECS" "${run_argv[@]}")
    time_limited="1"
  fi
  ...
  if [ -n "$RUN_LIMIT_BYTES" ]; then
    # F19: cap the stream AFTER sanitize (secrets are still redacted) and
    # BEFORE the FIFO, so F5 stores exactly the bytes the agent saw. The
    # pipeline runs under `pipefail`, so read the command's own status from
    # PIPESTATUS[0]; the byte cut is a separate flag, not a status.
    { "${run_argv[@]}" 2>&1 | sanitize | head -c $((RUN_LIMIT_BYTES + 1)) \
        | tee "$ESHU_OUT_FIFO"; } >/dev/null 2>&1
    rc=${PIPESTATUS[0]}
    LIMIT_HIT=""
    if [ -s "$ESHU_OUT_CAP" ] && [ "$(stat -c '%s' "$ESHU_OUT_CAP")" -gt "$RUN_LIMIT_BYTES" ]; then
      byte_limited="1"
      LIMIT_HIT="bytes"
      # Drop the probe byte so the cap is exact.
      truncate -s "$RUN_LIMIT_BYTES" "$ESHU_OUT_CAP"
    fi
  else
    ...today's branches, unchanged...
  fi
  if [ "$time_limited" = "1" ] && [ "$rc" -eq 124 ]; then
    LIMIT_HIT="time"
    say "   [ESHU limit: stopped after ${RUN_LIMIT_SECS} s]"
  fi
  if [ "$byte_limited" = "1" ]; then
    say "   [ESHU limit: output cut at ${RUN_LIMIT_BYTES} bytes]"
  fi
  ...
}
```

Details that matter, and why:

* **`head -c $((bytes + 1))` and a probe byte.** With exactly `bytes`, a stream
  that happens to end at the cap is indistinguishable from one that was cut.
  One extra byte distinguishes them; it is dropped before anything is stored
  or shown, so the cap the operator set is the cap that is kept.
* **`truncate -s`** (coreutils) may be missing on a minimal host. The
  fallback for that case is `head -c "$RUN_LIMIT_BYTES" "$ESHU_OUT_CAP" >
  "$ESHU_OUT_CAP.trim" && mv -f …`; the design note records the fallback so
  the implementer does not have to invent one. The marker and the F5 row are
  identical either way.
* **`pipefail` and `PIPESTATUS[0]`.** The existing code reads `$?` after a
  `||`, which is the *pipeline's* status under `pipefail`; for a capped run
  that would report `head`'s or `sed`'s SIGPIPE (141) as the command's exit
  code. Reading `PIPESTATUS[0]` gives the command's real status, so
  `[ESHU exit=N]` and the envelope's `exit_code` keep meaning "what the
  command returned". The SIGPIPE the writer gets when `head` closes is
  expected and is not reported.
* **The F5 capture still works.** The FIFO reader is unchanged; it now
  receives at most `bytes + 1` bytes, so `tail -c 16385` keeps the whole head.
  `post_exec_result` stores that, and a byte cut marks the row
  `truncated: true` as it does today. The new field is `limit: time|bytes`
  (empty when no limit fired), which is a new additive column
  (`request_results.limit TEXT DEFAULT ''`) and an optional body field on
  `/api/exec-result`.
* **`[ESHU exit=N]` in direct mode.** For a time limit N is `124` (GNU and
  BusyBox timeout both use it), so an SSH agent sees
  `[ESHU exit=124]` plus `[ESHU limit: stopped after 60 s]`. For a byte cut
  the sentinel is unchanged (typically `[ESHU exit=0]`) and the marker is the
  only sign, which is correct: the command was not stopped.
* **MCP mode.** The markers go through `say`, so they land in the envelope's
  `messages`; the exit code is the envelope's `exit_code`; a byte cut sets
  `truncated: true` exactly as a 64 KiB tail would. Nothing new is needed in
  the envelope format.
* **The timeout only bounds the run, not the whole call.** `timeout` kills
  the direct child (`bash -c`), and `-k 5` forces it 5 s later if it ignores
  SIGTERM. A command that backgrounds a child can still outlive it; that is
  the same statement F12 §4 makes about killing the local `ssh`, and the
  reason the dashboard's `timeout` and this limit are separate bounds.

#### 4. Old gateways and old dashboards

* **Old gateway, new dashboard.** The gateway ignores `run_limit` entirely,
  so nothing is bounded there. The dashboard therefore refuses to *auto-run*
  an unattended row on a gateway that has not reported the capability
  `run_limits: "v1"` (a new `gateways` column, sent by the installer in its
  existing `/api/register`, exactly like F12's
  `ssh_agent_format`/`ssh_ticket_format`). Instead of approving, the row is
  created `pending` with `reason = "read-lane-no-limits"` and an audit event
  `read_lane_no_limits`; the human sees it in the queue with that reason.
  With F4's `on` hard-locked, this is the second gate that must be green
  before `on` is unlocked at all.
* **New gateway, old dashboard.** No `run_limit` in the response → no limit →
  today's behaviour. The capability is reported but unused.
* **The capability is reported, not inferred from the version string.**
  Version strings lie after a dev update and a rollback; a reported
  capability is what the installer that wrote the script knows. It is stored
  on the gateway row and shown in the gateway card.
* **Rollout order.** F19 ships with every gateway on `off` (no lane), the
  capability starts empty, and the dashboard refuses `on` until every gateway
  the operator wants in the lane reports `v1`. That is the F4 § 8 gate, and
  it keeps "old gateway" a state that cannot auto-run.

#### 5. What is deliberately out of scope

* No per-command limits, no priorities, no queue: one fleet-wide pair of
  numbers, overridable only by the operator's settings.
* No output limit for a human-approved run: the human can interrupt it, and
  F5's 16 KiB tail already bounds what is *stored*.
* No `nice`/`cpulimit`/cgroup: a wall-clock bound and a byte cap are what the
  residual risk named; CPU accounting is a different feature.
* No change to the 90 s JIT wait, the 60 s `wait: true` poll or F12's
  `timeout` — those bound the *conversation*, not the command.

#### 6. Tests to add (final)

Gateway sandbox (`tests/test_f19_limits.py`), with the existing harness:

* a `sleep 10` command with `seconds: 5` is stopped, `exit_code` 124, the
  marker in `messages` (MCP) and `[ESHU exit=124]` + marker (direct), and the
  F5 row has `limit: time`;
* a command printing 2 MiB with `bytes: 65536` keeps exactly 65536 bytes,
  `truncated` is true, the marker says `output cut at 65536 bytes`, and the F5
  row has `limit: bytes`;
* a stream that ends exactly at the cap is **not** marked cut (the probe
  byte);
* a human-approved run (no `run_limit`) is unbounded: `sleep 6` finishes and
  no marker appears;
* `run_limit` present but out of range / non-numeric / negative → the call is
  refused and nothing runs;
* `run_limit` on a 200 response with `status: pending` is ignored (no run);
* the F5 capture path is untouched for an unlimited run (regex-allowlist and
  ticket paths).

Dashboard (`tests/test_f19_limits.py`):

* `run_limit` is added to `/api/request` only for `reason = read-lane`
  (pending rows and human-approved rows get none);
* `/api/request_status/{id}` and `/api/ticket/{id}` carry it for an
  unattended approved row;
* defaults are 60/1048576; a corrupt meta value falls back to the defaults;
  the settings endpoint rejects 4 s, 301 s, 65535 bytes and 8 MiB + 1;
* `receive_request` refuses to auto-approve a read-lane row on a gateway
  without `run_limits: v1` (row pending, reason `read-lane-no-limits`, audit
  row written);
* with the capability present, the row is approved as today;
* `/api/exec-result` stores `limit` and rejects an unknown value.

`bash scripts/verify.sh`: `2331 passed, 7 warnings in 430.08s (0:07:10)` (docs-only; this branch is `feature-roadmap` + the note, so F12's new tests are not in it).

### Reviewer notes

_(reviewer)_

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
* **UI design pass, after the features are done, in a fresh chat** (start from `docs/features/UI_INVENTORY.md`). Review the layout as a whole: what lives in the queue, the sidebar, the session modal and Statistics; what to merge or move; consistency across F2/F3/F5/F7/F18 additions.

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
| 1 | F1 | breakdown | dashboard | **Merged** 2026-11-07 | `feat/f1-breakdown` @ `e0df20a` | — | 3 rounds; 155 adversarial cases; 14.7% unknown on live data |
| 2 | F2 | review-card | UI | **Merged** 2026-11-08 | `feat/f2-review-card` @ `286df1b` | F1 | 2 rounds |
| 3 | F7 | inner-target | dashboard | **Merged** 2026-11-08 | `feat/f7-inner-target` @ `0ad167f` | F1 | 1 round; backfill 7.1 s on a live-DB copy |
| 4 | F3 | session-plans | dashboard + MCP | Merged | `feat/f3-session-plans` | F2 | + security reviewer sign-off |
| 5 | F4 | read-lane | dashboard | Merged — shadow only; `on` hard-locked | `feat/f4-read-lane` | F1, F3, Chunk H | reviewer pass + security reviewer sign-off |
| 5a | X1 | ctx-actions | dashboard | Merged | `fix/x1-ctx-actions` | — | FYI to security reviewer (approve ignored status) |
| 6 | F5 | exec-results | gateway + dashboard | Merged | `feat/f5-exec-results` | — | |
| 6a | F18 | request-context | dashboard | Merged | `feat/f18-request-context` | F1, F5, F7, X1 | |
| 7 | F12 | ssh-exec | dashboard + gateway | Ready (after F5) — design first | `feat/f12-ssh-exec` | F4, F5 | design approval + security reviewer sign-off |
| 8 | F6 | session-wrapup | dashboard | Merged | `feat/f6-session-wrapup` | F3, F4, F5 | |
| 9 | F13 | session-conclusions | dashboard + MCP | Merged | `feat/f13-session-conclusions` | F5, F6 | |
| 10 | F8 | test-traffic | dashboard | Merged | `feat/f8-test-traffic` | — | low priority |
| 11 | F9 | ha-supervisor-read | dashboard | Merged | `feat/f9-ha-supervisor-read` | — | token is now a dedicated `eshu` HA admin user |
| 12 | F10 | ha-supervisor-actions | dashboard | **Round 1: changes requested (M1–M2)** | `feat/f10-ha-supervisor-actions` @ `1e14827` | F9, F3, Chunk H | reviewer pass + security reviewer sign-off |
| 13 | F11 | ha-config-files | dashboard | Ready (after F10) — design first | `feat/f11-ha-config-files` | F10, F2, F5, F12 (runner) | design approval + security reviewer sign-off |
| 14 | F14 | inventory | dashboard + MCP | Merged | `feat/f14-inventory` | Chunk H | |
| 15 | F15 | probes | dashboard + MCP | Ready after F12 | `feat/f15-probes` | F4, F12 | |
| 16 | F16 | recipes | dashboard + MCP | Design first (questions listed) | `feat/f16-recipes` | F3, F12, F13 | design approval + security reviewer sign-off |
| 17 | F17 | local-agent | dashboard + docs | Ready after F12–F16 | `feat/f17-local-agent` | F12–F16 | |
| 18 | F19 | unattended-limits | gateway + dashboard | **Design note written** — awaiting review; before F4 `on` is unlocked | `feat/f19-unattended-limits` | F4, F5 | design approval + security reviewer sign-off |
Chunks are done **in the order column**; each new chunk branches from the
updated `feature-roadmap` after the previous one is merged.
