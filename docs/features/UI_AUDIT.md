# UI audit — every view at 1440×900 and 390×844

**Date:** 2026-11-14. **Branch:** `docs/ui-audit` (docs only). **Scope:** the
dashboard as it is on `master` (`14e19fc`), with seeded synthetic data only
(gateways `192.0.2.x`, `*.example.test`, three agents, two pending JITs, one
proposed plan, two sessions with a draft and a confirmed conclusion, two
runbooks, three integrations, inventory rows).

**Method.** A local dashboard (`uvicorn`) on a throwaway SQLite database,
driven by headless Chromium at **1440×900** and **390×844**. For every view
and modal the script recorded:

* a viewport screenshot (kept outside the repo; four are in
  `docs/screenshots/ui-audit/` — [Home at 390](../screenshots/ui-audit/home-pending-390.png),
  [Gateways at 390](../screenshots/ui-audit/gateways-390.png),
  [Gateways at 1440](../screenshots/ui-audit/gateways-1440.png),
  [Inventory at 390](../screenshots/ui-audit/inventory-390.png));
* elements whose right edge passes the viewport (excluding horizontal
  scrollers), and elements with `overflow-x: hidden` whose content is wider
  than the box;
* the text contrast of every visible text node against its nearest solid
  background, using the WCAG relative-luminance formula (4.5:1 normal,
  3:1 for large text).

**Severity.** `blocker` — the view cannot be used as intended at one of the
two widths. `should` — usable, but it misleads, clips or fails AA.
`polish` — a visual inconsistency with no functional impact.

**Owner.** `ui-pass` — fixed on `fix/ui-pass-1`. `X3` — the Integrations page
and its Policy modal; waits for F27. `X7` — the Development & Deployment
panel; in review.

**Not on this branch, so not audited:** the integration **Policy modal** named
in the brief does not exist yet on `master` — it arrives with X3/F27. The
integrations page is audited as it stands and tagged `X3`; its policy modal
gets its own pass with X3.

## Top 10

| # | Where | Severity | Measurement | Fix (one line) | Owner |
|---|---|---|---|---|---|
| 1 | Gateways, table | **blocker** | Table is 1393 px in a 1156 px wrapper at 1440 (237 px hidden) and 1397 px in 402 px at 390. `ACTIONS` is `position: sticky` at x=1246 while `MODES` starts at x=1229, so Actions paints over Modes at 1440; at 390 it paints over the gateway name (rows read `we`, `db`, `pv`). | Fold Enrolled + Policy into the gateway cell as a subline, hide the four-dot legend below 1600, and stop pinning Actions below 1024. | ui-pass |
| 2 | Home, pending queue at 390 | **blocker** | The compact queue row is a five-column grid (`.ccq-row`: 16px, 1.7fr, 1fr, auto, auto). At 390 the two minmax columns collapse, the command is clipped to `systemctl status postgr…`, and Deny/Approve sit on top of it. The 1440 layout is clean. | At ≤640 px make `.ccq-row` a wrapping flex row: command full width first, actions under it, host/TTL last. | ui-pass |
| 3 | All views, faint text | **should** | `--text-faint` `#6f6555` on the panel `#1a1714` = **3.39:1** at 11–12 px (sidebar section labels, brand sub, table keys, session meta, tickers, KPI sublabels). `Log Out` **1.93:1** (`rgba(220,38,38,.55)`), the deny label **3.86:1**, the toast label **4.25:1**, an unstyled `<a>` **2.06:1**. AA needs 4.5:1. | Raise `--text-faint`/`--text-quiet`, style links with the accent colour, keep the refusal state colour, and use a near-white toast label. | ui-pass |
| 4 | History, command column | **should** | `.cmd-code` has 126 px for 170 px of text (session view) and 16 px for 58 px (plain list), so commands read `u`, `systemctl rest…`; the status chips share the cell and overlap the clipped text. | Give COMMAND the widest column and move `output/breakdown/exclude` to their own row. | ui-pass |
| 5 | Inventory, refresh interval | **should** | The number input is 370 px at 390 and 1440 px at 1440; `MIN` sits under it and Save floats at x=129 (390) / x=780 (1440). At 390 the one control takes four rows. | Wrap number + `MIN` + Save in one inline group (`inline-flex; width:auto; max-width:220px`). | ui-pass |
| 6 | Session modal, header at 390 | **should** | Override/Exclude/Filter/× are edge-to-edge (x=97.6, 183.2, 257.7, 335.4; 0 px gaps) on the title row; the title then wraps under them. | Put the actions on their own row with an 8 px gap and pin × to the card corner. | ui-pass |
| 7 | History, session filter | **should** | `#history-session-label` is 162 px wide for 374 px of text at 1440: the goal ends mid-word (`Investigate the 502…`). | Cap the chip at 40ch with ellipsis + `title`, and let the filter row wrap. | ui-pass |
| 8 | Settings (and every view), toast | **should** | The toast is 221×43 at x=720–941, y=12–56: it covers the Settings HUD subtitle at 1440 and the Freeze control at 390 for 2.5 s. | Move the toast below the top chrome (`top: 72px`) and cap it at `min(92vw, 520px)`. | ui-pass |
| 9 | Gateways → Changes modal at 390 | **should** | The close × wraps under the title (y=357 vs 322); the range select becomes a 244×60 block and the counts (`0 changes · 0 alerts`) are pushed to the right edge. | Flex header with × pinned; stack the range row with the counts beneath it. | ui-pass |
| 10 | Integrations | **should** | The inline `Agents` link is browser blue at **2.06:1**; the gate-mode select truncates its own safety text (`destructive — approve only deletes/removals/re`); the TLS checkbox is an unstyled white square. | Accent link token; show the gate-mode wording as a hint line; theme the checkbox. | **X3** |

## Home

Captured: idle, with two pending JITs and a proposed plan, and with a long
queue. Also the recent-sessions table and the needs-attention line.

* **Blocker — pending cards overlap at 390.** See top-10 #2. The queue card
  is 370 px wide; the actions are on the command row. Fix: stack the actions
  and let the command wrap. `ui-pass`.
* **Polish — the plan card starts below the fold with two pending items.**
  At 1440 the plan section begins at y=725 in a 900 px viewport; its header
  is visible but the items and Approve are not. With one pending item the
  card is fully visible. Fixed on `fix/ui-pass-2`: when the first proposed
  plan's own Approve is off-screen, a fixed strip at the bottom names the
  plan and offers Review, Approve plan and Deny, calling the card's own
  `decidePlan` (same confirmation and TTL). It is Home-only and disappears
  as soon as the card's Approve is in view.
* **Polish — the HUD subtitle starts with a stray `·` when it wraps** at
  390 (`· WHO MAY CROSS…` on its own line). Fixed on `fix/ui-pass-2`: the dot
  is a `::before` pseudo-element, hidden at ≤640 px, so a wrapped subtitle
  never starts with it. `ui-pass`.
* **Polish — `0/3 gateways online` and `3 DISCONNECTED` are two statements of
  the same fact** in the idle state, one under the other. Not wrong, but the
  second adds nothing. Leave for the design pass.
* Recent sessions at 390: the goal wraps to three lines and `Open →` sits on
  the right edge (x=330–370), still inside the viewport. No fix needed.

## History

Captured: the plain list, a session-filtered list, and the stats strip.

* **Should — the command column loses the command.** See top-10 #4.
  `ui-pass`.
* **Should — the session filter chip clips its label.** See top-10 #7.
  `ui-pass`.
* **Should — the row's status chips crowd the command cell** (`TICKET
  CLAIMED` sits on `exclude` at 1440). Part of the same fix as #4.
* At 390 the table scrolls horizontally (1397 px in 402 px); the command and
  session columns are off-screen. Acceptable for a table, but the row's
  identity (id, time, host) should stay pinned; currently only `ACTIONS` is.
  Same fix as Gateways #1: pin the first column, not the last. `ui-pass`.
* Stats strip and the `SSH COMMANDS / PROXIED CALLS` tabs render correctly
  at both widths.

## Sessions (recent list and session modal)

Captured: the recent-sessions table on Home and the modal for a session with
a draft conclusion.

* **Should — header actions are fused at 390.** See top-10 #6. `ui-pass`.
* The modal body scrolls and the footer (`sess-alpha`, `Copy id`,
  Confirm/Edit/Reject) stays in the card at both widths — X5's fix holds.
* The `HOW APPROVED` chips wrap to three rows at 390; zero-count chips are
  readable but sit at 3.39:1 (top-10 #3).
* `Add a description…` renders at 3.39:1 and reads as a link rather than an
  input. Give it the input border/placeholder colour. `ui-pass`.

## Runbooks

Captured: the list and the runbook modal (steps + dead ends).

* No layout defects at either width. The modal is 670 px at 1440 and 390 px
  at phone width; steps show the host chip, the command, the note and the
  dead end.
* The step note and `created …` meta are `--text-muted` (3.39:1) — top-10 #3.
* The `Steps (2)` rule inside each step was a dashed line under the command;
  it read as an empty input. Fixed on `fix/ui-pass-2`: a solid `--rule-weak`
  separator. `polish`, `ui-pass`.

## Inventory

Captured: default view with three entities and a conflict line.

* **Should — the refresh-interval control is broken at both widths.** See
  top-10 #5. `ui-pass`.
* The `NEVER REFRESHED` chip and the `REFRESH EVERY` label are both at the
  top right; after the fix they should share one row with the control.
* Entity rows, the conflict line (`192.0.2.10 is claimed by app / web01`) and
  the source chips are readable at both widths.

## Agents

Captured: the list (active + revoked), the detail (policy rail, Tools pane),
and the SSH tab.

* The list stacks cleanly at 390: chips wrap, actions move under them.
* **Should — the detail's section rail becomes a 2×2 grid of labels at 390**
  (`TOOLS / RULES / SSH / ADVANCED` with `All / Arguments / 2 gateways /
  JSON` under them), which reads as a table of values rather than tabs: the
  active state is only an underline. Add the active background and hide the
  summary line under 640 px. `ui-pass`.
* The `Nothing to pick - …` hint uses a hyphen where the rest of the view
  uses an en dash; wording, not this pass.
* Muted labels and the `All`/`Arguments` sublabels are 3.39:1 (top-10 #3).
* SSH tab, any-gateway confirmation and hostname convert offer render
  correctly at both widths.

## Command policy

Captured: the page with the tester, exact and regex allowlists.

* `View Policy Changes` and `Save & Push Policies` wrap to two lines inside
  their buttons at 390. Fixed on `fix/ui-pass-2`: both are `nowrap`, and at
  ≤480 px they share one full-width row each (equal height). `ui-pass`,
  polish.
* The HUD subtitle wraps to two lines at 390; same stray-dot issue as Home.
* The `Open Agents` pointer link is 2.06:1 — the raw `<a>` problem in
  top-10 #3; that is the same fix.
* The tester row (`Paste command` + Test + Regex) fits at 390; the textarea
  is 200 px tall and pushes the allowlists below the fold, which is
  intentional (they follow it).

## Integrations

Captured: the add/edit form, agent/MCP access, the integration cards, the
tools list.

* **Should — see top-10 #10.** Owner `X3` (this page waits for F27/X3).
* The policy modal named in the brief is not on this branch; it is audited
  with X3.
* At 390 the first two form fields look empty because the placeholder is
  3.39:1; the gate-mode select truncates its option text. Both `X3`.
* The `Edit / Test / Seed / Delete` row fits at 390 on one line for each
  integration card.

## Gateways

Captured: the table, the enrollment panel, and the Changes modal for
`web01`.

* **Blocker — the sticky Actions column covers the table.** See top-10 #1.
  `ui-pass`.
* **Should — Changes modal header at 390.** See top-10 #9. `ui-pass`.
* `GATEWAY ENROLLMENT` collapses to a one-line disclosure at 390; the
  one-liner and hint are cut by the same sticky column when it opens, so the
  fix above covers it.
* The `Freeze is fleet-wide` hint is correct and readable.

## Settings (including Development & Deployment)

Captured: Settings with the dev panel hidden, then with
`Show development tools` on and the Build/Edge/Fleet panel visible.

* **Should — the toast covers the header.** See top-10 #8. `ui-pass`.
* The Development & Deployment panel itself is X7 (in review); it was
  audited but not changed here. At 1440 the Build/Edge/Fleet lines and the
  four action buttons fit; at 390 the panel scrolls below the fold.
* The `Show development tools` toggle showed `Off` while the panel was
  visible in one capture (the PUT may have raced the reload). Not counted as
  a defect; the X7 pass should re-check the state on load.
* The webhook/Slack block, password block and getting-started cards fit at
  both widths.

## Statistics

Captured: the 14-day view with the rhythm chart and the KPI grid.

* No layout defects at either width. The KPI grid reflows to two columns at
  390 and the chart shrinks with the viewport.
* `kpi-sub` (`/ 3`, `14-day window`) and the chart's axis labels are
  `--text-muted` at 3.39:1 — top-10 #3.
* `Log Out` at 4.02:1 is top-10 #3 as well.

## What this pass deliberately left

* The Integrations page and its future Policy modal (`X3`, waits for F27).
* The Development & Deployment panel (`X7`, in review).
* The plan card below the fold (polish above) is a design question, not a
  CSS fix in this pass.
* No wording changes to permissions or policy text; the hyphen/en-dash notes
  are recorded, not changed.

## Fix status (fix/ui-pass-1)

Every `blocker` and `should` tagged `ui-pass` is fixed; #10 waits for X3.
Re-measured with the same script, same seed, both widths. "Before" is the
number in the top-10 table.

| # | Before | After | Commit |
|---|---|---|---|
| 1 | table 1393 px in a 1156 px wrapper at 1440; Modes under Actions; host `we` at 390 | table 1156 px in 1156 px (fits, all columns, Actions visible); host `web01.example.test` at 390 | `608f676` |
| 2 | 5-column grid at 390; actions on the command | wrapping flex row; command full width, actions under it | `608f676` |
| 3 | faint 3.39:1, link 2.06:1, Log Out 1.93:1, deny 3.86:1, toast 4.25:1 | no text below 4.5:1 (3:1 large) in any of the 17 states at either width | `ff57c31` |
| 4 | `.cmd-code` 16 px for 58 px of text; chips on the command | command on its own line, chips wrap under it; only a genuinely long command ellipsises | `608f676` |
| 5 | input 370 px / 1440 px wide, Save floating | one inline group: 56 px input + `min` + Save (+ Refresh now) | `608f676` |
| 6 | actions edge-to-edge on the title row at 390 | actions on their own row (5 px gap); × stays on the title row | `608f676` |
| 7 | label 162 px for 374 px, cut mid-word | ellipsis + `title` with the full goal | `608f676` |
| 8 | toast at y=12–56, over the HUD / Freeze | toast at y=76, below the top chrome | `ff57c31` |
| 9 | × under the title; range select full width | × on the title row; select 97×38 with the counts beside it | `608f676` |
| 10 | link 2.06:1, truncated gate-mode wording | not changed (X3/F27) | — |

The browser regression is `tests/ui/ui_views.cjs`, run by
`tests/test_ui_views.py` (no horizontal scroll, the view renders, AA
contrast, plus the gateways/inventory/session checks). On the pre-fix tree it
reports the old failures, including `gateways/1440: table 1344px wider than
its 1156px wrapper` and `contrast 3.39:1 ... div.brand-sub`.

## Fix status (fix/ui-pass-2)

The polish items tagged `ui-pass`, plus the plan-card question:

| Item | Before | After |
|---|---|---|
| Plan card with 2+ pending | card at y=725; its Approve below 900 | fixed strip (`.plan-cta`) at the bottom: "Plan #1 · ops-agent · 2 changes waiting · Review · Approve plan · Deny", Home only, hidden when the card's own Approve is in view |
| HUD subtitle at 390 | wrapped line began with `·` | dot is `::before`, hidden at ≤640 |
| Command policy buttons at 390 | wrapped inside themselves, different heights | `nowrap`; each full width in one row, equal height |
| Runbook step note | dashed rule read as an empty input | solid `--rule-weak` separator |

The browser check (`tests/ui/ui_views.cjs`) asserts that when the plan card's
Approve is outside the viewport, the strip is visible, names the plan and its
Approve is a real in-viewport button. Re-measured at 1440×900 and 390×844; no
new horizontal scroll and no text below AA.

`bash scripts/verify.sh` on `fix/ui-pass-1` (with this pass): `3030 passed,
26 skipped, 7 warnings in 678.59s (0:11:18)`.
