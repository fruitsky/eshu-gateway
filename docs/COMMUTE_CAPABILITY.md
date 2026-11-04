# Commute profiles and notifications (`commute` integration)

Eshu owns the commute profiles, the check logic, the notify slots, and the
deduplication state. The harness only triggers a scheduler and delivers text.
There is no approval queue for profile edits: they are validated, audited, and
scoped to the household agent.

## MCP tools

| Tool | Type | Notes |
|---|---|---|
| `commute_check` | read-only | Live check for one person; `person`, optional `at` (HH:MM) |
| `commute_profile_get` | read-only | Read-back text + profile object |
| `commute_profile_set` | ungated write | `person`, `patch`; applies immediately, returns stored read-back |
| `commute_override` | ungated write | `person`, `date`, `action` (`skip`/`office`/`clear`), optional `arrive_by` |
| `commute_pause` | ungated write | `person`, optional `until` (`YYYY-MM-DD` or `clear`) |

Shared `/mcp` names are `commute_check`, `commute_profile_get`, etc.;
`/mcp/commute` exposes `check`, `profile_get`, `profile_set`, `override` and
`pause` un-namespaced.

`commute_profile_set` also creates a profile when the person does not exist, if
the required fields (`from`, `to`, `days`, `arrive_by`, `usual_train`,
`notify_times`) are present. Other fields default.

Read-only profile endpoints for the gate/UI:

```text
GET /api/commute/profiles
GET /api/commute/profile/{person}
```

Both accept a dashboard session or one of the commute agent tokens
(`rowana-household`, `hermes`, `commute-cron`). No chat ids are stored. Write tools are `principal_allow: ["rowana-household"]`.

## Scheduler endpoint

`POST /api/commute/due` with an agent bearer token (`rowana-household` or
`hermes`):

```json
{"person": "megan", "at": "05:30", "dry_run": false}
```

Returns `{send, slot, text, ...}`. Eshu checks the day gate, notify times and
`commute_notify_state`; it records the slot only when it returns a message.
The harness prints `text` when `send` is true. `dry_run` composes without
recording (shadow testing).

`date` is accepted only with `dry_run=true`, for future-day acceptance tests:

```json
{"person": "megan", "at": "05:30", "date": "2026-11-05", "dry_run": true}
```

Live scheduling always uses the current day. A real send at a slot marks it sent,
so the one-minute grace window cannot produce a duplicate production message.

The preferred host-side trigger is the generic `eshu_call.py` client plus a
tiny shim:

```python
# commute_due_megan.py
import sys
from eshu_call import main
raise SystemExit(main([
    "--path", "/api/commute/due",
    "--body", '{"person": "megan"}',
    "--print-field", "text",
    "--when-field", "send",
]))
```

Set `ESHU_URL` and `ESHU_TOKEN_FILE` in the shim environment. See
`docs/ESHU_CALL_CLIENT.md` for install/version/hash details. The older
`scripts/commute-due.py` remains as a compatibility path:

```bash
ESHU_URL=http://192.168.1.114:8000 ESHU_TOKEN=... \
    python3 scripts/commute-due.py --person megan

# or keep the token in a 0600 file instead of the environment:
python3 scripts/commute-due.py --person megan --token-file /path/to/eshu-token
```

## Profile shape

```json
{
  "display_name": "Megan",
  "from": {"name": "Chelsfield Rail Station", "naptan": "910GCHLSFLD"},
  "to": {"name": "London Bridge Rail Station", "naptan": "910GLNDNBDC"},
  "days": ["tue", "thu"],
  "arrive_by": "07:30",
  "usual_train": "06:46",
  "never_before": "06:46",
  "notify_times": ["05:30", "06:00"],
  "warn_minutes": 5,
  "lines": ["southeastern"],
  "alerts_only": false,
  "overrides": {},
  "paused_until": null
}
```

- `notify_times` is sorted and must be before `usual_train`, within 04:00–10:00
  local.
- `notify_at` is accepted as a legacy alias and mapped to `[notify_at]`.
- `overrides` maps a date to `"skip"`, `"office"`, or an object with
  `arrive_by`/`usual_train`/`notify_times`.
- No chat ids are stored in Eshu. Delivery targets stay in the harness.

## Messages

- The first `notify_times` slot is labelled `Early check (HH:MM)`.
- The last slot is labelled `Final check (HH:MM)` and includes the service
  immediately before the usual train if it is late:
  `The 06:31 is running 5 min late.`
- `alerts_only` suppresses a normal proactive message but does not hide the
  text from an ad-hoc `commute_check`.
- `commute_check` on a non-commute day returns an explanation; after the usual
  train has gone it says so and points at `train_plan` for alternatives.

## Importing the old profiles

```bash
python3 scripts/import-commute-profiles.py /path/to/profiles.json
python3 scripts/import-commute-profiles.py /path/to/profiles.json --dry-run
```

The importer drops `chat_id`, maps `notify_at`, validates every profile, and
only writes after all profiles have passed validation.

## Scheduler token

Use the existing `rowana-household` token from her MCP config, or create a
dedicated `commute-cron` token in the Eshu Agents tab and set its MCP policy to
`allowlist` with no tools. Store the raw token in a mode-600 file and pass it
with `--token-file`; it is only accepted by `/api/commute/due`.

## Scheduler schedule

Run the trigger every minute through the morning window, e.g.:

```text
* 3-10 * * 1-5
```

Every-minute is deliberate: existing notify times such as 06:16 are preserved
exactly. Silent runs call Eshu but perform no TfL fetch.
