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

## First-run authentication

If no dashboard password is set, startup prints a one-time token:

```
First-run setup token: <token>
```

- On the dashboard host, the setup overlay works from loopback without the
  token. The equivalent CLI call is:

  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/auth/set-password \
    -H 'Content-Type: application/json' \
    -d '{"password":"a-12-plus-character-password"}'
  ```

- From another host, paste the token into **Setup token** in the first-run
  overlay (sent as `X-Eshu-Setup-Token`), or start the service with
  `ESHU_SETUP_TOKEN=<value>` and use that value. Without either, a non-loopback
  first-run request is refused with 403.
- **Behind a reverse proxy on the same host**, the loopback shortcut is
  disabled whenever the request carries `X-Forwarded-For`, `X-Real-IP` or
  `Forwarded`; the setup token is required even though the proxy connects from
  127.0.0.1.
- Passwords must be at least **12** characters. Legacy plaintext hashes are no
  longer accepted at login; reset one with `python3 dashboard/set_password.py`.

## The four steps

| step | what it does | state it reads |
|---|---|---|
| `key` | shows `ssh-keygen`, takes the **public** key, `PUT /api/enroll/keys` | `key_configured` |
| `gateway` | `POST /api/enroll/generate`, shows the one-liner, polls `/api/gateways` every 3s until a gateway appears | gateway count |
| `agent` | the `~/.ssh/config` block, the `session_begin` call, and the first signed `ssh … ` command, using the first enrolled hostname | gateway list (for the host) |
| `integrations` | four example integration cards and a link to the Integrations view | none |

The wizard never invents a state: if the key is already saved it says so, and
if the agent is opened after enrolment step 2 shows the gateway by name.

### The session contract (step 3)

Step 3 must match what the fleet enforces. The agent calls
`session_begin(goal="…", session_id="<chat id>")` and gets back a
`session_sig` plus an `ssh_prefix`:

```
ESHU_SESSION_ID=<id> ESHU_SESSION_SIG=<signature> <command>
```

Every SSH command carries that prefix; every MCP call passes `session_id`. A
gateway with `/etc/eshu-require-session` set to `1` refuses an unsigned command
with `[LOCKED] A minted Session is required…`, so never show a bare
`ssh host "uptime"` — it works only on a gateway that has not opted into strict
mode.

The prefix is shown with ellipses on purpose: signatures are long, and the
point is the shape, not a value someone could copy. Describe it as something the
agent does, not another thing the operator types.

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
three example `tools`. There are four cards, laid out 2×2 (one column on a
phone); adding a fifth means the grid needs `repeat(3, 1fr)` or a scroll, so
prefer replacing one over growing the set. They are rendered previews, not
screenshots — they stay crisp, theme with the page, and cannot go stale.

Blurbs are single-quoted strings, so no apostrophes; `check_onboarding.py` reads
them with a regex and will not parse a card that breaks the shape.

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
4. continue — step 3 names the enrolled gateway and shows the signed command,
   step 4 shows four cards in a 2×2 grid;
5. **Finish** — the modal closes and does not return on reload;
6. **Settings → Getting started → Open** reopens it.

To make an existing dev database show it again:

```sql
DELETE FROM meta WHERE key = 'onboarding_done';
```
