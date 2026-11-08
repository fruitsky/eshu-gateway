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
| 1 | F1 | breakdown | dashboard | **Merged** 2026-11-07 | `feat/f1-breakdown` @ `e0df20a` | — | 3 rounds; 155 adversarial cases; 14.7% unknown on live data |
| 2 | F2 | review-card | UI | **Merged** 2026-11-08 | `feat/f2-review-card` @ `286df1b` | F1 | 2 rounds |
| 3 | F7 | inner-target | dashboard | **Merged** 2026-11-08 | `feat/f7-inner-target` @ `0ad167f` | F1 | 1 round; backfill 7.1 s on a live-DB copy |
| 4 | F3 | session-plans | dashboard + MCP | **Ready** | `feat/f3-session-plans` | F2 | + security reviewer sign-off |
| 5 | F4 | read-lane | dashboard | Ready (after F3) | `feat/f4-read-lane` | F1, F3, Chunk H | reviewer pass + security reviewer sign-off |
| 6 | F5 | exec-results | gateway + dashboard | **Design approved with changes (D1–D8); implementing** | `feat/f5-exec-results` | — | |
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
