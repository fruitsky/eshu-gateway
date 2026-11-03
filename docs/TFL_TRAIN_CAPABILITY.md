# TfL train capability (`tfl` integration)

Eshu owns the train-planning capability. Any MCP client can use it; no harness
code, no API key, and no TfL JSON reaches the model.

## Endpoints and tool names

| Surface | Tool |
|---|---|
| Shared `/mcp` | `tfl_train_plan`, `tfl_train_status` |
| Standalone `/mcp/tfl` | `train_plan`, `train_status` |

The `tfl` integration is created automatically at startup if it does not exist,
and its catalog is reseeded on every start. It has no generic passthrough —
only the curated tools below. Disable the integration to hide it; deleting it
will recreate it at the next start.

## `train_plan`

| Param | Type | Notes |
|---|---|---|
| `origin` | string | Station name or NaPTAN id. Required. |
| `destination` | string | Station name or NaPTAN id. Required. |
| `time` | string | `HH:MM`, Europe/London. Required. |
| `arrive_by` | boolean | `true` (default) = be there by `time`; `false` = leave after `time`. |
| `date` | string | `YYYY-MM-DD`, `today`, `tomorrow`; default today. |
| `limit` | integer | 1–5, default 3. |

Returns JSON with a short `text` field, station names/NaPTANs, and the ranked
options. Relay `text`. If `ok` is false, relay `message` and ask the question it
raises (e.g. which station).

### What it does

- Resolves station names, including London hubs
  (`Cannon Street` → `910GCANONST`, `London Bridge` → `910GLNDNBDC`,
  `Waterloo` → `910GWATRLMN`).
- Returns structured candidates for genuinely ambiguous names (e.g. the three
  Bromley stations) instead of guessing.
- Fetches the TfL Journey Planner response server-side, then ranks:
  - **arrive by:** fewest changes, then shortest duration, then latest departure.
  - **leave after:** earliest arrival, then fewest changes.
- Uses **actual** departure/arrival times. A delayed service is shown as
  `13:39 → 13:45 ... — 8 min late (was 13:31)`.
- Never invents a platform number (TfL does not provide one).
- If nothing meets the deadline, returns the earliest available and says so.
- If TfL is unreachable, returns an explicit error — never a made-up train.

## `train_status`

| Param | Type | Notes |
|---|---|---|
| `lines` | string | Comma-separated TfL line ids; default `southeastern`. |

Returns `text` with the current status per line and any disruption reason. URLs
in the reason are moved to a `Detail:` line so they don't break the sentence.

## Conversation rules (for any agent)

1. **Ask only for missing slots** — origin, destination, time.
2. **Ask which station** when the tool returns `station_ambiguous`; do not pick
   one yourself.
3. **Infer the mode from the wording** — "be there by / need to be in X at T"
   → `arrive_by=true`; "leaving at / after" → `arrive_by=false`. If it is
   genuinely ambiguous, ask.
4. **Default to today** only when the user said "today" or gave no date. If the
   requested time has already passed, say so and ask whether they mean
   tomorrow.
5. **Relay the tool's text**, not the raw JSON. Do not add platform numbers,
   guesses, or "probably fine" assurances.
6. **No profile lookup is needed** for ad-hoc journeys. The tool is read-only
   and stateless.

## Example

> "I need to be in Petts at 13:30 today."

1. The agent asks: "Which station are you starting from?"
2. User: "Chelsfield."
3. Call:

```json
{"origin": "Chelsfield", "destination": "Petts", "time": "13:30",
 "arrive_by": true, "date": "today"}
```

4. The tool returns text like:

```
Chelsfield Rail Station → Petts Wood Rail Station, arriving by 13:30:
1. 13:01 → 13:11 (10 min, rail, direct)
2. 13:04 → 13:27 (23 min, bus + rail, 1 change) — latest that makes it
3. 12:44 → 13:08 (24 min, bus + rail, 1 change)
```

## Enabling it for an agent

In Eshu, add `tfl_train_plan` and `tfl_train_status` (or the standalone
`/mcp/tfl` endpoint) to that agent's tool allowlist. The shared endpoint uses
the namespaced names; the standalone endpoint uses the short names.
