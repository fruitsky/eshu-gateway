# The first-run walkthrough

A four-step introduction that appears once, on an empty install, and walks the
operator from "nothing is configured" to "my agent can cross". It is the only
place in the product that explains the whole setup in order.

- UI: `#onboarding-modal` in `dashboard/static/index.html` (shell) and the
  `ONB_*` block in `dashboard/static/app.js` (content + behaviour)
- styles: "First-run walkthrough" at the end of `dashboard/static/style.css`
- state: `meta.onboarding_done` (server-side, so it is per install, not per
  browser)
- validation: `python3 scripts/check_onboarding.py` (also runs in pre-commit)

## When it shows

`GET /api/onboarding` returns `show: true` only when **both** are true:

1. `onboarding_done` has never been set, and
2. **no gateway is enrolled** (`/api/gateways` is empty).

Consequences, on purpose:

- a fresh install sees it once, right after the first login;
- an existing install (upgrade, restore, another browser) never sees it;
- dismissing it with **Skip for now** or finishing it writes
  `onboarding_done`, so it does not return on the next login;
- closing with **Esc** or a click outside does *not* write it — an accidental
  dismissal comes back;
- it can always be reopened from **Settings → Getting started → Open**.

The rule is "empty install", not "first login": a dashboard with gateways is
not a first run, even if the flag was never written.

## The four steps

| step | what it does | state it reads |
|---|---|---|
| `key` | shows `ssh-keygen`, takes the **public** key, `PUT /api/enroll/keys` | `key_configured` |
| `gateway` | `POST /api/enroll/generate`, shows the one-liner, polls `/api/gateways` every 3s until a gateway appears | gateway count |
| `agent` | the `~/.ssh/config` block and the first `ssh … "uptime"`, using the first enrolled hostname | gateway list (for the host) |
| `integrations` | three example integration cards and a link to the Integrations view | none |

The wizard never invents a state: if the key is already saved it says so, and
if the agent is opened after enrolment step 2 shows the gateway by name.

## Editing it

The content is data. `ONB_STEPS` is an ordered array:

```js
{ id: 'key', label: 'Key', title: 'The key your agent holds',
  lede: 'A pair of keys: the agent keeps one, the dashboard gets the other.',
  render: onbStepKey }
```

- `id` — lowercase-kebab, used nowhere else; unique.
- `label` — the word under the rail tick (keep it to one word).
- `title` — serif heading, ≤ 64 characters, sentence case.
- `lede` — one sentence under it, ≤ 130 characters.
- `render` — a function returning the step's HTML. Add it to app.js; it can use
  `onbCmd()`, `onbInput` styles and the interactive helpers.

Steps are independent: the rail is generated, Back/Continue are generic, and
the footer's last button becomes **Finish** automatically. To insert a step,
add an object in the right position and a renderer; nothing else changes.

`ONB_INTEGRATIONS` is the card list: `name`, `kind`, `url`, `blurb` and two or
three example `tools`. They are rendered previews, not screenshots — they stay
crisp, theme with the page, and cannot go stale.

## Copy and design rules

- Second person, sentence case, no exclamation marks, no invented telemetry.
- Say what happens to the key: the **public** half is stored, the private one
  never leaves the agent.
- Every command must be copyable (use `onbCmd`; the Copy button percent-encodes
  the text in a `data-copy` attribute).
- Amber is the signal: the active rail tick, the waiting pulse. Not headings,
  not buttons.
- No text below 11px; `scripts/ui_check.py` enforces it.
- The card has a fixed height on desktop and is full-screen on a phone, so a
  step can change length without the modal jumping.

## Testing

```bash
python3 scripts/check_onboarding.py    # shape: steps, renderers, cards, anchors
bash scripts/pre-commit.sh             # ui tokens + the checks above
```

For the real thing, run the dashboard against a fresh DB:

1. log in — the modal opens on step 1;
2. save a public key — the step shows "Public key saved";
3. continue, generate a token — the command and the waiting pulse appear;
4. continue through the agent and integrations steps;
5. **Finish** — the modal closes and does not return on reload;
6. **Settings → Getting started → Open** reopens it.

To make an existing dev database show it again:

```sql
DELETE FROM meta WHERE key = 'onboarding_done';
```
