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

### 1.7 Smaller repairs

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

**2.1 Gateway health is colour-only and unlabelled**

Three or four dots per row, red/green, `title` absent, no legend anywhere.
Colour-blind operators and anyone new to the screen cannot decode them.

- Do: a legend in the widget header (● poller · ● gateway · ● reachability),
  plus `aria-label`/`title` per dot and a text summary in the expanded row.
- Done: every dot has a text equivalent; the legend is visible without hover.

**2.2 The `!` glyph before a command**

History rows prefix some commands with `!` and nothing explains it. Either it
means something (anomaly? blocklisted?) or it is decoration.

- Do: replace with a labelled chip (`anomaly` / `risky`) or remove.
- Done: no unexplained glyphs in the table.

**2.3 Audit log is a wall of red**

Six of seven rows are `DISCONNECTED` in `--state-denied`, so "red" no longer
marks an exception. Same for the gateways table where every row said `offline`
and the pulse animation ran on all of them.

- Do: event badges get category colours (state change = neutral, policy
  decision = state colour, failure = red); severity, not event type, drives hue.
- Done: a normal day's log is mostly neutral.

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

**2.5 Statistics repeats itself three times**

The same numbers appear in the chart header (`34 commands · automated 27%`), in
the chart body, and again in the KPI cards below (`34 COMMANDS · WINDOW`,
`27% AUTOMATION AVG`). Four mini bar charts with no axis or labels sit under
the chart as ornament.

- Do: one summary line, chart, then the KPI cards — delete the duplicated
  hero numbers; either label the sparkbars with a period and a total or remove
  them.
- Done: each number appears once.

**2.6 Controls is a 1,100px-wide list of one-line rules**

Thirteen allowlist rows, each a full-width bar with a `×` at the far right,
~450px tall for 13 short strings. The `×` is ~1,000px from the text it deletes.

- Do: two-column denser grid or a grouped list; move the destructive control
  next to the row content; add a count per section.
- Done: a 20-rule policy fits on one screen.

**2.7 Gateways: enrollment owns the top of the page**

The keys + one-liner card takes ~40% of the viewport; the actual fleet table —
what you open the page for — starts below the fold on smaller laptops.

- Do: collapse enrollment into a `<details>` / modal opened by an "Add gateway"
  button; the table becomes the page.
- Done: the fleet table starts within the first 300px.

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

Still open here:

- 119 distinct paddings, 28 radii, 196 rgba values — the token scale exists
  (`--space-*`, `--text-*`) but is used sparingly. Next step is replacing
  literals with tokens view by view, again snapshot-verified.
- Dead generations: `.cc-radar*` (~20 rules, no markup since the constellation
  replaced the radar), `.view-title` / `.view-head*` / `.view-eyebrow`
  (styled, never emitted), `.jit-ticket-*` from the old ticket layout.

**2.9 Glow inflation**

Amber glow on hover of every button, stat card, widget header (`text-shadow:
0 0 20px`), page title (`0 0 24px`) and the selected ticket. When everything
glows, glow stops meaning "act here".

- Do: glow for the selected ticket and the freeze/alert state only; hover =
  border-colour shift.
- Done: two glowing elements max on any screen.

**2.10 Motion has no signal value**

39 `@keyframes`, six or more infinite ambient pulses (three health pulses, the
sidebar edge, TTL, override), a 42s ticker, a radar sweep. The eye's "something
needs a human" channel is spent on the logo.

- Do: pulse only for pending requests and freeze; everything else one 120ms
  state transition. Keep `prefers-reduced-motion` (already global).
- Done: at most one animated element when nothing is pending.

**2.11 Naming drift in the tokens** — ✅ shipped

Was: `--brand-red: #f59e0b` (no red anywhere), `--primary` and `--accent` were
the same colour declared twice, `--fg`/`--text-main` and `--bg`/`--bg-base` were
duplicate pairs, `--ice` was the star colour, `--on-primary` was just the page
background.

Now: `--accent` / `--accent-hover` / `--accent-glow`, `--text-main` /
`--text-dim` / `--text-muted` / `--text-faint`, `--star`, and the state ramp
`--state-pending|approved|auto|blocked|denied`. The aliases are gone; 0 uses of
the old names remain. Snapshot-verified no-op.

**2.12 Accessibility** — 🟡 partially shipped

Zero `aria-*` attributes in 1,269 lines of markup. `.stat-card` (the History
filters) and `.gw-node` (the constellation nodes) are `<div onclick>` — not
focusable, not announced. Keyboard hints exist (`F`, `O`, `C`) but the queue
itself was mouse-only.

Done with 2.4: queue rows are `role="button" tabindex="0"`, Enter/Space opens
them, and `↑`/`↓`/`j`/`k` + `Enter` drive the queue. Still open: everything
else listed above.

- Do: `role="button" tabindex="0"` + Enter/Space on clickable divs; `aria-label`
  on icon-only buttons (`×`, `↻`); `aria-live="polite"` on the pending count.
- Done: keyboard-only walkthrough of approve → deny → filter works.

**2.13 Mobile**

The sky is now a 210px band and the keyboard legend is hidden under 768px, but:
the bottom nav still scrolls sideways and clips ("Approved W…"), tables rely on
horizontal scroll with no affordance, and `Freeze` sits below the fold.

- Do: 4 primary nav items on mobile (rest behind "More"), card-style table rows
  or a horizontal-scroll shadow, Freeze pinned in the header.

**2.14 Small things worth a sweep**

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
