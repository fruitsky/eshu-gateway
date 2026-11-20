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

Opening an agent gives **Overview, Tools, SSH, Websites, Activity and
JSON**, one section at a time from the left menu. Deep links such as
`#agent/3/tools` open the same section. Revoke and Delete sit in a **Danger
zone** at the bottom of Overview, each behind its own confirmation.

Tools are grouped by integration and collapse to a "tools · ask first"
summary, with search and an "Only allowed" filter. A tool that always asks
(for example Home Assistant `call_service`) disables "Runs without asking"
and points at the device picker instead. Home Assistant device permissions
are edited by friendly name in a grid grouped by device type and real area;
protected devices show one **Protected** line, and a column can follow a
Home Assistant label instead of individual devices. At phone width each
device becomes a card with its own toggles, grouped by room.

Every save is **Check → preview → Apply**. The preview lists only the
sections that changed, uses device and action names, and separates **New or
changed** risky items from **Already allowed, confirm again**. Apply stays
disabled until the required boxes are ticked and reads **Allow and save**
when a risk confirmation is part of it. A stale screen is refused and
reloaded instead of overwriting a newer change. The JSON section's Apply is
disabled until Check passes and again after any edit.

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

### Window lookup leaves the URL; the legacy claim is kept and audited
(pending Security)

The public `/api/window-by-token/<token>` lookup is retired with **410
Gone**; use `POST /api/window-by-token` with `{"token": "..."}`. The old
response never echoes the token.

The pre-BJ2 **claim** path
`POST /api/approved-windows/execute/{token}` is **not** retired in 1.4. It
still works for older gateways, uses the same gateway-token and target-IP
checks as the body form, and writes one audit row per use with only the
token fingerprint and gateway IP. It will be retired at the X9 legacy
cut-over, once no such audit row has appeared for 7 days.

Window execution history now stores only a token fingerprint; existing rows
are migrated on start and the old raw column is blanked. The retrieval-key
read returns `token_fp`, not the raw token.

**What the operator needs to do:** change any local script or cron job that
still calls the lookup URL to the POST form. Older gateways can keep
claiming through the path until they are upgraded; their use is counted in
the audit log.

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
* **Window tokens.** The old lookup URL returns 410; update any local script
  or cron job. The pre-BJ2 claim path still works and is audited, so older
  gateways do not lose their windows during the fleet upgrade.
* **Proxy logs.** If a reverse proxy fronts the dashboard, it will still log
  a path token from an older gateway until that gateway is upgraded. The
  dashboard's own access log masks it, but the proxy's log is outside its
  control. Retire the path claim at the X9 cut-over.
* **Gateway logs.** A gateway keeps printing the first 8 characters of the
  token in its own log lines until its installer is re-run. The fleet
  rollout re-runs the installer, which fetches the fixed feature file and
  switches those lines to `token_fp`, the same fingerprint the dashboard
  uses. Prefixes already in old syslog stay there.
* **Screenshots and docs.** The Stage 3 screenshots in
  `docs/screenshots/x11/` are synthetic (`192.0.2.x`, `*.example.test`) and
  contain no live data.

## Check after upgrade (on Dev, before the fleet)

Tick these off in order. Stop and roll back if any fails.

- [ ] **Agents tab** — every existing agent appears, the paused banner (if
      any) has the right count, and **Review** opens the owning agent.
- [ ] **Device grid** — open an agent → Tools → Home Assistant `call service`
      → **Runs without asking → pick devices**. Rows are grouped by device
      type and real HA area, protected devices show one Protected line, and
      a label column is read-only.
- [ ] **Check and Apply** — make one small change, press **Save changes**,
      read the preview, tick any required risk/resume box, and apply. The
      change appears after reload; Cancel changes nothing.
- [ ] **JSON editor** — open **JSON**, change one harmless field, **Check**,
      then **Apply**. Paste a bad document and confirm the server error is
      shown next to the document.
- [ ] **Window approval** — create a window from a preset, approve an agent
      request, and claim one window from a gateway. The old lookup URL must
      answer 410; a claim through the legacy path must still succeed and
      appear once in the audit log.
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
* **Window tokens out of URLs** — Security review of the new POST route, the
  retired lookup, the kept-and-audited claim path and the access-log mask.
* **HA label registry read** — Security review of the single read command.
