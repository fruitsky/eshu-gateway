# Release tests — what to check by hand, release by release

The automated suite (`bash scripts/verify.sh`) proves the code. This list
proves the **deployed** system with a real agent (Hermes) and a real
operator. Each test has an ID, so a test session can be described as "run
T-PRB-1 to T-PRB-4", and the reviewer can turn any group into a prompt for
Hermes.

**Who:** **H** = Hermes does it, **O** = the operator does it on the
dashboard, **H+O** = Hermes asks and the operator approves or checks.

**Rules for every test session:**
* Hermes starts with `session_begin`, with a goal that begins `TEST:`.
* Commands are harmless (`uptime`, `df -h /`, `true`, `false`, reading a
  non-secret file) unless the test says otherwise.
* At the end:
  1. Hermes calls `session_conclude`.
  2. The operator rejects the draft (unless T-RB is running).
  3. The operator excludes the session from the statistics (T-TT-1).

**Recording results:** keep results **outside the repo** (this repo is
public). Note the request ids and anything odd, and pass failures to the
reviewer. Never paste live IPs, hostnames or output into this file.

**Preconditions:**

| Ref | What | Needed by |
|---|---|---|
| P-1 | Hermes can read the agent manual at `/api/docs/agent-manual` | everything |
| P-2 | `/var/lib/eshu/ssh` exists, `0700`, owned by the service user (done 2026-11-10) | `ssh_exec`, probes |
| P-3 | At least one gateway reinstalled from the current dashboard (the "test gateway") | T-GW, T-SSH, T-EX, T-PRB, T-LIM |
| P-4 | The HA integration has the F10 actions enabled and `ha_host_reboot` disabled | T-HA |
| P-5 | A Pi-hole v6 integration with `local_dns_records` enabled | T-PRB-5, T-INV |
| P-6 | A Pulse integration | T-CJ-3 |

---

## Release 1.0 (2026-11-10) — dashboard

### T-CARD — approval card and breakdown (F1, F2)
1. **H+O.** Hermes runs a multi-step command that needs approval (for
   example `uptime && df -h / && cat /etc/os-release`). **Expect:** the card
   shows the step count with read/write labels, and the risky step is
   highlighted.
2. **H+O.** A command with one write step among reads (for example
   `uptime && touch /tmp/eshu-test && ls /tmp/eshu-test`). **Expect:** the
   write step is the one highlighted.

### T-CTX — sidebar context (F18)
1. **O.** Open a pending request's sidebar. **Expect:** this session's
   earlier commands, "seen before" counts with past decisions, the host's
   state, and why it is waiting.

### T-X1 — pending-only decisions (X1)
1. **O.** Deny a request, then press Approve on it. **Expect:** refused, as
   it is no longer pending.
2. **O.** Approve and deny from the sidebar as well as the queue. **Expect:**
   both work for SSH requests and integration calls.

### T-INNER — inner target (F7)
1. **H+O.** `pct exec <vmid> -- uptime` (or `docker exec <name> uptime`).
   **Expect:** the request records the container or guest as the real
   target.
2. **O.** With a host-wide override on a Proxmox node, open one of its
   container requests. **Expect:** the warning that the override also covers
   its containers.

### T-PLAN — session plans (F3)
1. **H+O.** Hermes proposes `session_plan` with two harmless items
   (`max_uses` 1 each). **Expect:** one card with both items.
2. **H+O.** Approve the plan; Hermes runs both over normal SSH. **Expect:**
   no further prompts. A third run of either command asks again.
3. **H+O.** Deny a second plan. **Expect:** Hermes's `check_plan` says
   denied, and the commands ask normally.
4. **H.** For an agent in SSH mode `approval`, the same plan over
   `ssh_exec`. **Expect:** it does **not** cover the commands; each asks.
   This is by design.

### T-LANE — read lane in shadow mode (F4)
1. **O.** Set the test gateway's read lane to `shadow`.
2. **H+O.** Hermes runs a few plain reads. **Expect:** each is still asked,
   and is tagged "would have run without asking". Nothing auto-runs.

### T-EX — exit codes and output (F5) — needs P-3
1. **H+O.** On the test gateway: `true`, then `false`. **Expect:** the
   requests show exit 0, then exit 1.
2. **O (optional).** Set capture to `tail`; Hermes runs `journalctl -n
   20000`. **Expect:** the end of the output appears in the sidebar, and the
   call returns promptly. Set capture back afterwards.

### T-SSH — SSH through Eshu (F12) — needs P-2, P-3
1. **H.** `ssh_exec` `uptime` on a gateway that has **not** been updated.
   **Expect:** refused with `ssh_not_ready` or `ssh_host_unpinned`.
2. **H+O.** `ssh_exec` `uptime` on the test gateway. **Expect:** `pending`
   → approve → `check_request` says `consumed` → the same call again returns
   `ran` with the output. The audit log shows Hermes's own name.
3. **H.** `ssh_exec` `false`. **Expect:** `exit_code` 1.
4. **H+O.** `ssh_exec` `df -h` with `wait=true`, approved within 60 s.
   **Expect:** the result in one call.
5. **H.** `ssh_exec` with `host` set to `user@<ip>` or an `-o` option.
   **Expect:** refused before anything is sent.

### T-WRAP — session wrap-up (F6)
1. **O.** Open a finished test session. **Expect:**
   * the goal;
   * what ran, changed and failed;
   * how much ran under overrides.

### T-CONC — conclusions and runbooks, agent side (F13)
1. **H.** `similar_incidents` and `find_runbooks` for a made-up problem.
   **Expect:** an empty or relevant answer, with no error.
2. **H.** `session_conclude` with evidence refs from this session.
   **Expect:** the draft shows in the session view for the operator.
3. **H.** `session_conclude` citing a request from **another** session.
   **Expect:** refused.

### T-TT — test traffic (F8)
1. **O.** Exclude the test session from the statistics. **Expect:** its
   requests leave the statistics, but stay in History.

### T-HAR — HA reads (F9)
1. **H.** Supervisor info, the add-on list, the backup list and
   `ha_resolution`. **Expect:** answers, with no approval asked.
2. **O.** Freeze, then Hermes calls any HA write. **Expect:** refused as
   frozen.

### T-INV — inventory (F14)
1. **H.** `whereis` for a known service, a known IP and a vmid. **Expect:**
   * the right host, IPs and sources;
   * a `stale` flag only where a source is stale.
2. **O.** The Inventory page lists the same entities.

## Release 1.1 (2026-11-10) — HA actions (F10) — needs P-4

### T-HA
1. **H.** `ha_check_config`. **Expect:** `valid` (or the real errors), with
   no approval asked.
2. **H+O.** `ha_backup_create`, partial, `partial_folders: ["ssl"]`, named
   `eshu-test`. **Expect:** the card shows the name and folders; after
   approval, the backup exists in HA. Delete it in HA afterwards.
3. **H.** `ha_core_restart` with **no** check in the last 10 minutes.
   **Expect:** refused before any card.
4. **H.** `ha_host_reboot`. **Expect:** it is not in the tool list at all
   (disabled tools are withheld, not refused).
5. **H+O (optional, at a quiet time).** `ha_addon_action` restart of a
   harmless add-on. **Expect:** the card names the add-on, and it restarts
   after approval.

## Release 1.2 (2026-11-11) — limits, probes, runbooks page, change journal

### T-RB — Runbooks page (X2)
1. **H+O.** Hermes files `session_conclude` with `outcome: solved` and
   `runbook_candidate: true`. The operator confirms it in the session view,
   then presses **Promote to runbook**.
2. **O.** Open **Runbooks** in the navigation (next to Inventory). **Expect:**
   the new runbook is listed. Search and the tag filter find it, and opening
   it shows the ordered steps with their breakdown.
3. **H.** `find_runbooks` with a word from its title. **Expect:** found.
   `get_runbook(id)` returns the steps.
4. **O.** Retire it. **Expect:** it is greyed under "show retired", and an
   audit row is written.
5. **H.** `find_runbooks` and `get_runbook(id)` again. **Expect:** not found.
6. **O.** Restore it. **H:** found again.

### T-CJ — change journal (F23)
1. **O.** On the gateways table, press **Changes** on the test gateway.
   **Expect:**
   * a timeline of the last 7 days;
   * the SSH commands that **changed** something (not plain reads), with
     their exit codes;
   * integration writes on that host.
2. **O.** Switch the range (1–30 days). **Expect:** the list changes
   accordingly.
3. **O (needs P-6).** On a host with a recent Pulse alert. **Expect:** the
   alert appears. If it started within 30 minutes after a change, it shows
   "+N min after #id"; this is a hint, not a cause.
4. **O.** A host named like another (for example `pve` and `pve3`).
   **Expect:** each shows only its own items.

### T-PRB — health-check probes (F15) — needs P-2, P-3
1. **H+O.** `diagnose_service` on the test gateway for a real unit (for
   example `ssh` or `cron`). **Expect:** one card marked as a probe, listing
   its three commands. `probe_result` before approval says `pending`; after
   approval it returns:
   * the state;
   * de-duplicated warnings and errors;
   * the listening ports.
2. **H.** `probe_result` again for the same plan. **Expect:** the same
   digest, and nothing re-runs (no new requests).
3. **H+O.** `disk_pressure`, then `diagnose_container` (a vmid on a Proxmox
   node, or a Docker name). **Expect:**
   * filesystems sorted by use;
   * for a container, its status and restart count, with no environment
     variables anywhere.
4. **H.** `diagnose_service` with a unit like `-x` or `a;b`, and with a host
   that is not a gateway. **Expect:** refused, with no card.
5. **H (needs P-5).** `dns_check` for a name in Pi-hole's local DNS.
   **Expect:** the record at once, with no card.
6. **H+O.** Deny a probe's card. **Expect:** `probe_result` says `refused`.

### T-LIM — limits for unattended runs (F19)
Limits apply only to runs with nobody watching; since 1.3 that is a plan
check (T-CHK).
1. **O, after P-3.** The test gateway's details (or the reviewer, read-only)
   show it reports the `run_limits: v1` capability. On a gateway whose
   `timeout` is not GNU (BusyBox, or Ubuntu 26's uutils), it does **not**
   report it, and a check there is `not_run` (T-CHK step 4).

## Release 1.3 (2026-11-14) — lockbox, plan checks, agent/UI round

1.3 changes **how an approval reaches a gateway** (the lockbox) and adds a
**check that runs after a plan item**. Both need P-3. The rest is the
dashboard/UI round.

### T-LBX — one approval, one run (lockbox)
1. **H+O.** On the test gateway, `ssh_exec` `uptime`. **Expect:** `pending`,
   a card naming the gateway; approve it.
2. **H.** `check_request(request_id, session_id="<yours>")` again.
   **Expect:** `approved`, then `consumed` (the poller deposited the ticket),
   and only then the result. Never `ran` while the ticket is still in
   flight, and never a second run from the same approval.
3. **H.** Re-run the same `ssh_exec` call after it ran. **Expect:** a **new**
   request id with a new card; the old approval is not reused.
4. **O (late approval).** Leave a card unapproved past its TTL (90 s).
   **Expect:** the row ends `expired`; pressing Approve then is refused
   (`not_pending`); nothing ran. Hermes's next call is a new card.
5. **O.** Press Approve twice on one card. **Expect:** one run; the second
   press is refused, never a second ticket.

### T-ONE — a command is one line
1. **H.** `ssh_exec` (or a plan item) whose command contains a newline.
   **Expect:** refused before a card exists, with `multiline_command` and the
   advice to join the steps with `;` (not a newline, not a base64 pipe).
2. **O.** The queue has no card for it, and History has no `pending` row.
3. **H.** The same command with `;` instead of the newline. **Expect:** it is
   taken normally (a card, or the policy verdict) - the rule is the line
   break, not the step count.

### T-PLAN-V — the plan card is where the pending items are
1. **O.** With no pending JIT requests, have Hermes propose a two-item plan.
   **Expect:** the plan card is visible in the queue area immediately, not
   behind the starfield or below the fold.
2. **O.** Add a pending JIT request, then propose another plan. **Expect:**
   both are visible at once in the same area; neither hides the other.
3. **H.** Propose a second plan that replaces the first, then
   `check_plan(<old id>)`. **Expect:** `superseded` with the new plan id, not
   `not_found`; the old card is gone from the queue.

### T-PRB3 — probes end to end (re-run of T-PRB on 1.3) — needs P-2, P-3, P-5
1. **H+O.** `diagnose_service` for a real unit on the test gateway.
   **Expect:** one card marked as a probe, its three commands listed;
   `probe_result` says `pending` before approval, then returns the state, the
   de-duplicated warnings/errors and the listening ports.
2. **H+O.** `diagnose_container` with a numeric vmid. **Expect:** the guest's
   status and restart count, no environment variables; the card names the
   vmid.
3. **H+O.** `diagnose_container` with a Docker name. **Expect:** the same
   shape for the container.
4. **H+O.** `disk_pressure`. **Expect:** filesystems ordered by use, the
   fullest first.
5. **H.** `probe_result` again for the plan in step 1. **Expect:** the same
   digest, and **no new requests** in History for that plan (nothing re-ran).
6. **H (needs P-5).** `dns_check` for a name in Pi-hole's local DNS.
   **Expect:** the record at once, with no card.

### T-X6 — the agent/API fixes from the test round
1. **H.** `check_request(<id>)` from a transport with no bound session.
   **Expect:** `session_id_required` naming the id, not `not_found`; with the
   right `session_id` the call works.
2. **H+O.** Confirm a conclusion, then have Hermes call `session_conclude`
   for the same session again. **Expect:** a **new** draft for the operator;
   the confirmed one stays confirmed and visible; no `already_confirmed`
   refusal.
3. **H+O.** `session_conclude` with a `title`. **Expect:** the title is shown
   on the conclusion, and a runbook promoted from it uses the title instead
   of the cut summary.
4. **H (needs P-4).** `ha_addons` and `ha_backups`. **Expect:** the real
   lists, not `{}`, matching what the HA UI shows.
5. **H+O.** `pct exec <vmid> -- uptime` (or a Docker exec). **Expect:** the
   card shows the inner target chip, and `check_request` returns
   `inner_target` for the row; History shows the same target after it runs.

### T-X4 — the Agents page
1. **O.** **Agents** is in the navigation (next to Inventory and Runbooks).
   Open an agent. **Expect:** the token (Revoke/Delete), Tools, Rules, SSH,
   Advanced, Web, and the Activity list, all on the page; no policy modal.
2. **O.** Open the SSH tab. **Expect:** one checkbox per gateway. Tick
   **any gateway**: the confirmation says it also covers every gateway
   enrolled later, and cancelling leaves the box unchecked.
3. **O.** With a stored hostname entry (an older row, or set through the
   API), reopen the SSH tab. **Expect:** the entry is **not** ticked, but a
   line says hostname entries are not matched, with `name -> ip` and a
   **Convert hostnames to IPs** button. Convert, press **Save SSH**, and
   reopen: the IP is ticked and the line is gone.
4. **O.** Save Tools, then Save SSH. **Expect:** the tools stay saved and the
   SSH hosts stay as set (each Save sends one section; neither resets the
   other). Repeat the other way round, and with Web.

### T-POL — the page is called Command policy
1. **O.** The navigation item and the page title say **Command policy**, not
   Controls; the `#controls` deep link still opens the page.
2. **O.** The page holds the exact and regex allowlists, the blocklist and the
   policy tester; the web-fetch widget is gone, with a one-line pointer to
   the agent's Web tab on Agents.

### T-CHK — a change carries its check (F22) — needs P-3
1. **H+O.** Have Hermes propose a plan whose item is `true` and whose check
   is the ssh check `true` (expect exit 0). **Expect:** the plan card shows
   the check under the command, with its wait/retries, **before** approval.
2. **O.** Approve. **Expect:** the item runs once with no second card; after
   exit 0 the check runs with no card and the item line reads
   `check: true - passed`; the plan continues.
3. **H.** Propose an item `true` with the check `false`. **Expect:** the item
   runs, the check `failed`, the plan **stops**, and `session_plan` carries
   the state and the reason.
4. **H+O (not-yet-updated gateway).** Repeat step 2 on a gateway still on the
   old script (no `run_limits: v1`). **Expect:** the item runs, the check
   reads `not run` with `check_no_limits`, and the plan stops. No check
   command reaches the gateway.

## Gateway update (every release that changes the gateway script)

### T-GW — on the test gateway, right after reinstalling it
1. **H+O.**
   * An allowlisted command runs without asking.
   * A JIT command asks, and runs after approval.
   * A blocked command is refused.
2. **O.** Freeze the fleet (the freeze is fleet-wide, not per gateway).
   **Expect:** every command is refused. Unfreeze it.
3. **O.** The Gateways table shows the new version, and heartbeats arrive.

---

## Fleet rollout checklist (gateway updates, one at a time)

Do one gateway per update. Old and new scripts run side by side, and a
half-updated fleet is expected while this runs.

### Before the update, on that gateway
* `curl --version` is **7.55 or newer** (the installer refuses older: the
  token header is read from stdin, which old curl accepts and drops).
* `command -v flock` finds util-linux `flock`. Without it the poller refuses
  lockbox writes and the gateway refuses the by-id claim, so approvals never
  run: fix that first.
* `timeout --version` says GNU coreutils. Without GNU `timeout` the
  unattended limit (a plan check, a future read lane) is refused, not
  half-applied; only those paths are affected.
* `/etc/eshu-token` is present, and `/etc/eshu-session-key` plus
  `.prev` exist. Record `sha256sum` of all of them; the update must keep
  them byte-identical.
* The Gateways table shows a recent heartbeat, and no request is pending on
  that gateway.

### After the update, on that gateway
* `grep -c 'eshu.tickets.lock' /usr/local/bin/eshu-gateway.sh /usr/local/bin/eshu-poller.sh`
  returns at least 1 for each (the lock path is in the scripts; the file is
  created at runtime under `/run/eshu`).
* `grep -n -- '-H @-' /usr/local/bin/eshu-gateway.sh /usr/local/bin/eshu-poller.sh`
  shows the token headers on stdin (and `@<(gateway_header)` for a POST
  body), never a named token file on disk.
* The keys recorded before are unchanged (compare `sha256sum` output), and
  `/etc/eshu-token` is still `0600 root:root`.
* Polls arrive: the Gateways table's last-seen moves within a minute, and
  `journalctl -t eshu-poller -n 30` shows the tick. `systemctl status
  eshu-poller` is `active`, and `journalctl -t eshu-gateway -n 30` has no
  `LOCKED`/`FATAL` line.
* Run **T-GW** on that gateway: an allowlisted command runs, a JIT asks and
  runs after approval, a blocked one is refused.
* Only after all of that, mark the gateway updated in the rollout table.

### If one gateway fails
* Stop. Do not re-run the installer in a loop, do not hand-edit the
  installed scripts, and do not copy a script from another gateway.
* Stop approvals to that gateway: denylist its pending cards, and use its
  **Zero-Trust** control (the fleet freeze is fleet-wide, not a per-gateway
  substitute).
* Capture: `journalctl -t eshu-gateway -t eshu-poller -n 100`, the installer
  output, `bash -n` on both installed scripts, `curl --version`,
  `command -v flock timeout`, and `ls -l /etc/eshu-token
  /etc/eshu-session-key*`.
* If the old script is still installed, the gateway keeps using the old
  path. The dashboard cannot tell the two scripts apart, so leave it out of
  the rollout table and say so.
* Report the installer output and the journal to the reviewer (outside the
  repo; no live IPs, hostnames or output in this file).

## Results

Keep results outside the repo. A useful shape is:
`<date> <release> <test id> pass|fail <request ids> <note>`.
