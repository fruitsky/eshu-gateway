# UI cohesion pass — what shipped, and what's next

Branch: `ui/cohesion-pass`
Scope: the dashboard's static UI (`dashboard/static/`). No API, schema or
gateway-script changes.

This is the written follow-up to a full design review of the dashboard as
rendered at 1440×900, 1920×1080 and 390×844. Everything below was measured in
a real browser (headless Chromium against a throwaway copy of the DB), not
inferred from the stylesheet.

---

## 1. What this branch changed

### 1.1 The fonts never loaded

`style.css` declared `'JetBrains Mono'` 225 times and `'Space Grotesk'` for the
body, and shipped **zero** `@font-face`, no `@import`, no font files. Measured:
`document.fonts.size === 0` at load. Every uppercase label and every
letter-spaced eyebrow rendered in the system sans — the "HUD terminal" identity
had no carrier.

Now: four variable woff2 files in `dashboard/static/fonts/` (84 KB total,
latin + latin-ext), `fonts.css`, preloaded. `document.fonts.size` after load: 4
faces, 0 external requests.

### 1.2 Contrast, type and colour

| | before | after |
|---|---|---|
| `--text-muted` on base | `#7a6d5e` — 3.86:1 | `#9c8e7a` — 5.5:1 |
| Sidebar nav items | 1.97:1 (0.55 alpha) | 5.5:1 |
| Smallest font size | 6px, 199 declarations under 11px | 11px floor |
| Text nodes ≤10px on Command Center | 91 | 0 |
| KPI states with a distinct hue | 2 of 5 (four ambers + red) | 5 of 5 |

State colours are now their own tokens (`--state-pending/approved/auto/
blocked/denied`); the brand amber is chrome only. Approve is green-tinted,
Deny is a red outline.

### 1.3 The washed-out feel

The "low-contrast filter" was three things stacked:

1. **The deck fade.** `updateDeckFocus()` set `opacity = 1 - distance*0.72`
   and `translateY(d*16) scale(...)` on every off-centre pending card. The two
   cards beside the focused one were rendered at **28% opacity** — the only
   actionable content on the screen, dimmed by a carousel effect. Removed.
2. **Low-alpha greys.** 43 inline `rgba(122,109,94,0.3–0.6)` text colours
   replaced with solid values.
3. **Film grain above every pixel.** `.grain` sits at `z-index: 9996` over the
   whole viewport at 5% (12% in alert). Halved; still state-driven.

### 1.4 Constellations that aren't shaped like their names

The five figures were hand-eyeballed point sets, then distorted three ways:
`squashed by ASPECT (0.62)`, `rotated up to ±26°`, and drawn into a
`100×100` viewBox with `preserveAspectRatio="none"` across a **2.6:1** canvas,
which squashed every figure vertically by 2.6×. On top of that, three figures
were placed at random centres and their lines crossed.

Now:

- Coordinates are projected from real equatorial positions (RA hours, Dec
  degrees) of the bright stars — Orion with a level belt, Cassiopeia's W, the
  Northern Cross, Vega's parallelogram, the Big Dipper.
- `w`/`h` on each figure carry its true width:height ratio; the renderer works
  in **pixels** and sets the SVG `viewBox` to the container size, so nothing is
  squashed.
- One column per figure, and the **fleet is one named constellation** — the
  smallest figure that can hold every gateway (6 gateways → the Big Dipper).
- `fitSafe()` no longer *upscales* a figure that merely sat too low: it used a
  plain fit-to-bounds ratio, so a 561px constellation was drawn at 776px.
- Redraws on resize (debounced).

### 1.5 JIT cards no longer stretch

- Command text clamps to 3 lines (`-webkit-line-clamp`) with the full text one
  click away in the request panel; cards are a fixed 226px, so **Approve sits
  at the same height on every card**.
- Wrapping is `overflow-wrap: anywhere` + `word-break: normal`, so
  `--no-install-recommends` breaks at the space, not as `no-install-recommen /
  ds`.
- The deck shows a real scrollbar (was `scrollbar-width: none`) and snaps.
- TTL countdown is neutral until the last 15 seconds (was pulsing red the whole
  time).

### 1.6 Truthful headers

`1,204 crossings this week`, `15/16 online · 1 overriding · 0 pending` and
`3 open · next 03:00 UTC` were **literals in `index.html`** that no code path
ever wrote. On a seeded dataset the Gateways header claimed *0 pending* while
five requests were waiting. All three now derive from data already in memory,
and render `—` when there is none.

### 1.7 The sky now carries across the product

The Command Center lived in a night sky; every other page sat on a flat dark
background with the same accent colour. The palette matched, the *motif* did
not — the constellation was a one-page flourish.

Now the motif is a system:

- **Every inner page opens on a band of sky.** `.hud` — the eyebrow line that
  already opened History, Statistics, Gateways, Fleet, Integrations, Controls,
  Settings, Logs and Windows — is now a lit strip: the same deep-blue glow the
  Command Center sits under, a fixed scatter of ~16 stars (three of them amber,
  the "gates"), and an amber hairline where the sky meets the page. The band is
  the same on every page, so it reads as one horizon rather than a per-page
  decoration.
- **The page background keeps the stars.** The tiled star layer behind the
  content was there but too faint to see; it is now in the same brightness
  family as the Command Center's sky, so the space between panels is sky too.
- **The sidebar is part of the same night.** A top glow and a handful of faint
  stars sit behind the nav (through the existing blur/glass), with a hairline
  under the brand and one down the rail — the threshold between the nav and the
  page.
- **Every panel carries the horizon.** `.widget` gets a 1px amber gradient that
  fades at both ends — the same hairline as the band's bottom edge, repeated at
  panel scale.

The constellation itself stays unique to Command Center, because there it is
*not* decoration: it is the fleet map — one figure, one star per gateway. That
hierarchy is the point: the sky is everywhere, the map is where the fleet is.

**The fleet is in every header, in a fixed slot.** Each page's sky band carries the same
constellation the Command Center draws at full size — 150×40, the same figure,
the same stars — with one amber dot per online gateway and a hollow dot per
offline one. It is not decoration: it says *which figure the fleet forms* and
*how much of it is up*, from any page. It rides with the eyebrow text rather
than the right-hand controls, because that cluster grows per page (Statistics
has a selector plus two buttons) and a map that moves around is worse than no
map. It sits in a fixed slot on the right edge of the band (the band reserves
`padding-right` for it): inline next to the eyebrow it slid left and right as each
page's title changed length, which read as jitter. It redraws only when the
fleet's shape or health changes.

This also exposed a real bug in the main sky: every figure was centred on
`(0.5, 0.5)` of its normalised box rather than on its own bounding box
(`w/2, h/2`), so a wide-but-short figure like Cassiopeia sat noticeably above
where it was meant to be. Both the big chart and the mini-map are centred on
their own extents now.

Rejected on the way: a star glyph before every section title (it becomes a flex
item in `.widget-header` and re-centres the title — visible in the screenshots
of that attempt), and per-page constellations (kitsch, and they would compete
with the one on Command Center).

### 1.8 Recent sessions: a carousel became a table

The Command Center's "Recent sessions" was a horizontal card carousel — the
same shape the pending queue used to have, with the same hidden-scroll
problem, and cards that duplicated the session modal's contents. It also sat
outside the design language of the rest of the product, where everything else
is a ledger.

It is now a **table**: Session (kind chip + name, or short id, with the
description beneath) · Host · Activity (`12 cmd · 3 mcp`, pending count in
amber) · Last seen · Open. **Three rows by default**, with a full-width
"Show all N" toggle underneath — enough to see recent activity without giving
a secondary panel the weight of the queue above it. Rows are
`role="button" tabindex="0"` (Enter/Space opens the session), and on a phone it
drops to Session / Activity / Open.

The carousel's drag-and-wheel handlers, its `.rs-*` card CSS and
`renderRecentSessions()` are deleted; `/api/sessions/recent` is now read once
for the table (limit 12) rather than twice.

### 1.9 The Command Center now says something you have to act on

The two cards under the headline — "Command rhythm · 24h" and "Today" — were
unlabelled sparkbars and a count that Statistics already owned. They are
replaced by **Needs attention**, a panel that only speaks when there is
something to do:

- *5 of 5 gateways offline · oldest 3d ago* → Gateways
- *5 requests waiting over 10 min · longest 4h ago* → back to the queue
- *9 commands refused in the last hour* → History

Each row is a button with a severity dot (red / amber / orange), the detail in
mono, and where it takes you. When nothing is wrong it renders one quiet line —
"All clear · 6 of 6 gateways online · nothing waiting too long" — which is the
answer to the question an operator actually opens this page with. Thresholds:
gateway offline, a pending request older than 10 minutes, three or more
refusals in the last hour.

### 1.10 The constellation is now yours, and it travels

**`C` used to re-roll the layout** — same figure, new random positions — so the
header map never changed and the key felt broken. It now cycles **which figure
the fleet takes**, through every constellation big enough to hold the fleet
(5 gateways → Cassiopeia, Cygnus, Lyra, Ursa Major, Orion). The choice is stored
in `localStorage`, applied to the Command Center sky *and* to every page header,
and announced with the existing constellation chime. The legend and the header
map always agree.

The header map also became live and useful:

- **A new pending request pulses that gateway's star** in every page header —
  the sky is the same sky, so a request arriving is visible from Logs or
  Settings without navigating back.
- **Clicking it opens Gateways** (it carries a tooltip saying so).
- Offline gateways are hollow, online ones filled amber.

### 1.11 Fleet Run is a broadcast board

The old screen was a 900px solar-system orbit holding **one node**, flanked by
two narrow rails, with the answers in a separate deck below the fold. The
orbit was beautiful and carried no information; the split put "what I selected"
and "what came back" a screen apart.

It is now a board:

- **Compose** stays where it was (a command, a timeout, a reason, + Queue) —
  it was already the clearest part of the page.
- **Targets** on the left: the roster with Online / All / None, and the
  **Dispatch (N)** button at the foot of the panel — the action sits with the
  selection it acts on, instead of in the far-right rail.
- **Queue** at the top of the right column, as inline chips (`01 systemctl
  status nginx · 5 nodes · 180s · ×`).
- **Broadcasts** below it: one row per run with its verdict (`ALL CLEAR`,
  `RUNNING 2/3`, `PARTIAL`, `DENIED`) and per-ear chips that colour as they
  answer.
- **Output** directly under the list, for the selected run: per-host rows with
  exit code and a collapsible full output.
- **The orbit became a 44px dial** in the broadcasts header. It still animates
  the same nodes and still pings each time a result lands — the sound design
  survives — but it now reads as "what is the fleet doing right now" instead of
  occupying half the page. Its state label shows the selected run's verdict
  (`idle`, `running 2/3`, `all clear`, `denied`), colour-coded.

Offline nodes used to sit unpositioned at the centre of the dial (they never
run through the animation loop); they now take their slot on the ring at build
time.

### 1.12 Statistics: two new datasets, and a grid you can read

The page measured the past; it did not tell you *when* the fleet works or
whether the fleet is consistent. Two additions, both from data the API already
had but nothing rendered:

- **"When the fleet works"** — a 7×24 weekday × hour grid. The endpoint has
  returned `hourly_heatmap` for a while and no widget used it; a flat 24-bucket
  histogram cannot answer "is this a nightly-cron fleet or a workday fleet?".
  The grid uses a five-step amber ramp, hour labels every three hours, a
  weekday column, a hover title per cell ("Tue 14:00 — 12 commands"), a
  `peak FRI 08:00 · 8 cmds` chip and the window total. It also looks like the
  rest of the product: a field of stars whose brightness is activity.
- **"Fleet hygiene"** — three facts that need a person: version spread
  (`v15.4 ×2 · v15.2 ×1 · v15.1 ×1`, "3 releases behind the newest"), token
  coverage (a gateway without a token silently cannot receive JIT), and
  approved windows that have never executed. Each turns amber when it needs
  attention, and the header chip counts the problems ("3 to look at").
- **Top commands now show their spread** — `hostname · 2 hosts`. A command
  spreading across the fleet is a different signal from one host hammering it,
  and the API now computes it in the same pass that splits shell chaining.

The backend additions: `weekly_heatmap` (7×24, Monday-first, from
`strftime('%w','%H')`), `busiest` (the hottest cell, for the chip), and
`hosts` per top command. Existing keys are untouched.

### 1.13 Integrations and Gateways show health, not just configuration

- **Integrations** had no "is it actually working" signal: you could see a name,
  a URL and a gate mode, but not whether the upstream answered. Each card now
  carries a last-call line — a state dot, `last call 21m ago`, and
  `6 calls / 24h · 6 failed · 48ms avg` — fed by a new
  `GET /api/integrations/health` (aggregate over `integration_calls`, one row
  per integration). A card whose last call failed turns amber; a never-called
  integration shows a hollow dot.
- **Gateways** gained a `24h` column: 24 hourly bars per row, from a new
  `per_gateway_hourly` map on the statistics endpoint (rotated so the right
  edge is the current hour). It answers "is this host busy or idle" without
  leaving the table. Nine columns now fit because the table's cell padding was
  tightened for this view.

### 1.14 Pilot: "ink and signal" on Statistics

Borrowed from [sora-to-ki](https://github.com/koimsurai/sora-to-ki) — the
principles, not the assets (their code is AGPL, their content all rights
reserved). Their system is three materials: **black is the paper, white is the
ink, amber is the signal — ever on one point, never a fill.**

Eshu spends amber on about ten different roles, which is why nothing stands
out. This pilot applies the discipline to **one page only** (everything is
scoped to `#view-stats` and reverts in one block):

- **A display serif** (Instrument Serif, OFL, 21 KB, regular + italic) for the
  page word, the 44 and the KPI values. Numbers in a ledger deserve a ledger
  face; it is the single change that stops the page reading as a dark admin
  theme.
- **KPI cards → corner brackets.** Four hairline L-marks at the cell corners
  instead of a filled card. Cheap, instrument-like, and it disappears when the
  number isn't interesting.
- **Sections are rules and air**, not filled panels: `border-top` and 34px of
  margin, no background, no radius.
- **Headings go to ink.** Amber is no longer a heading colour.
- **The heat grid is ink; one cell is the signal.** The busiest hour is the only
  amber cell in the grid — the rule made literal.
- **Chips are ink outlines**, except the one that is a signal (the hygiene
  count), which keeps the amber.

Result: the only amber left on the page is *live data* — the chart, the fleet
nodes, the peak cell, the alert count. Nothing in the chrome is amber.

### 1.15 Pilot 2: Command Center becomes a page, not a dashboard

The same ink-and-signal language, now on the screen people actually live in
(scoped to `#view-home`):

- **The sky is the page.** The constellation is no longer a header band above
  the queue — it runs behind it. The queue sits *in* the figure, with the
  constellation's lines passing behind the hero card, which is the
  star-trails-behind-text move from sora-to-ki's Notes page. The sky grew to
  `clamp(320px, 46vh, 520px)` with a 190px negative margin, and the content
  layers above it.
- **The queue is a ledger.** The hero keeps its command block but loses the
  card: transparent surface, corner brackets, no radius, no amber border. The
  remaining requests are hairline rows on the sky rather than a bordered
  container — you can see stars through the list.
- **"At the threshold · 5 waiting"** is set in the serif at 38px. It is the
  page's voice, and it is the only place on this screen that shouts.
- **The keyboard key moved out of the sky** into a footer line under the
  ledger. On the sky it collided with the constellation; as a reference line at
  the bottom it costs nothing.
- **The anomaly is a word, not a mark.** The 3px red edge on an otherwise
  borderless card read as a stray pen mark; the hero now carries an `ANOMALY`
  chip beside the command's plain-language line.

### 1.16 Pilot 3: the table screens

History and Gateways were the last two "panel" screens. Both now follow the
same rules (scoped to `#view-history` / `#view-gateways`):

- **Sections are rules.** The Command History panel and the Active Gateways
  panel lose their fill, border and radius; a hairline and 30px of air separate
  them. Gateway enrollment became a hairline `<details>` section rather than a
  card above the table.
- **The five states keep their colours**, because on this page colour *is* the
  meaning — but the numbers are now 34px Instrument Serif inside corner
  brackets, so the row reads as an instrument panel rather than five boxes.
- **Tables are hairlines.** No sticky header fill (the blurred
  `rgba(15,13,10,0.72)` band is gone), no zebra striping, no row shadows —
  header rule, row rules, and a 3% ink hover.
- On a phone the five states go two-up again (a 26px gap would push each card
  to full width and make the row 500px tall).

### 1.17 Smaller repairs

- Fleet roster: hostnames were clipped to one letter (`h…`, twice) because the
  IP won the width fight. Hostname + state on line 1, IP on line 2.
- Audit log: `691233s ago` → `8d ago` (one `formatAgo`), and `7 events` no
  longer wraps into the refresh button.
- Pending badge: the countdown's fixed 32px box ran into the status word
  (`6389SPENDING`).
- Sky height `52vh` → `clamp(240px, 34vh, 380px)`; the queue now fits above the
  fold at 900px instead of overflowing.
- Three rules with `font-family: ''JetBrains Mono''` (doubled quotes, dropped
  by the parser) repaired.
- `*.db-shm` / `*.db-wal` added to `.gitignore`.

---

## 2. What is still open

Ordered by what blocks use. Each item: why it matters, what to do, how to know
it's done.

### P0 — trust and comprehension

**2.1 Gateway health is colour-only and unlabelled** — ✅ shipped

Was: four identical `⬤` glyphs per row, green or red, meaning carried only by
colour and a `title` tooltip, with no legend on the page.

Now: **filled `●` = ok, hollow `○` = failed** (shape carries the answer,
colour reinforces it), each dot has `role="img"` + `aria-label`, the row
carries a summary (`Health: poller ok, gateway ok, reachability failed, token
ok`), and the column header spells out the order: `poller · gateway · reach ·
token`.

**2.2 The `!` glyph before a command** — ✅ shipped

Was: *two different facts* — a risk note and "first time this gateway runs X" —
rendered as the same `!`, told apart only by hovering.

Now: risk keeps `!` (`aria-label="Risk: …"`), the anomaly is a small `new`
chip, and both are announced.

**2.3 Audit log is a wall of red** — ✅ shipped

Was: `disconnected` mapped to `badge-denied`, so a routine overnight log was
eleven red rows; red marked "a gateway went to sleep", not "something is
wrong". The badge family was also internally inconsistent (`.badge-approved`
was an amber tint with green text, `.badge-auto` an amber tint with blue text,
`.badge-info` defined twice with orange and then ice).

Now: **severity decides the colour.** Refusals (`jit_denied`,
`*_denied`, `mcp_policy_denied`) are red; approvals green; irreversible actions
(uninstalled, deregistered, token revoked) orange; everything else — connects,
disconnects, enrollments, config edits — is neutral. Each badge's tint, text
and border share one base colour.

### P1 — information architecture

**2.4 The pending queue hides its own length** — ✅ shipped

Was: `.jit-deck-scroll`, a horizontal carousel with the scrollbar deliberately
hidden (`scrollbar-width: none`, `::-webkit-scrollbar { height: 0 }`,
`perspective: 1200px`). With five pending requests, 3.4 cards fit in 1040px and
the 4th was sliced off; approving requests 4 and 5 meant guessing that a queue
scrolls sideways.

Now: **hero + list** (option B). The top-priority request gets the full card —
command block, TTL, plain-language impact, Approve/Deny — and the rest are
compact rows with description, command, host, TTL and inline actions. All of
them are visible at 1440×900 with five pending. Priority is honest rather than
insertion order: an anomalous command first, then whichever expires soonest,
then the oldest.

Keyboard: `↑`/`↓` (or `j`/`k`) moves a cursor through the queue, `Enter` opens
that request's detail panel. Rows are `role="button" tabindex="0"` with
Enter/Space handlers, so the queue is reachable without a mouse.

Remaining in this area:

- The bulk-selection bar (`Approve All` / `Deny All`) still lives above the
  fold in `#cc-bulk-bar` and only appears once something is ticked — it should
  be visible in the queue header ("4 more waiting · select all").
- `a`/`d` single-key approve/deny was deliberately **not** added: a keystroke
  that executes an SSH command is too easy to fire by accident. If it is ever
  wanted, make it opt-in per session.
- At 768px the rows lose the host and TTL columns; the command is ellipsised to
  ~30 characters. Consider a stacked mobile row (description, then command on
  its own line, actions right) rather than truncation.

**2.5 Statistics repeats itself three times** — ✅ shipped

Was: `34 commands` in the page HUD, again as the chart's hero number, again in
a KPI card; `27%` twice; plus four spark-panels under the chart whose bars had
no axis, no period and no relation to the number above them (the "Auto-approved
9" panel plotted the automation *rate*).

Now: the HUD carries no numbers, the chart owns the total + window + automation
rate, and the four cards carry the facts the chart does not — **9 auto-approved
by policy · 15 cleared by a human · 10 refused (6 policy / 4 human) · 0/5
gateways online** — coloured in the same language as History. The spark panels
are gone; each number appears once.

**2.5b "The nodes" was a horizontal line** — ✅ shipped

Reported as a long-standing bug: every gateway in the Statistics scatter sat on
one horizontal line. Two causes, both in the placement maths:

1. The y value was `(total − blocked − denied) / total`, i.e. *everything that
   was not refused* — which counts human-approved JITs as automation. With
   nothing blocked or denied, every gateway scored exactly 100% and landed on
   the same row at `y = 16`.
2. `Math.round(rate * 100)` collapsed near-equal rates onto the same row even
   when the underlying numbers differed.

Now `y = auto_approved / total`, unrounded for placement, and any nodes that
still land in the same 3% cell are pushed apart on a golden-angle spiral — so a
fleet with genuinely identical numbers reads as a cluster instead of one dot.
The panel also gained what a scatter needs to be readable: a 0/25/50/75/100%
grid, an "automated" axis label, dot size described in the legend (it encodes
volume against the busiest gateway), and a tooltip that separates auto / human /
refused instead of printing one misleading "automated" figure. The arbitrary
connector lines between the four busiest nodes are gone — they implied a trend
that did not exist. Nodes are `role="button" tabindex="0"` with Enter/Space.

**2.6 Controls is a 1,100px-wide list of one-line rules** — ✅ shipped

Was: 13 allowlist rows, each a full-width bar with a `×` at the far right —
~450px of height for 13 short strings, and the delete control ~1,000px from the
text it deletes (the classic Fitts-law failure).

Now: chips size to their command and wrap into rows, so the `×` sits next to
the text it removes and the same policy takes about a third of the height —
20+ rules fit where 13 used to sprawl. Commands longer than 46 characters still
ellipsis with the full value in the `title`.

**2.7 Gateways: enrollment owns the top of the page** — ✅ shipped

Was: the SSH keys + one-liner card took ~40% of the viewport, so the fleet
table — the reason you open the page — started at y≈530 and fell below the fold
on a laptop.

Now: enrollment is a `<details>` that is closed by default, with the hint "Add a
host · SSH keys & one-liner" in its summary. The table starts at ~160px and the
whole fleet is visible without scrolling. Nothing was moved out of the DOM, so
every handler (copy one-liner, generate token, edit keys) still works — open the
panel to use them.

### P2 — system and craft

**2.8 The stylesheet is two stylesheets** — ✅ mostly shipped

Was: 1,159 lines of base rules plus a 1,690-line override layer (59% of the
file) carrying **136 `!important` occurrences across 60 rules**. Three
generations of the same selector were held in line only by the flag —
`.widget` was defined three times, `.login-card` three times, the form
controls three times, `thead th` three times.

Done on this branch, by measurement rather than by eye:

1. A **computed-style snapshot harness** (`/tmp/opencode/pw/snapshot.py`)
   records 30 computed properties for every element on all 10 views plus the
   login screen, at 1440×900 and 390×844 (2,200–3,150 elements per view). Dust
   stars are seeded so the diff is deterministic.
2. An **`!important` audit** flips each flag off in the live CSSOM and re-reads
   computed styles, classifying every rule as *load-bearing* or *safe*.
3. Safe flags removed; load-bearing conflicts resolved properly — merged into a
   single declaration, out-specified (`.select-actions.btn-muted`, `.panel
   .uninstall-confirm-input`, `.widget.dropdown-menu`), or moved after the rule
   they were shadowing.

Result: **3 `!important` rules remain, each deliberate and commented** —
`.hidden` (utility), `tbody tr:nth-child(even)` (zebra must beat row states),
and the `prefers-reduced-motion` reset.

The refactor is verifiably pixel-equivalent: 0 computed-style diffs on desktop,
and on mobile only two *intended* restorations — the tables render at the
11px the 480px breakpoint always asked for (a global `13px !important` was
overriding it), and the uninstall confirmation field finally shows the
`--danger` border its rule always intended (it lost to a more specific
`input[type=text]` rule).

**Undefined tokens (found during this pass).** Four custom properties were used
161 times and never defined anywhere in the project: `--border` (82 uses),
`--muted` (69), `--info` (7), `--border-subtle` (3). Every
`border: 1px solid var(--border)` was therefore **invalid at computed-value
time and silently dropped**, and every `color: var(--muted)` fell back to
`inherit` — which is why some panels had borders and others did not, with no
rule explaining the difference. They now exist as aliases
(`--border` → `--border-color`, `--muted` → `--text-muted`, `--info` →
`--state-auto`), which restores ~160 declarations. `todo: fold the aliases into
direct token names once the usage sites are touched`.

Still open here:

- 119 distinct paddings, 28 radii, 196 rgba values — the token scale exists
  (`--space-*`, `--text-*`) but is used sparingly. Next step is replacing
  literals with tokens view by view, again snapshot-verified.
- Dead generations: `.cc-radar*` (~20 rules, no markup since the constellation
  replaced the radar), `.view-title` / `.view-head*` / `.view-eyebrow`
  (styled, never emitted), `.jit-ticket-*` from the old ticket layout.

**2.9 Glow inflation** — ✅ shipped

Was: amber bloom on hover of every button, stat card and session card, plus
`text-shadow` on every heading, KPI number, HUD line and the login title —
18 text-shadow declarations across the stylesheet.

Now: **no text glow anywhere.** Glow is reserved for things that emit light in
the metaphor — constellation stars and nodes, the freeze/override banners, the
transient approve/deny flash — and hover/selection is carried by border colour.
1,137 box/text-shadow declarations removed across the 12 rendered states
(snapshot-verified: 0 non-shadow computed changes).

**2.10 Motion has no signal value** — ✅ shipped

Was: ambient infinite pulses on health dots, connection dots, fleet run dots,
status dots, the mobile-menu edge, plus a radar sweep and a 42s ticker. On an
idle screen roughly a dozen things moved.

Now, infinite motion is limited to signals that need a human or are content:
**a pending request's node pulses**, the TTL goes red and pulses only in its
final 15s, freeze/override banners pulse, a progress bar animates while work is
in flight, the ticker scrolls (it is content), stars twinkle. Static presence
(green health dot, connected dot, running dot) no longer beats.
`prefers-reduced-motion` still wins globally.

**2.11 Naming drift in the tokens** — ✅ shipped

Was: `--brand-red: #f59e0b` (no red anywhere), `--primary` and `--accent` were
the same colour declared twice, `--fg`/`--text-main` and `--bg`/`--bg-base` were
duplicate pairs, `--ice` was the star colour, `--on-primary` was just the page
background.

Now: `--accent` / `--accent-hover` / `--accent-glow`, `--text-main` /
`--text-dim` / `--text-muted` / `--text-faint`, `--star`, and the state ramp
`--state-pending|approved|auto|blocked|denied`. The aliases are gone; 0 uses of
the old names remain. Snapshot-verified no-op.

**2.12 Accessibility** — ✅ shipped for the interactive surface

Zero `aria-*` attributes in 1,269 lines of markup. `.stat-card` (the History
filters) and `.gw-node` (the constellation nodes) are `<div onclick>` — not
focusable, not announced. Keyboard hints exist (`F`, `O`, `C`) but the queue
itself was mouse-only.

Shipped: `.stat-card` filters and `.gw-node` nodes are `role="button"
tabindex="0"` with Enter/Space and `aria-label`/`aria-pressed`; the bulk-select
square is a real `<button role="checkbox" aria-checked>`; nav items carry
`aria-current="page"`; the content area is a `<main>` landmark; icon-only
buttons (`×`, `↻`, mobile menu) are labelled; the toast is
`role="status" aria-live="polite"`.

Still open: `aria-expanded` on the request panel and session cards, and a
proper focus trap in modals (Escape closes, but Tab can leave the dialog).

- Do: `role="button" tabindex="0"` + Enter/Space on clickable divs; `aria-label`
  on icon-only buttons (`×`, `↻`); `aria-live="polite"` on the pending count.
- Done: keyboard-only walkthrough of approve → deny → filter works.

**2.13 Mobile** — ✅ shipped

Was: an 11-item bottom bar that scrolled sideways and clipped mid-word
("Approved W…"), no current-page indicator, tables that cut off a column with
no cue, and `Freeze` — the one control you might need in a hurry — buried at the
bottom of the drawer.

Now: **Home · History · Stats · Fleet · Freeze · More** fits 390px without
scrolling, the current page is underlined, `Freeze` is one tap away and mirrors
its state (`Frozen` + red), `More` opens the full drawer, content carries
`padding-bottom` so the fixed bar never covers the last row, and scrollable
tables fade their right edge instead of slicing a column.

**2.14 Small things worth a sweep** — 🟡 mostly shipped

- **`/api/web-fetch-policies/{principal}` 404 on every Controls visit** — fixed
  client-side. The 404 is deliberate (a test pins it: a principal with no policy
  is a 404, not an empty policy), so the client now reads the list endpoint and
  picks its principal out of the result: one request instead of a guaranteed
  404, and no console error.
- **Modal focus trap** — shipped. Every `.modal-overlay` is observed for class
  changes; on open it gets `role="dialog"`, `aria-modal="true"` and an
  `aria-label` from its header, focus moves to the first field, Tab/Shift-Tab
  cycle inside, and focus returns to the opener on close.
- Still open: `aria-expanded` on the request panel, and a copy audit of the HUD
  strings (the old legend had `THE SHAW` for `THE SWAN`, long gone, but the
  strings are hand-written in a few places).

- Login: visible banding in the sky gradient (add a noise/dither layer or a
  smoother ramp), and the logo's red art is the only red in the product before
  you are even logged in — it competes with "red = denied".
- `THE SHAW` was a typo for `THE SWAN` in the old legend string; it is gone now,
  but a copy audit of the constellation/HUD strings is worth one pass.
- The `2.6:1` sky is a hard canvas for real star charts: if the constellation
  stays a hero, consider a dedicated **fleet map panel** (portrait-ish, 1:1 or
  4:3) where a real figure reads properly, and let the Command Center header be
  a thin band.
- With 1–2 gateways the figure still draws its full 5–7 stars, so 3–5 dots are
  decorative. Either fade them further or scale the figure to the fleet.

---

## 3. How this was verified

### Computed-style regression harness

For CSS-only work, screenshots are too coarse and eyeballing is not evidence.
`snapshot.py` walks every element on every view and records 30 computed
properties (colour, border, font, spacing, size, transform, shadow, opacity),
keyed by a DOM path; `diffsnap.py` reports any element/property that moved.
`Math.random` is seeded before load so the procedural starfield is identical
between runs.

Method for any no-visual-change refactor:

```
python3 snapshot.py baseline.json          # before
# …edit the CSS…
python3 snapshot.py after.json
python3 diffsnap.py baseline.json after.json   # expect TOTAL PROPERTY DIFFS: 0
```

For `!important` work specifically, `audit_important.py` toggles each flag off
in the live CSSOM and checks whether any computed value moves — that is what
separates "load-bearing" from "someone was afraid of the cascade".



```
# throwaway copy, never the repo DB
rsync -a --exclude eshu.db --exclude venv dashboard/ /tmp/review/dashboard/
python3 -m uvicorn main:app --host 127.0.0.1 --port 8123   # from the copy
# headless Chromium via playwright, 1440x900 @1.5x, 390x844, plus 1920x1080
```

Checks run on the branch:

- `bash scripts/pre-commit.sh` — all pass
- `python3 -m pytest tests/` — 947 passed
- `node --check dashboard/static/app.js`
- Rendered at 1440×900 / 1920×1080 / 390×844; every view screenshotted before
  and after; no console errors, no 4xx.

Acceptance for "done" on any item above: it is visible in a screenshot diff and
it survives `prefers-reduced-motion` and a keyboard-only pass.

---

## 4. Suggested order

1. ~~2.4 queue IA~~ — shipped on this branch (hero + list, keyboard)
2. 2.11 token rename + 2.8 CSS consolidation (do together, before more rules)
3. 2.12 accessibility (the rest: stat-card filters, gateway nodes, aria labels)
4. 2.9 / 2.10 glow + motion budget
5. 2.1 / 2.3 meaning of colour (health dots, log badges)
6. 2.5 / 2.6 / 2.7 page-level density
7. 2.13 mobile, 2.14 polish
