# Changelog

All notable changes to Eshu Gateway are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed
- **Editable Core Blocklist — shipped safety-net patterns are now relaxable.** The gateway's hardcoded Stage-1 `case` no longer contains the command-safety patterns (`rm -rf`, `mkfs`, `dd`, `iptables/ip6tables/nft` flush, power control). They now live in the blocklist, **seeded by default** (one-time, idempotent migration) and 🛡️-flagged in the UI. They are **relax-only**: removing one requires a loud danger confirm, and `↺ Core defaults` re-adds them (audited via the normal save/commit path + policy-change history). **Non-editable, permanently hardcoded:** self-protection (`/usr/local/bin/eshu-*`, `/etc/eshu-*`, `/var/run/eshu.`, `eshu.db*`) and `$(which`/`` `which `` evasion. `POST /api/policies/restore-core` re-adds missing core patterns; `/api/policies` returns `core_patterns`/`hard_patterns` for the UI. Dashboard blocklist matching was **aligned to the gateway's substring semantics** (strip `^`/`$`, `#` comment lines skipped) across the tester, `/api/policies/check`, and fleet dispatch — previously the tester treated the blocklist as regex and could report "jit" for a command the gateway hard-blocks. Gateway script change ships via Build → Edge → Fleet. Tests: substring parity, seeding idempotency, registry exposure, restore-core, self-protection/evasion stay FATAL.
- **UI design-system refresh (Option A) — one styling system, white primary buttons.** The Tailwind CDN runtime dependency is gone (every utility used is now a local class, verified programmatically). `:root` gained design tokens (spacing/typography/radius, `--primary`/`--on-primary`/`--primary-hover`, `--danger`, red-tinted `--focus-ring`, `--bg-stripe`). Buttons re-unified: **white** primary (`.btn`), **green** approve (`.btn-approve`), **outline-red** danger (`.btn-danger`), muted/deny/dev variants unchanged; focus rings and input focus are brand-red. ~410 inline `style=` strings reduced to ~50 (only genuinely dynamic ones remain); body text floor lifted to 12px+; widgets/panels standardised. Every view converted (Dashboard, Gateways, Windows, Statistics, Controls, Fleet Run, Settings, Logs, all modals, login/setup, banners). Fixed latent bugs: search inputs referenced undefined `--bg-card`/`--text-primary`; the toast used dead Tailwind colour classes after CDN removal (now `.toast-success/.error/.info`).
- **Policy lists are now chip editors.** Exact Allowlist / Regex Allowlist / Blocklist render as editable rows (monospace, truncating, `×` remove, `+ Add` input, vertical one-per-row, zebra-striped), backed by hidden textareas so the backend contract is unchanged. The tester is context-aware: a blocked command shows `＋ Blocklist`; otherwise `＋ Exact` / `＋ Regex` (Regex auto-escapes to `^cmd$`). Duplicate entries are deduped with a toast. History-table action pills (Fleet Frozen / Fleet Run / Block by Core) now match the Actions dropdown width and height.
- **Deny-based blocklist nudge + blocklist suggestions.** `POST /api/deny/:id` now returns `deny_count` + `command`; the "Add to blocklist?" bar appears only on the **10th** denial of a command (silent otherwise, no counter shown). The learning scanner (Policy Suggestions in Statistics) now also surfaces commands **denied ≥20 times** as blocklist suggestions (`＋ Blocklist` + Dismiss), alongside the existing repeated-approval allowlist suggestions. *Note: the 10-deny prompt counts only denials accumulated from this version onward — a command already denied ≥10 times before upgrading won't prompt (accepted as-is).*
- **Copy fix:** "Gateways sync every 3s." → "every 30s." (the policy poller runs on a 30s interval).
- **Emergency Freeze moved to a sidebar toggle; Controls is now full-width policy.** The Freeze widget (which sat awkwardly alone in Controls' right column after the Deployment widget moved to Settings) is removed. Emergency Freeze now lives as a **discreet pill in the sidebar** above Gateway Health — visible on every page, turns red + "Frozen — tap to unfreeze" when active, and stays live-synced by the existing 10s poll. Controls is now a single full-width Global Central Policy editor. The pulsing top banner remains; its Unfreeze button now acts directly instead of navigating to Controls.
- **Docs overhaul + two read endpoints gated.** Full technical/architecture documentation refresh: `docs/API.md` now documents **every** endpoint with an explicit Auth column (Public / Gateway / Session / Token-in-path); `docs/AGENT_MANUAL.md` corrected the `/api/policies/test` response shape (`action`/`tier` instead of the moved-to-`/api/policies/check` membership booleans) and the window quick-reference (`retrieval_key`); `docs/ARCHITECTURE.md` gained **Stage 0 — Emergency Freeze** in the policy pipeline and the dev-tools-toggle note; `docs/DEV_GUIDE.md` refreshed test counts (289 / 22 files) and the file structure. For security, `GET /api/feature-flags` and `GET /api/policies/check` (which reveal flag/policy state) are now **session-gated** — previously open read. Regression tests added.
- **Webhook message formatting is now destination-aware.** Discord renders the 🔗 dashboard link with Markdown (`[Open dashboard](url)`) and bold with `**title**`; Slack / Mattermost / Discord `/slack` keep the Slack link (`<url|text>`) and `*title*`. Previously the link always used Slack's `<url|text>` syntax, so on Discord it showed up as literal text instead of a clickable link (and the title rendered as italic instead of bold). Verified live against Discord. Regression tests added.
- **Webhook notifier sends a real User-Agent (Discord was silently rejecting it).** The notifier's `urllib` POST sent no User-Agent, so Discord rejected every message with **HTTP 403** (it blocks the default `Python-urllib/3.x` UA) — the JIT/window/offline notifications silently never arrived, and the failure was invisible because `_do_webhook` swallowed exceptions. `_do_webhook` now sends `User-Agent: EshuGateway/<version>` (verified 204), and **logs failures** (status code or exception) instead of swallowing them, so a future webhook problem shows up in the dashboard logs. Regression test added.
- **Discord webhooks now work out of the box.** Eshu sent Slack's `{"text": ...}` body, which Discord's **native** webhook endpoint rejects with "Cannot send an empty message" (400, code 50006) — so plain Discord URLs silently failed unless you knew to append `/slack`. The notifier now auto-detects the destination: Discord native URLs get `{"content": ...}`, everything else (Slack, Mattermost, Discord `/slack`) gets `{"text": ...}`. The "add /slack" workaround is no longer needed. Regression tests added.
- **Dev Tools are now a Settings toggle (hidden by default).** The **Development & Deployment** widget (Build → Edge → Fleet pipeline, Seed/Push/Promote/Rollback, Dev Gateways, Feature Flags) moved from the Controls page into **Settings**, hidden until you enable **"Show development tools"** (a server-side `dev_tools_enabled` setting, `GET/PUT /api/settings/dev-tools`). When off, the widget is hidden and the dev fetches are skipped — a clean default for non-dev users. Controls keeps Emergency Freeze + Global Central Policy. (Also fixed: the widget's data now loads on the Settings view — Build/Edge/Fleet labels and Feature Flags populate correctly — and an empty flags table shows "No feature flags configured." instead of a stuck "Loading…".)
- **External Notifications fixed + dashboard link.** The webhook notifier had two dead paths: the **Test button never actually sent** (`send_notify('test')` was silently filtered out by the events list — so the "sent" toast was a lie), and **offline/online** events could never be enabled (no UI checkbox, excluded from the default events). Fixes: the `test` event now **always sends** (bypasses the filter); **offline/online** checkboxes added to the widget; `send_notify`/`_do_webhook` now return whether delivery succeeded, so the Test button reports the real result; and a **Dashboard URL** field lets notifications include a `🔗 Open dashboard` link back to the approval page. (Approving *from* Slack/Discord isn't possible with plain webhooks — that would need a Slack app + public reachability; noted as deferred.)
- **Dashboard password is now mandatory and non-removable.** Previously the dashboard auto-generated a password on first startup and offered a "Remove Password Protection" action (`POST /api/auth/clear-password`, `set_password.py --reset`, and a Settings button) that cleared it — after which **every** session-protected endpoint (`_check_session`) silently granted access. Now: the operator must set a password on **first launch** (setup overlay or `set_password.py`); protected endpoints **fail closed** (401) when no password is set; and the clear-password endpoint, UI button, and `--reset` CLI flag are all **removed**. The password can still be *changed* via Settings → Dashboard Password or `set_password.py`. README + API docs updated.
- **Version redesign: one product version, no deploy counter.** The dashboard previously carried several disparate version numbers: a static `DASHBOARD_VERSION` constant (`v15.4`), a `fleet_version` that auto-incremented on every Deploy to Fleet (`v15.11`), a `golden_version`, and a separate gateway-runtime `GATEWAY_VERSION` — several of which disagreed. These are now unified: **`DASHBOARD_VERSION = "v0.1.0"`** is the *only* product version, reported consistently by the dashboard, `/api/version`, and every gateway. The `fleet_version` and `golden_version` meta keys and their plumbing are **removed** — the Build → Edge → Fleet pipeline is now driven purely by **file hashes + the update trigger** (`deployed_golden_hash`, `trigger_update_version`), so Deploy to Fleet no longer bumps any version. `bump_fleet_version()` and the version-based dev-gateway staleness check are gone. `/api/dev/status` and `/api/policies/rollback-status` now return `dashboard_version` (the product version) + hashes. Requires a fleet re-deploy to re-bake the new `GATEWAY_VERSION` into gateways.

### Fixed
- **Gateway re-registration flood returns after uninstall/reinstall (stale `'None'` token).** The `api_token` column can hold the **literal string `'None'`** (from the v15.0 `DEFAULT None` schema bug — SQLite stores the string, and the current `ALTER … DEFAULT NULL` is a silent no-op on an existing column). When a gateway was uninstalled (row deleted) and reinstalled, `register_gateway` INSERTed a new row **omitting `api_token`**, so SQLite filled it with the buggy default `'None'`; `/api/register` then treated that truthy string as a real token and returned `'None'`, the installer (correctly) rejected it, the poller got `GATEWAY_TOKEN=""`, and the self-heal re-registered every ~30s — the flood again. Fixes: (1) `/api/register` now treats `'None'`/empty as "no token" and mints a fresh one; (2) the fresh-gateway INSERT sets `api_token NULL` explicitly so the buggy column default can never apply. Existing rows with `'None'` are also repaired by the startup cleanup. Regression tests added. Server-side only — a dashboard restart lets affected gateways self-heal on their next poll (no direct gateway action, no fleet deploy needed).
- **`/api/policies/test` surfaces the FATAL tier.** The gateway's hardcoded catastrophic blocklist (Stage 1, e.g. `reboot`, `rm -rf`, `mkfs`) was invisible to the pre-flight endpoint — `test_policy("reboot")` returned `action: "jit"` when the gateway would hard-block it. `test_policy` now runs the server-side `hard_block_match()` first and returns `action: "blocked"`, `tier: "fatal"`, with the matched pattern.
- **Approved Windows: opaque retrieval keys replace enumerable numeric IDs.** Window/request IDs were sequential `AUTOINCREMENT` integers and the no-auth `GET /api/approved-windows/{id}` / `GET /api/window-requests/{id}` returned the live token — so any LAN client could enumerate `1..N` and pull tokens (Medium risk: using a token still requires the eshu-gateway SSH key, but it's an IDOR smell). Windows now carry an opaque `retrieval_key`; agents use it for the no-auth poll/single-window reads, and the numeric id is only honoured for session-authed callers. `POST /api/window-requests` returns both `id` and `retrieval_key`. The windows table also gained an **ID column** so the operator can refer to a window (e.g. "I edited window #17") when coordinating with the agent.
- **Approved Windows: recurring window tolerance documented.** `AGENT_MANUAL.md` now spells out the exact (inclusive) `[execution_time − 2, execution_time + 2]` minute window and notes it's currently fixed (configurable duration is a planned enhancement).

### Removed
- **Approver SSH key removed.** The second enrollment key — an operator "approver" key installed with **full root SSH access** to `/root/.ssh/authorized_keys` (bypassing the gateway entirely) — was never used by any runtime code path (JIT approval, Approved Windows, and Fleet Run all authenticate via the dashboard session or the gateway API token, and the backend has no SSH client at all). It was pure attack surface: a standing root key on every gateway that the product never exercised, and it also **survived uninstall** (the uninstaller only stripped lines matching `eshu-gateway`). Now enrollment uses **one SSH key** (the Eshu Gateway key); the approver key field, its API/schema, the installer's `/root/.ssh/authorized_keys` write, and the UI are all removed. README Quick Start updated to single-key enrollment. **Migration (complete):** the v15.11 fleet update stripped the leftover approver key from every gateway (all 13 on v15.11), and the transitional installer hook + dashboard injection were removed in a follow-up — the codebase now has **zero references** to the key.

### Added
- **Emergency Freeze.** One button that makes **every gateway reject all commands** — including whitelisted and approved-window commands — until unfrozen. A global circuit breaker for the whole fleet. Backend stores a global `trigger_freeze` meta key (`set_trigger_freeze` / `clear_trigger_freeze` / `get_trigger_freeze`), exposed via `POST /api/freeze`, `POST /api/unfreeze`, and `GET /api/freeze/status` (all session-protected and audit-logged as `freeze_started` / `freeze_ended`). The policy payload now carries `trigger_freeze` next to `trigger_uninstall`; the poller writes it atomically to `/etc/eshu-freeze` (`1`/empty) each cycle; the gateway script runs a new Stage 0 — before the blocklist — that rejects any command with a clear "fleet is frozen" message (logged as `frozen`). UI: new **Controls** tab with a red Freeze / Unfreeze control, a persistent pulsing banner modeled on the Override banner, and a status label. Freeze takes effect within 30s (next poll cycle).
- **Fleet Run (Ansible-lite).** Queue one or more commands in the dedicated **Fleet Run** tab, then hit **Dispatch** — one click sends everything to the selected gateways (that single click is the approval; no separate approval section), with per-gateway results printed to the dashboard. **v1 is user-only** (operator; agent-origin submissions deferred — the AI already has approved windows). Data model: `fleet_commands` (status approved/complete, origin, optional reason, timeout) + `fleet_results` (per-gateway queued/running/success/failed/timeout, exit code, exact `started_at`/`finished_at` run times, up-to-1MB output, `UNIQUE(cmd_id, gateway_ip)`). Endpoints: `POST /api/fleet/commands` (session only — validates and **dispatches immediately**), `GET /api/fleet/commands`, `POST .../{id}/result` (gateway-token, validated against the result's gateway). Safety at dispatch: **hardcoded catastrophic blocklist → hard-reject**, regex-blacklist match → **warn with override**, **refused while the fleet is frozen (409)**. Delivery: `fetch_policy` injects `pending_fleet_cmd(+id)` only into selected gateways with no terminal result; the poller downloads `fleet-runner.sh` (`bash -n` validated), launches it detached via `systemd-run --unit=eshu-fleet-<id>` (persisting the id only on a successful launch so crashes can't double-run), and the runner posts `running` → executes `timeout <t> bash <cmd>` as root → posts the result (output captured from a file and truncated to 1MB). The runner also refuses to run if `/etc/eshu-freeze` = `1`, keeping freeze absolute even for commands already dispatched. UI: compose box (command, timeout, optional reason, target checkboxes) → slim **Dispatch queue** with red ✕ per item → full-width results with output printed inline and exact run times. **Commands execute one at a time per gateway in dispatch order** (the poller only receives the next command after the previous one finishes on that host; combine steps with `&&` in a single entry to run them together) — different gateways run independently. Fleet runs also appear in the main Dashboard history as a **⚡ Fleet Run** status row per target gateway. Output is capped at **1MB** per result with a visible `… [output truncated to 1MB]` marker; the results list ships only a **2KB preview** of each output and the full log is **fetched on demand** when you expand it (keeps the 10s poll light) — each gateway result shows its **hostname** next to the IP and has a collapsible output box with a **copy button**. The command header shows a **live timeout countdown** (`⏳ M:SS left`) while running, and the runner hardens the timeout with `--kill-after` (SIGTERM → SIGKILL 5s later, so a signal-ignoring process can't hang; 124/137 map to `timeout`). Results list rendering is **scroll-stable** (open output boxes and scroll positions survive refreshes). Completed fleet results are **auto-purged after 7 days** (hourly background sweep; in-flight commands are never touched; the audit log keeps the permanent record).
- **Agent window self-service.** Window **read** endpoints are now open to agents (no auth): `GET /api/approved-windows` (full status list; the `token` field is omitted for unauthenticated callers), `GET /api/approved-windows/{id}` and `GET /api/window-requests/{id}` (full record / status + token by ID — a request and its approved window share the same ID). The agent can submit a window request, the operator approves it, and the agent polls the same ID to retrieve its token and inspect the window — for both recurring and single-use windows. Creating/editing/toggling windows and approving/denying requests remain operator-only.
- **Fleet Run clear/skip + queued accuracy.** A stuck fleet result (e.g. dispatched to a gateway still on an old poller that never runs it) can now be **cleared per gateway** with a ✕ on that result row — `DELETE /api/fleet/commands/{id}/result/{ip}` (audit-logged `fleet_result_cleared`) marks only that gateway's `queued` result as `skipped`, keeping the command and the other gateways' successful results; once the remaining results are terminal the command flips to `complete`, and the cleared gateway is never re-injected. Results that are `queued` (waiting for a poll) now show `[queued — waiting for poll]` instead of a misleading `→ running`, and cleared ones show `[skipped — cleared]`. Note: fleet-running a gateway on an old poller stays `queued` until that gateway is updated to the fleet-capable poller (normal JIT/SSH commands are unaffected).
- **Risk Hint ("What could go wrong").** The dashboard now shows a one-line amber ⚠ risk hint for potentially destructive commands — in the main queue next to any **pending** JIT command (tooltip on hover), and in the Fleet Run dispatch queue when you add a command. Powered by a static, word-boundary-aware rule map (`core/cmd_risk.py`): service restarts/stops, `docker rm/rmi`, log vacuum/truncate, package installs, `rm`, `kill -9`, `chmod 777`, `userdel`, recursive `find -delete`. When a known-safe **dry-run** variant exists (`apt-get install` → `apt-get --dry-run install`, `npm install`, `pip install`), the Fleet Run queue shows a 💡 suggestion with a **Use** button to swap the queued command to the dry-run version (which then carries no risk hint), and the Dispatch confirmation warns when queued commands have a dry-run upgrade available so you can Cancel to test first. Risk is computed server-side (`/api/requests` adds `risk` for pending rows; `/api/policies/test` returns `risk` + `dry_run`).
- **Controls UI consolidation.** The dashboard's operational, preference, and record-keeping features are now cleanly separated. **Controls** groups the operational levers — 🚨 Emergency Freeze (existing) · ⚡ **Override Mode** (moved off the gateway cards: a per-gateway list with enable/Cancel and live countdowns; cards keep a read-only `Override MM:SS` badge, and the top banner's button now routes to Controls) · 🚀 **Development & Deployment** (moved from the old Admin view: Seed Edge / Push to Dev / Deploy to Fleet / Rollback, dev gateway search, and feature flags, untouched). **Settings** (renamed from Admin) keeps only preferences — Notifications · External Notifications (webhook) · Dashboard Password. **Logs** (new) holds the Audit Log + search. The sidebar now groups primary views under **Navigation** and Controls · Fleet Run under **Operations**, with Settings · Logs · Log Out at the bottom. Front-end only — no API changes.
- **UI consolidation, round 2.** **Gateways** is now the single home for all gateway data points: Active Gateway cards (health + heartbeat) with the **full Override controls restored** (Override button / countdown + Cancel), plus **Gateway Enrollment** merged in (SSH keys, one-liner, enrollment history) — the separate Enrollment tab is gone. **Controls** gains the **Global Central Policy** editor (Tester + exact/regex allowlists + blocklist + Save & Push) in a two-column layout: Policy occupies the left column, Emergency Freeze and Development & Deployment stack in the right rail; the Override Mode sub-section was removed since overrides now live on the Fleet cards. Nav is now 8 tabs: Dashboard · Gateways · Windows · Statistics · Controls · Fleet Run · Settings · Logs. Also fixed a dangling `fetchRollbackStatus()` reference that silently aborted the Controls fetch chain (the cause of the Feature Flags "Loading…" placeholder never resolving). Front-end only — no API changes.
- **Stats & Gateways consolidation, round 3.** **Stats:** removed the read-only **Policy Gaps** widget (and the `policy_gaps` block in `/api/statistics`) — it was redundant with the richer **Policy Suggestions** widget (per-gateway, NEW badges, Allowlist/Dismiss, Mark All Seen). **Gateways:** the "Active Gateways" cards and the duplicate "Enrollment History" table (both were `/api/gateways`) are now one **unified full-width gateway table** — Status · Gateway · IP · Enrolled · Policy · Health (heartbeat + token dots) · Override (live countdown + Cancel / Override button) · Actions (Uninstall). The SSH keys + one-liner Enrollment widget sits full-width above it. **Fixed a latent uninstall-progress bug** — the progress bar targeted a non-existent 7th column (`cells.length >= 7` never matched a 6-column row), so uninstall progress never rendered; it now renders into the unified table's Actions cell.
- **Uninstall progress modal.** Uninstall progress now actually displays. The old inline row progress never showed because the poller **gave up at t=0** when `/api/uninstall-progress/{ip}` returned no progress yet (the gateway only picks up the trigger on its next ≤30s poll cycle, and the whole uninstall typically completes in ~20-50s), and any rendered progress was wiped by the 5s/10s `fetchGateways()` re-renders. Uninstall now opens a dedicated **progress modal** (independent of table re-renders): it keeps polling every 2s, shows "Waiting for the gateway to pick up the uninstall (≤30s)…" until the first step arrives, maps steps to progress bar + label, and resolves on `complete` **or** when the gateway disappears from `/api/gateways` (deregistration) → "✅ Removed" → auto-close + table refresh. While an uninstall is in flight the gateway's row shows a muted "🗑 Uninstalling…" state (no double-click). Front-end only.
- **Installer privilege + init checks.** The installer (`eshu-installer-template.sh`) no longer fails with a terse "Please run as root." Instead it detects the host's privilege model up front and gives actionable guidance: **root** → proceeds (TrueNAS SCALE console is root with no `sudo` binary — run the one-liner directly); **non-root with `sudo`** → prints "re-run the one-liner prefixed with sudo"; **non-root, no `sudo`** → platform-aware message distinguishing root-shell systems (TrueNAS) from genuinely rootless/immutable ones (Home Assistant OS — not supported); **no `systemctl`** → clear "requires systemd" message. All probes are `if`/`command -v` guarded so `set -euo pipefail` never aborts on a non-root environment. The checks cover install / update / uninstall / migrate modes; runtime gateway scripts are untouched. Docs: README "Supported platforms & installation" table + DEV_GUIDE note.
- **Token self-heal fix.** A gateway whose API token goes missing could get **permanently stuck** after a re-enroll on a host that hasn't rebooted: the poller's self-heal was gated by a once-per-boot marker (`/var/run/eshu.self_heal_done`), and the installer/uninstaller never cleared it, so a stale marker suppressed all future recovery. Fixes: (1) the poller self-heal is now **cooldown-gated (60s) instead of once-per-boot** — if the token is empty it keeps retrying, so a gateway can always recover; (2) the installer clears the self-heal guards (`self_heal_done`, `self_heal_ts`) on **install/reinstall**, and the uninstaller removes them too (matching its "removes runtime files /var/run/eshu.*" behavior). The poller change ships to gateways via the deploy pipeline (Seed Edge → Push to Dev → Deploy to Fleet).
- **Enrollment bootstrap privilege handling.** The `/api/enroll` one-liner used to run the installer with a **hardcoded `sudo bash`** — which is what actually produced the "no sudo" errors on TrueNAS SCALE (root shell, no sudo binary) and Home Assistant OS (rootless). The bootstrap now branches: **root** → runs the installer directly (TrueNAS works, no sudo needed); **non-root with `sudo`** → elevates via sudo as before; **neither** → prints a clear "Eshu Gateway requires root or sudo — Home Assistant OS / rootless systems are not supported" message instead of `sudo: command not found`. Dashboard-side only (bootstrap is generated per request) — no installer regen or fleet deploy needed.
- **Zero-Trust Gateways.** A new strictness tier per gateway (every Eshu gateway is already protected by default — this one means **no implicit trust**): when enabled, **allowlisted commands no longer auto-run** — everything routes to JIT, so the operator personally approves/denies every command (with the P2 ⚠ risk-hint visible). Freeze + blocklists still reject first; approved-window tokens still auto-run (pre-approved by the operator); Fleet Run is unaffected. Per-gateway `zero_trust` flag + `POST /api/gateways/{ip}/zero-trust` toggle (session + audit `zero_trust_enabled/disabled`); `fetch_policy` carries `zero_trust`; the poller writes `/etc/eshu-zero-trust` (atomic, like `/etc/eshu-freeze`); the gateway skips the exact/regex allowlist auto-run when the marker is present, falling through to JIT. UI: 🔒 ZT badge + toggle in the Gateways table. Ships to gateways via the deploy pipeline (poller + gateway scripts embedded in the regenerated installers).
- **Zero-Trust fix: content-based marker check.** The initial gateway guard checked only file *existence* (`[ ! -f /etc/eshu-zero-trust ]`), but the poller always creates the marker file (empty when ZT is off) — so on any gateway running the new poller the file always existed and **allowlisted commands routed to JIT even with ZT disabled** (and toggling ZT off couldn't restore auto-run). Now `eshu-gateway.sh` computes `ZT_ACTIVE` from the marker **content** (`cat` == "1"), exactly mirroring `/etc/eshu-freeze` — ZT is enforced only when the marker content is `1` (absent/empty → auto-run as before).
- **Zero-Trust ↔ Override are now mutually exclusive.** Override Mode auto-approves every JIT request, which would silently defeat Zero-Trust's "every command needs operator approval." Enforcing exclusivity (defense-in-depth): `start_override` is rejected on a Zero-Trust gateway; enabling Zero-Trust is rejected while Override is active; and the `/api/request` auto-approve path additionally skips auto-approval when the gateway is Zero-Trust, so **ZT wins** even if both flags somehow end up set. README: added a **"Security & threat model"** section (honest limitations) and an **"Integrating an AI agent (Hermes)"** section pointing to `AGENT_MANUAL.md` / `AI_WINDOWS.md`, plus updated feature bullets (Zero-Trust; removed the retired Policy-Gaps widget mention).
- **Docs & UI: SSH key roles made explicit + README thinned.** The two enrollment keys are now explained in plain terms in the README Quick Start (a table + `ssh-keygen` examples) and in the dashboard's Gateways → Gateway Enrollment section: the **Eshu Gateway key** is what the AI agent uses (logs in as `eshu-gateway`; every command goes through the policy gate), and the **Approver key** is the operator's key (logs in as `root` directly, bypassing the gateway, for break-glass access). README simplification: removed the API "key endpoints" table (now just a pointer to `docs/API.md`) and the duplicated Project Structure tree (points to DEV_GUIDE's file-structure section) — ~100 lines trimmed with no content loss.
- **Approved Windows decoupled from the feature system (now core/always-on).** `fetch_policy` always delivers the active windows for a gateway (data-driven — the poller already syncs the `approved_windows.sh` feature script based on data presence, not the flag), so windows no longer depend on a toggleable feature flag or its dev/prod scope. The `approved_windows` row is removed from `feature_flags` (existing rows are cleaned up on startup), and the dev-installer/agent-docs URLs for dev-mode gateways no longer gate on the flag — which also fixes the silent coupling where dev-mode updates only worked while the windows flag happened to be enabled. Dashboard-side only: no gateway/poller change, no fleet deploy needed. Docs: README feature bullet, FEATURE_ROADMAP (item done), DEV_GUIDE note.
- **Fix: rejected window-token commands no longer create a duplicate JIT ticket.** The `approved_windows.sh` feature script logged a rejection (`window-rejected`) but never exited, so the command fell through to the JIT stages and created a *second*, spurious JIT ticket per rejected attempt — and because the rejection POST was asynchronous, those `window-rejected` tickets landed with a delay, interleaving with later attempts and making IDs look "out of order" in the history (and confusing the agent/operator). Now any rejected window token is a **hard reject**: the gateway logs one `window-rejected` ticket, prints `[LOCKED] Window rejected: <reason>` to the agent, and exits — no JIT is created. Also handles the edge case of a token presented when no windows are configured. The request history now orders by `id DESC` (monotonic) instead of `created_at` (second-granularity ties) for deterministic ordering. Propagates to gateways via the poller's ~30s feature-script sync — **no fleet deploy or dashboard restart needed**. Docs: AGENT_MANUAL updated (rejected windows + troubleshooting row).
- **Fix: window-token commands on a host with no active windows now hard-reject too.** Because Approved Windows is core/always-on, the poller now **always keeps** the `approved_windows.sh` handler deployed instead of removing it when a host has no active windows. Previously a token presented on such a host fell through to JIT (creating an approvable ticket); now the feature script's guard hard-rejects it (`[LOCKED] Window rejected: no windows configured on this gateway`) — making rejection behavior consistent across all hosts regardless of whether any window is currently active. Ships via the deploy pipeline (poller embedded in the installers).
- **Docs: AI agent docs consolidated into one manual.** `docs/AI_WINDOWS.md` was merged into `docs/AGENT_MANUAL.md` (the Approved Windows section is now the full workflow: request → poll → list/inspect → use the token → tolerance/bitmask → error handling, with the new **hard-reject** behavior documented — a rejected window token produces one `Window Rejected` ticket and no JIT, including the "no windows configured on this host" case). `/api/docs/agent-windows` is now an **alias** for `/api/docs/agent-manual` (both serve the merged manual) so existing agent/skill workflows that fetch the window guide keep working. Also added a **Zero-Trust** note to the auto-approved-commands section (on ZT gateways, even allowlisted commands route to JIT). README + docs/API.md references updated.

---

## [v15.10] — 2026-08-10

### Fixed
- **Fix: gateway `/api/register` re-registration flood.** The installer's `write_poller()` (and `write_gateway()`) replaced the `__GATEWAY_TOKEN__` placeholder with a **global** sed, which also rewrote the poller's self-heal placeholder check — `[ "$GATEWAY_TOKEN" = "__GATEWAY_TOKEN__" ]` became `[ = "<real-token>" ]` (always true) — so every gateway's poller re-registered with the dashboard every ~60s, flooding the audit log with `enrolled` events (~630/hr, ~12k in 19h). The flood started with the v15.7 fleet deploy: the once-per-boot self-heal marker previously suppressed the resulting re-registration, and the cooldown self-heal (`77897c7`) removed that suppression. Fix: the token substitution is now **scoped to the header assignment line only**, so the self-heal placeholder survives; a fleet `--update` rewrites each gateway's poller with the corrected script. Regression tests added (`tests/test_installer.py`). Shipped to the fleet as v15.10 (2026-08-10) — re-registration stopped immediately after the deploy; the 642 `(re-registered)` spam rows that had accrued in the audit log were purged.

---

## [v15.4] — 2026-08-07

### Added
- **Approved Window expiration.** Single-use windows can have an expiry time (date + time pickers, matching the "Starts At" field). Expired windows are filtered from gateway sync, shown with a red "(expired)" label in the UI, and **rejected server-side** — `increment_window_execution()` now refuses to consume a use once `expires_at` has passed, so an expired window can no longer burn its remaining uses.
- **Pipeline status indicator.** The Development & Deployment section shows a colored banner with the pipeline state — `needs_seed`, `ready_for_dev`, `dev_in_progress`, `ready_for_promote` — driven by SHA256 hash comparison of the Build, Edge, and deployed installers. The Build/Edge/Fleet version labels show their short hashes alongside the semantic version (e.g., `Build: v15.4 (a1b2c3d4)`). Seed Edge acts as the gatekeeper: if a previous dev push is active, it confirms before overwriting.
- **Fleet versioning.** Each **Deploy to Fleet** auto-increments the fleet version (`v15.3` → `v15.4`). Gateways report the fleet version on registration.
- **Poller efficiency.** Poll interval changed from 3s to a configurable 30s (`POLL_INTERVAL`), cutting poller CPU from ~8% to <1% per host. JIT approval UX is unaffected (the gateway script runs its own 3s auto-poll for 90s).
- **Token self-healing.** If a gateway's API token is missing (e.g., enrollment raced a dashboard restart), the poller re-registers and persists a fresh token to both `eshu-poller.sh` and `eshu-gateway.sh` — with a one-shot guard so it doesn't spam the audit log.
- **Restart-on-update.** The installer now restarts `eshu-poller` and `eshu-logger` after every `--update`, so gateways pick up new scripts immediately without manual intervention.
- **Logger diagnostics.** `eshu-logger.sh` emits `Heartbeat: poller=X gw=X reach=X` lines for `journalctl` debugging.

### Changed
- **"Golden" terminology renamed** to avoid appearing twice in the pipeline. The current installer is now **Build**, the last deployed version is **Fleet**, and "Promote to Golden" is now **Deploy to Fleet** (rollback: **Rollback Fleet**).
- **Per-gateway version badges removed** from the UI — with only dev and fleet builds, per-gateway version numbers were noise and produced false "needs update" warnings. The dev/fleet distinction is shown via the DEV badge and the Build/Edge/Fleet hash labels.
- **`gen_installer.py` writes to both** `dashboard/eshu-gateway-install.sh` (source) and `dashboard/static/eshu-gateway-install.sh` (served Golden), so `git pull` keeps the served installer in sync with the repo.

### Fixed
- **Unprotected dev-gateways endpoint.** `GET /api/dev-gateways` previously returned full gateway rows (including `api_token`) with no session check. Now session-protected and returns only `{ip, hostname}`.
- **`/api/version` blocked for gateway-token callers.** The version endpoint now rejects requests carrying an `X-Gateway-Token` header (AI agents authenticate this way). Gateways (poller, logger, installer) still fetch the version without a token header, and the dashboard UI uses the session cookie, so neither is affected.
- **Approved windows consuming uses after expiry.** A single-use window with an expiry time could still have its remaining uses consumed after `expires_at`. Fixed by adding the `expires_at` guard to `increment_window_execution()`.
- **"Dev push active" stuck after Deploy to Fleet.** `trigger_dev_update` was never cleared on promote/rollback, so the pipeline banner stayed on `dev_in_progress` even after the fleet deploy completed. `promote_edge()` and `rollback_golden()` now clear it, and the state condition was corrected so `dev_in_progress` only shows while a pushed build has not yet reached the fleet.
- **Service worker removed.** The cache-first service worker cached `app.js` and `/api/*` GET responses, causing stale UI and stale pipeline state after every deploy (browser kept serving old assets even across incognito purges). `sw.js` is now a self-unregistering stub that clears caches and tears down any previously-registered worker; the registration line in `index.html` was removed.

### Security
- Pipeline endpoints (`/api/dev/status`, `/api/dev/seed`, `/api/dev/promote`, `/api/dev/rollback`, `/api/dev-gateways`) are session-protected. Gateway-facing endpoints the agent needs (register, request, log, policy, poll, ticket, window-requests, docs) remain open as designed.

---

## [v15.3] — 2026-07-31

### Added
- **Override Mode.** Per-gateway toggle that auto-approves all JIT requests for a configurable duration (15 minutes to 24 hours). A mandatory reason is captured and written to the audit log (`override_started` / `override_cancelled`). Auto-approved requests are recorded as `jit_override_approved`. The dashboard shows a red animated banner with per-gateway countdowns, and each gateway card gets an Override badge + cancel control.
- **Command descriptions.** Every JIT request, pending ticket, and top-command now shows a one-line description of what the command does. Descriptions are auto-built at startup from the system `whatis` database (`/usr/share/man/whatis`) plus a curated static dictionary (~180 commands). Longest-prefix matching handles compound commands, and `sudo`/`nice`/`nohup` prefixes are stripped before lookup. Served to the UI via `GET /api/cmd-descs`.
- **Statistics page enrichment.** Date range selector (7/14/30/90/365 days), summary cards (commands, automation %, gateways online, JIT approvals), command category breakdown (Storage & FS, System Services, Network, etc.), denied-commands list, and policy-gap detection (commands approved 3+ times via JIT that are not yet allowlisted).
- **Policy Gaps actions.** Each gap has a one-click **+ Allowlist** button and a persistent **Dismiss** button (stored in the new `dismissed_policy_gaps` table).
- **CSV/JSON statistics export.** `GET /api/statistics/export?days=14&format=csv` downloads daily activity as a CSV file.
- **Dev badge.** The dashboard header shows a yellow **DEV** badge whenever the server is running on a non-`master` git branch (detected from `.git/HEAD`).
- **Policy Suggestions.** A persistent background scanner (startup + hourly) analyzes all gateways' JIT history across all time and surfaces commands approved 3+ times that aren't yet allowlisted. New "Suggestions" sidebar view groups gaps per gateway, flags NEW ones, and offers one-click **+ Allowlist** / **Dismiss**. Seen-state is tracked persistently in the database so new gaps are easy to spot.

### Changed
- "Live Requests" table heading renamed to **"Historical Commands"**.
- Statistics page simplified to 6 widgets; low-value widgets removed (daily bar chart, hourly heatmap, automation trend, gateway health cards, approved-windows summary). The extended statistics API still returns this data for programmatic consumers.

### Fixed
- **Auth flash on page load.** `main-sidebar` and `main-content` now start `display:none` and are only shown after the session is verified, eliminating a glimpse of the unlocked dashboard before the login prompt.
- **Command encoding in Policy Gaps buttons.** Commands containing single quotes (e.g. `grep -E 'eshu'`) no longer break the Allowlist/Dismiss click handlers.

---

## [v15.1] — 2026-07-17

### Fixed
- **Critical: `GATEWAY_TOKEN="None"` bug causing 100% JIT failure on all gateways.** Root cause: SQLite migration used Python `None` in an f-string (`DEFAULT None`) instead of `DEFAULT NULL`, writing the literal TEXT string `"None"` into the `api_token` column for every existing gateway. `get_gateway_token()` returned `"None"` (truthy), so the register endpoint returned `gateway_token: "None"` instead of generating a real token. Every gateway then sent `X-Gateway-Token: None`, which `_resolve_gateway_token()` rejected with HTTP 401 → "Dashboard unreachable". Fixed with a 3-layer patch: (1) `database.py` migration now uses `'NULL'` string for proper SQL syntax and runs a one-time `UPDATE gateways SET api_token = NULL WHERE api_token = 'None'` cleanup on startup; (2) `main.py` `_resolve_gateway_token()` now treats the literal string `"None"` as no-token and falls through to the legacy IP path — immediately unblocking all deployed gateways without re-enrollment; (3) Installer Python one-liner now uses `t if t and t!='None' else ''` to prevent "None" string from ever being written to gateway scripts.
- **`first_seen` overwrite bug.** `register_gateway()` was setting `first_seen=now` in every `UPDATE`, making all gateways appear enrolled "30 minutes ago". Fixed: `first_seen` is now only set on initial `INSERT`, never on heartbeat updates.
- **Installer token fallback propagating corrupted "None" tokens.** The existing-token fallback (`grep -oP 'GATEWAY_TOKEN=...'`) now explicitly rejects "None" as a valid token value.

### Added
- **Gateway Rollback feature.** "Push Gateway Update" now auto-saves the current installer as a rollback backup (content + version + timestamp stored in the meta table) before pushing the new trigger. A new "⏪ Rollback" button appears in the Enrollment view whenever a backup is available, showing the backed-up version and save timestamp. Clicking it pushes a `trigger_rollback` ID to all gateways; the poller downloads `/api/gateway-script-rollback` (the DB-stored backup) and runs `--update`. Rollback also resets `trigger_update_version` to the rollback ID to prevent freshly-restored VMs from re-updating automatically. New API endpoints: `POST /api/policies/trigger-rollback`, `GET /api/policies/rollback-status`, `GET /api/gateway-script-rollback`.
- **`[Gateway vX.X]` version suffix on all block messages.** Stage 1 (hardcoded catastrophic blocklist) and Stage 2 (file-based blacklist) block messages now include the gateway version (e.g., `[Gateway v15.1]`), making it immediately clear which installer version is enforcing the block.

### Changed
- **UI queue badge shortened.** "🛡️ Permanently Blocked by Core Policy" → "🛡️ Block by Core" to fit the actions column without overflow.
- **"Update Gateways" confirmation dialog updated.** Now mentions that a rollback backup is saved before pushing, and uses "Push update" language instead of "Trigger update".
- **Version bumped to v15.1.**

---

## [v15.0] — 2026-07-17

### Added
- **Gateway API token authentication (v15+ auth).** All 6 gateway-facing endpoints (`/api/register`, `/api/request`, `/api/log`, `/api/poll/{ip}`, `/api/policy/{ip}`, `/api/ticket/{id}`) now validate an `X-Gateway-Token` header. Tokens are 256-bit hex strings generated on first enrollment and stored per-gateway in a new `api_token` DB column. Legacy v14 gateways (no token) fall through to the self-reported IP path with a deprecation log. Token mismatch returns HTTP 401. Installer captures token from register response and embeds it via sed substitution.
- **Core Blocklist UI transparency panel.** A collapsible "🛡️ Core Gateway Blocklist" panel in the Policies view lists all 13+ categories permanently blocked at Stage 1, with descriptions. Blocked commands in the queue now show an orange "🛡️ Block by Core" badge instead of the action dropdown.
- **Conditional secure cookie for session.** Login sets `secure=True` on the session cookie only if the request arrived over HTTPS (`X-Forwarded-Proto: https`). Plain HTTP homelabs continue to work without HTTPS.

### Fixed
- **Hardcoded blocklist extended to 13+ patterns.** Added `/bin/rm -rf`, `dd of=`, `/bin/dd`, `iptables -X`/`--delete-chain`, `ip6tables -F`/`-X`, `nft flush`, `telinit 0`/`6`, `systemctl isolate reboot`/`poweroff`/`halt`, all `busybox` power variants, Eshu self-access, and `$(which)`/`` `which` `` evasion patterns.
- **Default seed blocklist cleaned up.** Removed 4 entries (`rm -rf`, `rm -fr`, `reboot`, `shutdown`) that were redundant with Stage 1 hardcoded rules.

### Changed
- **Version bumped to v15.0.**

---

## [v14.0] — 2026-07-17

### Added
- **Rate limiting on no-auth gateway endpoints.** `/api/register`, `/api/request`, and `/api/poll` now enforce a sliding-window rate limit (60 requests per 60 seconds per IP). Exceeded limits return HTTP 429.
- **Stale gateway auto-cleanup.** A background thread runs every hour and auto-deregisters gateways that have been offline for more than 7 days. Cleanup events are recorded in the audit log.
- **Policy sync status indicator.** `/api/gateways` now returns a `policy_synced` boolean per gateway, indicating whether the gateway's policy version matches the current dashboard policy version.
- **Search/filter for requests and audit log.** `/api/requests` and `/api/audit_log` now accept an optional `?search=` query parameter. The backend performs SQL LIKE matching across command, IP, hostname, status (requests) and event_type, IP, hostname, details (audit log).

### Changed
- **Version bumped to v14.0.**
- Dashboard seed `dashboard_version` updated from v8.3.11 to v14.0.

### Fixed
- `/api/policies/check` regex blacklist check now uses `re.search()` instead of plain substring matching — consistent with gateway and `test_policy` behavior.

## [v13.2] — 2026-07-17

### Added
- **Statistics page** (▥ sidebar icon). Grafana-style 14-day horizontal bar chart with stacked segments (auto-approved blue, JIT green, blocked orange, denied red). Gateway filter pills with hash-derived colors and All/None quick-select. Top 10 Commands table with command splitting on `&&` / `||` / `;` / `|`. Gateway Summary table with per-gateway breakdowns.
- **Gateway Health indicator.** Pulsing dot (green/orange/red) in the top header with tooltip showing online/offline gateways. 10-second polling. Per-gateway connection dots (green/grey at 30s threshold) in gateway cards.
- **Custom confirm dialog.** Styled dark-themed modal replaces browser-native `confirm()` popups for uninstall, force remove, clear password, and trigger update. Promise-based `customConfirm()` helper.
- **Audit log enrollment events.** `/api/register` now logs an audit event for first-time enrollment, version upgrades, and same-version re-registrations. Deduplication with 5-second window prevents noise from duplicate registration calls during gateway install/update.

### Changed
- **Sidebar consolidation.** Settings moved from top-right header dropdown to sidebar-only ⚙ icon. Admin Space accessible via Settings → "📓 Admin Space". Notification icon changed from control center logo to eshu logo.
- **Enrollment workflow.** One-liner box defaults to empty with grey placeholder ("Generate a token first..."). Copy button disabled by default, enabled on token generation, disabled on token expiry with input cleared.
- **Enrollment history.** "Force Remove" button removed. `first_seen` timestamp always updated on `register_gateway()` so re-enrolled gateways show recent enrollment dates.
- **Gateway cards.** Border color darkened from bright green to subtle tint.
- **Statistics sidebar icon** uses Unicode ▥ instead of 📊 emoji.

### Fixed
- **Stage 1 blocklist bypass.** Hardcoded power command patterns (`reboot`, `shutdown`, `poweroff`, `halt`, `init 0/6`, `systemctl reboot/poweroff/halt`) used exact/prefix matching in bash `case` statements, allowing chained commands like `hostname && reboot` to bypass entirely. All nine patterns now use `*...*` substring matching.
- **Database migration order.** `init_db()` was called before `_migrate_legacy_db()` in `on_startup()`, causing `sqlite3.connect` to auto-create empty `eshu.db` and block migration. Swapped the two calls.
- **Enrollment HTTPS detection.** Enrollment one-liner hardcoded `http://`, causing HTTPS redirects to save HTML as "installer script". Now uses `X-Forwarded-Proto` header detection and `curl -L` flag.
- **JIT notifications tab-locked.** `fetchRequests()` interval only ran on the Home tab due to a `view-home` guard. Removed so notifications work on all tabs.
- **Statistics filter feedback loop.** API-side `gateway_ips` filtering caused `_statsData.per_gateway` to shrink after filtering, corrupting the client-side comparison. Reverted to pure client-side filtering.
- **Custom confirm not closing.** `_confirmResolve` now hides the overlay before resolving the Promise, fixing stuck dialog state.
- **Version string stale.** `FastAPI(title=...)` corrected from "v13.1" to "v13.2".

## [v13.1.2] — 2026-07-16

### Fixed
- **Database migration never ran.** `on_startup()` called `init_db()` before `_migrate_legacy_db()`. Since `sqlite3.connect()` auto-creates an empty `eshu.db`, the migration guard saw the file already existed and skipped copying `hermes_jit.db`. Swapped the two calls so migration runs first — if no `eshu.db` exists yet, it copies the legacy DB, then `init_db()` opens it safely (all `CREATE TABLE IF NOT EXISTS` are idempotent).

## [v13.1.1] — 2026-07-16

### Added
- **`.gitattributes`** to enforce `*.sh text eol=lf` preventing CRLF corruption on Windows.

### Changed
- **`.gitignore`** updated with legacy Hermes file patterns.

### Fixed
- Removed tracked legacy files from git (`git rm --cached`).

## [v13.1.0] — 2026-07-16

### Added
- **Remote uninstall with live progress tracking.** `systemd-run --unit=eshu-uninstall --collect` runs the uninstall as a transient cgroup-independent service. SSE/JSON progress endpoints let the dashboard UI show live step-by-step progress.

## [v13.0.0] — 2026-07-14

### Changed
- **Project renamed from Hermes to Eshu.** All internal references, file paths, systemd units, DB meta keys, and documentation updated. Eshu (Èṣù) is the Yoruba divine messenger and guardian of gates and crossroads.
  - DB: `hermes_jit.db` → `eshu.db` (with auto-migration on first startup)
  - Linux user: `hermes-diag` → `eshu-gateway`
  - Systemd units: `hermes-dashboard.service` → `eshu-dashboard.service`, `hermes-poller.service` → `eshu-poller.service`
  - Session cookie: `hermes_session` → `eshu_session`
  - Installer template: `hermes-gateway-install.sh.txt` → `eshu-gateway-install.sh` (dropped .txt suffix)
  - Policy files: `hermes-exact.txt` → `eshu-exact.txt`, etc.
  - PNG logos: `hermes_control_center_*` → `eshu_control_center_*`
  - DB meta key: `hermes_ssh_key` → `eshu_ssh_key`
- **Tags and labels** updated throughout the dashboard UI and API.
- **Version bumped to v13.0.0.**

---

## [v12.0.0] — 2026-07-13

### Added
- **Browser notifications for new JIT requests.** When a gateway submits a command for JIT approval, the dashboard triggers a browser `Notification` with the pending count. Permission is requested on first click interaction.
- **Web Audio API doorbell chime.** A two-note sine-wave chime plays when new JIT requests arrive. Sound respects the mute toggle.
- **Notification mute & sound test.** Settings dropdown includes: mute/unmute toggle with live label, "Test Sound" (forced chime bypassing mute), and "Test Notification" (browser notification + chime).
- **Pet sidebar with idle/active states.** A pixel-art pet (Noir Neko) lives in a sticky right sidebar. When JIT requests are pending the pet is full-color with an amber glow; when idle it goes grayscale. Toggleable via "Show/Hide Pet" button.
- **Global Central Policy tester.** A tester panel above the policy textareas lets operators paste a command and instantly see whether it would be blocked, auto-approved, or require JIT approval — without making a real request.
- **Regex helper.** A "Regex" button next to the tester auto-generates an escaped `^...$` regex pattern from the pasted command for easy copy-paste into the Smart Regex Whitelist.
- **Approver Mode.** A focused single-column view showing only pending JIT requests as large cards with approve/deny buttons, plus paginated recent history. Toggle via the gold "Approver Mode" button on the dashboard.
- **Policy changes modal.** A "View Policy Changes" button on the policies tab opens a scrollable modal showing the full diff history of every policy edit, with red removal and green addition lines.
- **Dashboard version label.** The dashboard version is displayed prominently below the page title, fetched dynamically from `/api/version`.
- **Password management in Admin Space.** Set or clear dashboard password protection from the Admin Space tab with live status indicators.
- **Force remove gateway.** Gateways that are permanently offline or partially uninstalled can be force-removed from the dashboard record via the Enrollment tab.
- **Copy command button.** Each command in the request table has a clipboard copy button that appears on hover.
- **Uninstall/Force Remove buttons** on the Enrollment page for each enrolled gateway.
- **Ticketing system:** POST endpoints for approve/deny, GET ticket claim endpoint for gateways.

### Changed
- **Policies tab scroll restructured.** Removed the inner `overflow-y: auto` scroll container from the policies right panel. All three whitelist/blacklist textareas now expand naturally to their full height. The entire page scrolls as one unified view via the main content scroll. The "Global Central Policy" header and tester box scroll with the content rather than sticking.
- **Admin Space access.** The Admin Space is now accessed exclusively via the Settings dropdown (⚙ → "📓 Admin Space") rather than as a visible nav tab, keeping the main nav clean with only three tabs.
- **Enrollment history** now displays gateway pills, version badges, and per-gateway uninstall/force-remove buttons.
- **Version bumped to v12.0.0.**

### Fixed
- **Sticky header bleed-through.** Text from the policy textareas was bleeding through the sticky header's padding gutter. Fixed by removing parent container top/side padding and having the header self-handle its own spacing via `padding: 1.5rem 1.5rem 1rem 1.5rem`.
- **Admin Space blank page.** `switchTab('notes')` crashed because it tried to toggle the `tab-notes` nav button, which no longer exists. `switchTab` now checks if the nav button exists before trying to toggle its class.
- **Notification rate limiting.** New JIT notifications are throttled to at most once every 5 seconds to prevent spam during bulk command submissions.

---

## [v10.0.3] — 2026-07-13

### Added
- **Trigger-based gateway uninstall.** Operators can now remotely uninstall a gateway from the dashboard. Clicking "Uninstall" on a gateway card sets a per-IP `trigger_uninstall` flag. On the next poll cycle, the gateway's poller detects the flag, downloads the latest installer script, and runs `--uninstall --yes` in a detached background process. This performs a complete cleanup: stops and disables the poller service, removes all binaries (`hermes-diag.sh`, `hermes-poller.sh`), deletes sudoers config, policy files, runtime files, the `hermes-diag` user, and SSH key entries from `authorized_keys`. Finally, the gateway calls `DELETE /api/gateways/{ip}` to deregister itself from the dashboard.
- **Gateway installer `--uninstall` flag.** The installer script now supports `--uninstall` (interactive, prompts for confirmation) and `--uninstall --yes` (non-interactive, for triggered uninstalls). Either mode can optionally accept a dashboard URL to deregister on completion.
- **New uninstall API endpoints:**
  - `POST /api/gateways/{ip}/uninstall` — Sets the uninstall trigger for a specific gateway and records an audit event.
  - `DELETE /api/gateways/{ip}` — Called by the gateway after successful self-uninstall to remove its registration. Also cleans up disconnected-gateway tracking state.
- **Uninstall trigger in policy response.** The `/api/policy/{ip}` response now includes `trigger_uninstall: true/false`. The poller checks this before processing updates (uninstall is a terminal operation).
- **Stale uninstall trigger cleared on re-enrollment.** If a gateway re-enrolls while an uninstall trigger is pending, the trigger is automatically cleared — enrolling cancels a pending uninstall.

### Changed
- **Enrollment tokens are now single-use.** Previously tokens were time-limited but reusable within their TTL window. Now `validate_enrollment_token()` checks `used = 0` and atomically marks the token as used on first validation. This prevents token replay attacks and enforces one-enrollment-per-token semantics.
- **Version bumped to v10.0.3** — reflects the significant architectural addition of remote uninstall capability.

---

## [v8.3.12] — 2026-07-12

### Changed
- **All JIT timeouts standardized to 90 seconds** across the entire system. Previously the dashboard backend used 120s while the gateway agent used 60s, causing the UI countdown to show "100s remaining" long after the gateway had already timed out. All values now match:
  - `dashboard/database.py`: request creation TTL, approval re-extension, lockbox ticket timestamps (4 values)
  - `dashboard/hermes-gateway-install.sh.txt`: Stage 5 lockbox window, Stage 6 poll timeout, poller cleanup sweep, user-facing status messages (7 values)
  - The UI countdown auto-derives from `expires_at - now` with no code changes needed
  - Enrollment tokens intentionally left at 120s (separate system, not part of JIT pipeline)

### Updated Documentation
- **README.md**: Updated all TTL references from 60s/120s to 90s (How It Works, Data Flow Sequence, Policy Engine table, API docs)

---

## [v8.3.11] — 2026-07-12

### Added
- **Gap detection in request ID sequence.** When request IDs skip numbers (e.g., 248 → 251), adjacent rows show an amber ⚠ indicator with bold amber ID text. Hovering the ID reveals a tooltip: "Gap: #000250 is missing" (or ranges like "Gap: #000249–#000250 (2 missing)"). A 3px amber left border highlights each affected row. No backend changes — purely UI enhancement.
- **Unwhitelist action (reset to JIT).** Whitelisted commands now show `🔄 Unwhitelist (reset to JIT)` in the Actions dropdown. One click strips the command from both the exact-match and regex whitelist textareas and pushes the change. Safe — blacklist is never touched. The operator only needs to know *what* to unwhitelist, not *which* whitelist it's in.
- **Remove from Blacklist action.** Blacklisted commands now show `🚫 Remove from Blacklist` in the Actions dropdown, allowing one-click reversion to JIT approval without manually editing the blacklist textarea in the Policies tab.
- **Mobile responsiveness.** Two `@media` breakpoints:
  - `≤768px`: Pet sidebar hidden, body switches to vertical scroll, reduced padding (12px), nav wraps, table text downsized, actions select constrained to 180px/45vw, textarea min-heights reduced
  - `≤480px`: Further reductions for very small screens (select to 140px, nav gap 4px, 10px font tabs)
- **Direct ticket claim endpoint (`GET /api/ticket/{id}`).** Gateway agents can now fetch approved tickets by request ID directly, bypassing the per-IP poller pipeline for faster JIT approval delivery.
- **Raw gateway installer endpoint (`GET /api/gateway-script`).** Serves the installer with explicit UTF-8 encoding to prevent HTTP transfer corruption that was occurring through the static file mount.
- **Policy membership check (`GET /api/policies/check`).** Returns boolean flags indicating which policy lists (exact whitelist, regex whitelist, regex blacklist) a command already belongs to. Used by the UI to grey out duplicate policy actions.
- **Audit log API (`GET /api/audit_log`).** Dedicated endpoint for fetching audit log events (previously the log was only accessible via the Admin Space UI's inline fetch).

### Changed
- **Enrollment script is now dynamically generated** in `main.py` rather than served as a pre-baked static file. The dashboard URL is pulled from request headers, making enrollment work correctly behind proxies and on non-standard ports.
- **Enrollment meta key default** updated from `v7.0` to `v8.3.11` in the database seed so new installations don't report a stale dashboard version.

### Fixed
- **Enrollment script version strings** were hardcoded to "v7.0" / "v7.0.2" — now reference the live `DASHBOARD_VERSION` constant.
- Various minor UI and API robustness fixes across patches v8.3.2–v8.3.10.

---

## [v8.3.1] — 2026-07-11
**Commit:** `2de79f5`

### Fixed
- **Unbound variable crashes on gateway file reads.** Removed `set -u` from the gateway script so gateways running older versions of bash don't crash when reading policy files that may be empty or partially written. Reportedly caused the gateway agent to exit mid-policy check.
- **Race condition: poller writing policies while gateway reads them.** Policy file writes in the poller now use atomic `tmp + mv` instead of direct overwrite, preventing the gateway agent from reading a half-written policy file during the 3-second sync window.
- **Audit log ordering in trigger endpoint.** Fixed sort order so the most recent trigger events appear first in the audit log.
- **Repeat trigger updates not working.** Replaced version-string comparison with a unique Unix timestamp trigger ID. The poller now tracks `LAST_TRIGGER_ID` instead of comparing version strings, so issuing the same update trigger twice in a row works correctly.
- **Confirmation dialog showing stale version.** The trigger update confirmation dialog now fetches and displays the current dashboard version dynamically instead of using a cached value.
- **Gateways registering with wrong version.** Moved `GATEWAY_VERSION` fetch to before the register API call so newly enrolled or updated gateways report their correct version on first heartbeat.
- **No way to refresh audit log.** Added a refresh button to the audit log panel in the Admin Space tab.

---

## [v8.3.0] — 2026-07-11
**Commit:** `e102aff`

### Added
- **Admin Space tab.** Renamed the "Notes" tab to "Admin Space" and added a split layout with two panels:
  - **Notes panel** (left): persistent admin scratchpad, unchanged from v8.2.0.
  - **Audit Log panel** (right): live event feed tracking gateway enrollments, version updates, disconnections, policy commits, and update triggers with timestamps.

### Changed
- **Notes panel** is now part of the larger Admin Space layout rather than a standalone tab.

---

## [v8.2.0] — 2026-07-11
**Commits:** `d29a2e3`, `80fb162`

### Added
- **Single source of truth for versioning.** New `/api/version` endpoint returns the current dashboard version. The dashboard UI fetches its version dynamically from this endpoint on load rather than relying on a hardcoded string. The gateway installer also pulls the version from this API during installation.
- **Denial awareness in gateway agent.** The gateway now checks `/api/request_status/{id}` after dispatching a JIT request. If the request was denied by an operator, the gateway exits immediately with a "denied" message instead of polling the full 60-second timeout window. This gives operators faster feedback when a command is rejected.

### Changed
- **Actions dropdown sizing.** Locked the Actions dropdown to a fixed width and shortened option labels (e.g., "Whitelist Exact" → "WL Exact") for uniform appearance across all request rows.

---

## [v8.1.1] — 2026-07-11
**Commit:** `f0ac086`

### Fixed
- **Actions dropdown width inconsistent across rows.** Set `min-width: 260px` on all Actions dropdowns so they render at the same width regardless of row content, preventing layout jitter when opening different dropdowns.
- **Purge leaving expired rows behind.** The history purge endpoint was not deleting rows where `status = 'pending'` or `status = 'approved'` with an expired TTL. Fixed the purge query to include these so "Delete older than…" actually cleans up all expired records.

---

## [v8.1.0] — 2026-07-11
**Commits:** `dcdd1c3`, `415f997`

### Added
- **Persistent policy actions dropdown on resolved requests.** Every resolved (approved/denied/blocked) request row now shows a clickable "Actions" dropdown with options to:
  - Whitelist exact command
  - Whitelist via regex
  - Blacklist pattern
  - Options are greyed out if the policy entry already exists, preventing duplicates.

### Fixed
- **Mermaid diagrams not rendering on GitHub.** Removed unsupported `<br/>` HTML tags and `rect rgb()` CSS blocks from the architecture and sequence diagrams in README.md that broke GitHub's Mermaid renderer.

---

## [v8.0.1] — 2026-07-10
**Commit:** `eb1e8f7`

*Initial public release of the Hermes JIT Gateway project.*

### Added
- Full 6-stage policy engine (hardcoded blocklist → file-based blacklist → exact whitelist → regex whitelist → claim-and-burn lockbox → JIT dispatch).
- Dashboard SPA with Classic Gold theme, real-time polling, and pet sidebar.
- SQLite-backed API (FastAPI + Uvicorn) for request management, gateway registration, policy sync, and enrollment.
- Gateway installer script with one-liner deployment (`curl | bash`).
- `hermes-poller.service` (systemd) for 3-second policy + ticket sync loop.
- Enrollment token system with 120-second TTL.
- SSH key management for Hermes agent and approver keys.
- Admin notes persistence.
- Policy versioning with commit workflow.
- Trigger-based gateway update mechanism.

### Fixed
- systemd auto-start: `start.sh` hardcodes path to `venv/bin/python3` to work under systemd without `source activate`.
- `last_updated` timestamp now always stamped on gateway version match, not just on version change.
- Notes save now uses `INSERT OR REPLACE` to properly persist edits to existing notes.

---

## [v8.0] — (pre-git, date unknown)

### Added
- Classic Gold theme (CSS custom properties, `#FFD700` palette, Cascadia Code / JetBrains Mono fonts).
- Pet sidebar with idle/active animation states.
- Policy change history (new `policy_changes` table tracking every edit).
- Command truncation in the request table UI (long commands truncated with ellipsis).
- Deny → blacklist workflow: denying a JIT request prompts the operator to add the command to the blacklist.
- Blocked → +WL button: blocked requests show a one-click "Add to Whitelist" button.

### Changed
- UI polish pass across the entire dashboard.

---

## [v7.1.3] — (pre-git, date unknown)

### Added
- Policy versioning system (`policy_version` counter in `meta` table).
- Enrollment token system for one-liner gateway deployment.
- SSH key management (store and serve Hermes agent + approver public keys).
- Admin notes persistence (new `notes` table).

---

## [v7.0] — (pre-git, date unknown)

### Added
- Dashboard-led enrollment: gateways pull their installer and configuration from the dashboard rather than being manually configured.
- Centralized policy management: all policies (exact whitelist, regex whitelist, regex blacklist) stored and edited on the dashboard, synced to gateways every 3 seconds.

### Changed
- Gateway auto-update removed in favour of trigger-based updates (operator clicks "Update" to push a new version).

---

## [v6.x] — (pre-git, date unknown)

*Initial prototype — the original JIT approval system.*

### Added
- SSH-triggered command execution via `command=` restriction in `authorized_keys`.
- Hardcoded catastrophic blocklist (`rm -rf`, `mkfs`, `dd if=`, `iptables -F`, `reboot`, `shutdown`, etc.).
- Basic JIT approval: gateway POSTs to dashboard, operator approves/denies, gateway polls for ticket.
- 60-second auto-polling with single-use ticket claim-and-burn.
- Output sanitisation for secrets (API keys, passwords, tokens).
- Basic dashboard polling with request listing.