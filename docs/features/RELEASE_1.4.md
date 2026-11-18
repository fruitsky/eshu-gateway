# Release 1.4 — fleet rollout notes

Status: **draft for the end-of-sprint fleet rollout.** Do not tag or deploy
from this file yet. The X11 screens and the two security branches are marked
**pending review**; finalise those sections after the reviews.

Last fleet release: **1.3** (deployed 2026-11-14 at `f0af7e7`). These notes
describe what an operator sees after upgrading the dashboard and the fleet
gateways from that release. Nothing here is a commitment to deploy.

## What changed

### Agents and permissions, redesigned (X11 — pending review)

The separate **Allowances** page is gone. Everything lives under **Agents**,
which now has two tabs:

* **Agents** — one card per agent, with a plain one-line summary of what it
  can do, when it was last active and how many permissions are paused.
* **Rules for all agents** — protected devices, the limits every agent
  shares, and what is never allowed.

Opening an agent gives one page with **Overview, Tools, SSH, Websites,
Activity and JSON**. Home Assistant device permissions are edited by picking
friendly device names in a grid grouped by area; protected devices are
locked; a column can follow a Home Assistant label instead of individual
devices. Every save is **Check → preview → Apply**, and the preview shows the
risky devices, resumed permissions and waiting label members before anything
changes. A stale screen is refused and reloaded instead of overwriting a
newer change.

**What the operator needs to do:** nothing to migrate. Existing permissions
keep working and appear under Overview. Open **Agents** after the upgrade and
check that the card summaries and the device grid show what you expect.

### Approved Windows are set from presets (X10)

The window modal opens with **Now for 15 min**, **Now for 1 hour**, **Until
end of today** and **Custom**, defaults to **Once**, and uses one typed local
`HH:MM` field plus a duration instead of hour/minute wheels. A plain-words
sentence says when it will run and how many times. Agent window requests use
the same preview.

**What the operator needs to do:** none. Existing daily windows keep their
stored schedule.

### Home Assistant label names are readable by agents (pending Security)

The generic Home Assistant `ws_read` tool may now list the label registry
(`config/label_registry/list`). Labels are read-only metadata. Label
create/update/delete and every other `config/*_registry` write stay refused
and use the approval-gated `ws_write` tool.

**What the operator needs to do:** none.

### Window tokens no longer travel in URLs (pending Security)

The two old URL forms — the pre-BJ2 claim route and the public
`/api/window-by-token/<token>` lookup — are retired with **410 Gone**. The
token now goes in a JSON body:

* `POST /api/approved-windows/execute` with `{"window_token": "..."}`
* `POST /api/window-by-token` with `{"token": "..."}`

The old routes name the replacement, never echo the token, and the access
log masks the old path. Window execution history returns only a fingerprint.

**What the operator needs to do:** gateways must be on the BJ2 body-token
form before this dashboard change. The 1.4 gateway stack already uses it. A
script that still calls the old lookup URL must be changed to the POST form.

### Allowances find the Home Assistant integration by kind (F27)

Allowances no longer depend on the integration being named `ha`; they use the
single enabled integration whose kind is `home_assistant`. The same lookup
feeds the device picker and the HA pin.

**What the operator needs to do:** none, unless you have more than one HA
integration enabled — in that case keep exactly one enabled.

### Inventory reads Pi-hole v5 locally and Omada sources reach their tools

The Pi-hole inventory source reads the v5 local DNS records as well as the
v6 API, and the Omada source now reaches its group tools. The Inventory page
shows the same synthetic-safe host rows.

**What the operator needs to do:** re-check **Inventory** after the upgrade;
if a source was configured before, confirm it still lists what you expect.

### A scoped approver token for the E2E suite

A test agent can carry a separate approver token limited to named commands
on one gateway and to `E2E:` sessions. It cannot reach the rest of the
dashboard. The suite uses it to approve its own test rows without a human.

**What the operator needs to do:** none for normal use. The token is for the
E2E test tooling only; do not put it in an agent's normal configuration.

### Gateway 1.4 stack

The approved `release/1.4-gw` stack adds the installer-hash buckets, gated
gateway-file generations, the F19 run limits, and the BH2/BQ2/BQ4 fixes. It
also carries the SSH JIT reliability work, the uninstall-progress fix and the
gateway-side body-token claim.

**What the operator needs to do:** roll the gateways out in the approved
order (Seed Edge → dev gateways → Deploy to Fleet). Reinstalled or updated
gateways report their installer hash; a gateway that has not checked in since
the upgrade shows its last known hash until the next heartbeat.

## Upgrade notes

* **The database migrates on start.** New tables and columns are created by
  `init_db()`; no manual SQL is needed. Take the usual pre-release database
  backup anyway. Existing rows are not dropped.
* **Old allowances are pinned to the current Home Assistant at startup.** The
  first start after the upgrade reads the registry once. If HA is unreachable
  or the registry check fails, those permissions are suspended with a reason
  and are **not** stamped with the new HA pin. The startup pass runs once per
  process, not on every page load.
* **The protected-devices list starts empty.** Nothing is protected until an
  operator adds a device under **Rules for all agents**. Protecting a device
  suspends every permission that already holds it; unprotecting does not
  resume it — use **Resume** on each permission.
* **The Allowances page is replaced by the X11 Agents screens.** The API
  endpoints stay. The sidebar entry and the old page are removed; any in-app
  link that used to open Allowances now belongs under **Agents**.
* **Window tokens.** The old URL routes return 410. Update any local script
  or cron job that still uses the lookup URL. The gateway claim path used by
  1.4 gateways is already the body form.
* **Screenshots and docs.** The Stage 3 screenshots in
  `docs/screenshots/x11/` are synthetic (`192.0.2.x`, `*.example.test`) and
  contain no live data.

## Check after upgrade (on Dev, before the fleet)

Tick these off in order. Stop and roll back if any fails.

- [ ] **Agents tab** — every existing agent appears, the paused banner (if
      any) has the right count, and **Review** opens the owning agent.
- [ ] **Device grid** — open an agent → Tools → Home Assistant `call service`
      → **Runs without asking**. Rows are grouped by the real HA area,
      protected devices are locked, and a label column is read-only.
- [ ] **Check and Apply** — make one small change, press **Save changes**,
      read the preview, tick any required risk/resume box, and apply. The
      change appears after reload; Cancel changes nothing.
- [ ] **JSON editor** — open **JSON**, change one harmless field, **Check**,
      then **Apply**. Paste a bad document and confirm the server error is
      shown next to the document.
- [ ] **Window approval** — create a window from a preset, approve an agent
      request, and claim one window from a gateway. The old lookup URL must
      answer 410.
- [ ] **Inventory sources** — Pi-hole and Omada each list their expected
      hosts; no source is silently empty.
- [ ] **E2E suite** — run it against the dev dashboard and the one dev
      gateway; the scoped approver token approves only its own `E2E:` rows.
- [ ] **Upgrade one gateway** — reinstall or update one dev gateway, confirm
      it checks in, and confirm its installer hash is reported before the
      rest of the fleet is touched.

## Pending review

These sections can change before the fleet release:

* **X11 Agents and Rules screens** — operator review of the look and the six
  Stage 3 fixes; Security review of the three server follow-ups.
* **Window tokens out of URLs** — Security review of the new POST routes, the
  410s and the access-log mask.
* **HA label registry read** — Security review of the single read command.
