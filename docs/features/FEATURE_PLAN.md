# Feature Plan — Reviewable approvals & less approval fatigue

Status: **active** · Branch: `feature-roadmap` · Baseline: `master@4d13fd8` (security chunks 1–5 + R merged and deployed), **1343 tests passing** (`bash scripts/verify.sh`)

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
- [ ] Reviewer does a dedicated security pass on `cmd_rules.py` and `read_lane.py` (try to find a read-classified command that writes, executes or leaks a secret) and records it under Reviewer notes.
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
| 1 | F1 | breakdown | dashboard | **Changes requested** (round 1) | `feat/f1-breakdown` | — | R1–R11, see chunk |
| 2 | F2 | review-card | UI | Ready (after F1) | `feat/f2-review-card` | F1 | |
| 3 | F7 | inner-target | dashboard | Ready (after F2) | `feat/f7-inner-target` | F1 | |
| 4 | F3 | session-plans | dashboard + MCP | Ready (after F7) | `feat/f3-session-plans` | F2 | |
| 5 | F4 | read-lane | dashboard | Ready (after F3) | `feat/f4-read-lane` | F1, F3 | reviewer security pass |
| 6 | F5 | exec-results | gateway + dashboard | Ready (after F4) — design first | `feat/f5-exec-results` | — | design approval needed |
| 7 | F6 | session-wrapup | dashboard | Ready (after F5) | `feat/f6-session-wrapup` | F3, F4, F5 | |
| 8 | F8 | test-traffic | dashboard | Ready (after F6) | `feat/f8-test-traffic` | — | low priority |

Chunks are done **in the order column**; each new chunk branches from the
updated `feature-roadmap` after the previous one is merged.
