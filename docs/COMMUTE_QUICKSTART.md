# Commute capability — fresh-clone quickstart

Eshu owns the commute profiles, checks, notify slots and dedupe state. The agent
host owns the scheduler trigger, token file and delivery target.

## 1. Deploy Eshu

Start the dashboard as usual. On startup Eshu auto-provisions:

- `tfl` (train planning)
- `commute` (profiles/checks/notifications)

Both are `NO_GENERIC_KINDS`; only curated tools are exposed.

## 2. Create an agent token

In **Agents**, create a token for the agent that will use the tools, and add the
tools you want to its allowlist:

- `commute_check`
- `commute_profile_get`
- `commute_profile_set`
- `commute_override`
- `commute_pause`

For a scheduler-only token, create `commute-cron`, set its MCP policy to
**allowlist with no tools**, and keep its raw token in a mode-600 file.

## 3. Create a commute profile

A fresh database has no profiles. `commute_profile_set` creates one when the
person does not exist if you provide the required fields:

```json
{
  "person": "megan",
  "patch": {
    "display_name": "Megan",
    "from": {"name": "Chelsfield Rail Station", "naptan": "910GCHLSFLD"},
    "to": {"name": "London Bridge Rail Station", "naptan": "910GLNDNBDC"},
    "days": ["tue", "thu"],
    "arrive_by": "07:30",
    "usual_train": "06:46",
    "notify_times": ["05:30", "06:00"]
  }
}
```

Defaults: `never_before = usual_train`, `warn_minutes = 3`,
`lines = ["southeastern"]`, `alerts_only = false`, `overrides = {}`,
`paused_until = null`.

For an existing profile, pass only the fields you want to change. Changes apply
immediately, are validated, audited, and return the stored read-back.

## 4. Read the profile from a tool or the gate

Read-only, agent-token or dashboard-session authenticated:

```bash
GET /api/commute/profiles
GET /api/commute/profile/megan
```

The response includes the profile and a compact `text` summary. No chat ids are
stored in Eshu.

## 5. Install the generic host client

Fetch the versioned artifact from Eshu and verify the hash:

```bash
curl -fsS \
  -H "Authorization: Bearer $ESHU_TOKEN" \
  -D /tmp/eshu-call.headers \
  -o /tmp/eshu_call.py \
  http://<eshu-host>:8000/api/clients/eshu-call.py

grep -i x-client- /tmp/eshu-call.headers
sha256sum /tmp/eshu_call.py
```

Install it beside the agent profile's scripts, mode 755.

## 6. Create a scheduler shim

```python
#!/usr/bin/env python3
import sys
from eshu_call import main

raise SystemExit(main([
    "--path", "/api/commute/due",
    "--body", '{"person": "megan"}',
    "--print-field", "text",
    "--when-field", "send",
]))
```

Set `ESHU_URL` and `ESHU_TOKEN_FILE` in the scheduler environment. For a shadow
run, add `"dry_run": true` to the body.

## 7. Schedule and shadow

Run the shim every minute through the morning window, e.g.:

```text
* 3-10 * * 1-5
```

`eshu_call.py` prints only when `send` is true. Eshu performs the TfL fetch only
at a due slot and records `commute_notify_state` for real sends.

Before cutover, run with `dry_run: true` and compare against the local
`commute.py` at the same minute. Use Eshu’s profile/days as the schedule source,
not the endpoint’s own `send` value, so the gate is not self-confirming.

## 8. Cut over

Switch the delivery jobs from the local `commute.py` shims to the Eshu shims:

- remove `dry_run`
- keep the same WhatsApp/chat delivery targets
- keep the old jobs paused/available as fallback
- verify the next real slot

After cutover, `commute_profile_set` changes in Eshu directly affect delivered
messages.

## Read-only/dry-run test helper

`POST /api/commute/due` accepts `date` only when `dry_run=true`:

```json
{"person": "megan", "at": "05:30", "date": "2026-11-05", "dry_run": true}
```

Use it for future-day acceptance tests; live scheduling always uses the current
day.
