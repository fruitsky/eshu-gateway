# Eshu Gateway — AI Agent Manual

This manual describes how AI agents interact with the Eshu Gateway — a zero-trust SSH
command gateway with human-in-the-loop JIT (Just-In-Time) approval and pre-approved
time windows. Agent-agnostic: works with any framework that can execute `curl`, `ssh`,
and basic bash.

---

## 1. How the gateway works

**Getting access.** Your operator gives you SSH access to each host as the
`eshu-gateway` user. Authentication uses the **Eshu Gateway private key** the
operator generates and provisions to your host (see the README, *Quick Start →
Create and register the agent's SSH key*). You don't need to generate keys
yourself — you just need the private key the operator set up for you.

An Eshu Gateway intercepts every SSH command through a locked-down `eshu-gateway` user
on the target host. The SSH daemon runs the gateway script instead of a shell.

```
ssh eshu-gateway@<host> "<command>"
```

The script runs the command through a multi-stage policy pipeline:

1. **Blocklist** — matches against hardcoded + synced blocked patterns
2. **Approval-only gates** — commands that can never auto-run, whatever the
   allowlist says: shell chaining/redirection (`;` `&` `|` `` ` `` `$(` `>` `<`
   `\` or a newline), encoded/eval wrappers, and broad self-protection matches
   (any `.ssh/authorized_keys`, or a wildcard next to an `esh…` path). These go
   to JIT (stage 5) so a human sees them.
3. **Whitelist** — exact/regex match → auto-approved
4. **Feature scripts** — approved windows, future features loaded at runtime
5. **JIT lockbox** — claim-and-burn tickets from approved requests
6. **JIT human approval** — operator approves/denies via dashboard

If a command passes a stage, it executes immediately. The pipeline stops on first match.
A JIT-approved command runs only if it is byte-for-byte the command the
operator approved.

### Session & execution IDs (grouping)

An **id** is what ties one task's SSH commands and MCP calls into a single
Session in the dashboard. **Use the same id on both surfaces** — one task, one
id, never two.

> **Session contract — do this for every new task.** Call `session_begin` with
> **both** a specific human-readable `goal` (the session name) and the id you
> will use:
>
> ```json
> {"name": "session_begin", "arguments": {"goal": "Investigate pve2 NVMe overheating (P-005), read-only", "session_id": "<your chat id>"}}
> ```
>
> - `goal` is not optional in practice: it is the name the operator sees in the
>   Sessions list. Use a specific task name, not "task" or "investigation".
> - `session_id` is the client's conversation/chat id whenever one exists. If
>   there is none, omit it once and keep the minted id.
> - Then pass that id on **every** Eshu tool call and prefix **every** SSH
>   command with the exact `ssh_prefix` — both `ESHU_SESSION_ID=<id>` and
>   `ESHU_SESSION_SIG=<signature>`. Do not open a second session for the next
>   step of the same task.
> - End with `session_summary(session_id="<id>")` — the explicit id, not the
>   no-argument form.

**Every new session starts with `session_begin`.** If your client already has a
conversation id (Hermes' chat id, a cron origin id), pass it, so the chat and
the dashboard session stay the same thing:

```json
{"name": "session_begin", "arguments": {"goal": "swap the TLS certificate", "session_id": "<your chat id>"}}
```

If you have no conversation id, omit `session_id` and Eshu mints one:

```json
{"name": "session_begin", "arguments": {"goal": "swap the TLS certificate"}}
```

`goal` is required: an empty goal returns `{"error":"goal_required"}`. Either
way it answers:

```json
{"session_id": "a1b2c3d4e5f6",
 "goal": "swap the TLS certificate",
 "adopted": false,
 "session_sig": "<hmac-sha256 hex>",
 "ssh_prefix": "ESHU_SESSION_ID=a1b2c3d4e5f6 ESHU_SESSION_SIG=<hmac-sha256 hex> ",
 "how": "Pass session_id=<id> to every Eshu tool, and use the exact ssh_prefix (both keys) on every SSH command. The same id groups both."}
```

`adopted: true` means Eshu took the id you supplied. Then:

- every MCP tool takes `session_id=<id>` (and optionally an `execution_id` for
  per-run drill-down);
- every SSH command starts with the returned `ssh_prefix` — copy it exactly,
  including both keys:

  ```bash
  ssh eshu-gateway@<host> "ESHU_SESSION_ID=cf0f09869854 ESHU_SESSION_SIG=<signature> <command>"
  ```

The signature proves to the gateway that this id was minted by the dashboard
for this fleet; the gateway verifies it locally with the key from its last
policy poll. Do not construct or reuse a prefix by hand.

At the end of the task, `session_summary()` with no arguments describes **this
MCP connection's bound session** — the id from `session_begin`, or the last id
passed to an MCP tool. It does **not** see SSH commands stamped with a
different id: if SSH and MCP used different ids, pass `session_id` explicitly.
The response says which rule resolved the id (`resolved_from`: `argument`,
`connection` or `transport`) and returns `commands`, `pending`, the permitted
split (`whitelisted`, `jit_approved`, `override_approved`, `window_approved`,
`fleet_run`, `consumed`, `abandoned`), the back-compatible `approved` sum, `refused`,
`expired`, `mcp_calls`, `mcp_pending`, `gateways`, `first_seen` and
`last_seen`. `approved` means permitted, not succeeded — a command can be
approved and then fail. `consumed` means the ticket was claimed and the command
executed; `abandoned` means it was approved but never claimed before the claim
window closed; `expired` means it was never approved. `approved` is the permitted
sum and includes `abandoned`.

Rules:

- **One name and one id per task.** The `goal` is the name the operator sees;
  the id is the grouping key. Keep both for the whole task, pass the id on every
  call, and never mix a minted id with your chat id.
- **Both tools are always available**, even to an agent with a strict tool
  allowlist.
- **Omission is not an error when enforcement is off — it inherits.** After
  `session_begin`, an MCP call without `session_id` is grouped into that session
  on the same connection. Only a connection that never named a session falls
  back to its transport id (`mcp-…`, shown as `auto`). The result still carries
  `_eshu.session_id` and `_eshu.source` (`caller`, `inherited` or `transport`,
  object responses only) so you can adopt it for SSH. That id is per-connection,
  not a durable identity.
- **Strict session mode.** If the operator enables **Require sessions**, the
  dashboard refuses every MCP tool call that does not resolve to a minted,
  named Session, and the gateway refuses every SSH command without a valid
  `ESHU_SESSION_ID` + `ESHU_SESSION_SIG` pair. `session_begin` and
  `check_approval` stay available so you can recover. Old unsigned prefixes are
  refused — re-run `session_begin` and use the new `ssh_prefix`.
- **Nothing is rejected for a missing id unless strict mode is on.** Untagged
  SSH is visible to the operator in the Sessions coverage line. Coverage counts
  only explicit ids — inherited and transport ids are reported as `auto`, not
  as compliance.
- **One command, one ticket.** A session id groups commands; it does not cache
  approvals. Re-running the same command raises a new JIT. The exception is an
  operator-granted **Session Override** for that session (and host): while it
  is active, matching commands auto-approve until it expires.
- **`_eshu` is object-only.** Array-returning tools (the list endpoints) do not
  carry `_eshu`, so their absence is not evidence of missing attribution. Pass
  `session_id` explicitly if you need it, or confirm with
  `session_summary(session_id=...)`.
- **A failed command is marked.** The gateway appends `[ESHU exit=N]` to the
  output on a non-zero exit and returns that status. `[OK] Approved!` means the
  ticket was approved, not that the command succeeded.

**Checklist for every new session**

1. `session_begin(goal="<specific task name>", session_id="<chat id>")`
2. Every MCP call carries `session_id="<id>"`.
3. Every SSH command starts with the exact `ssh_prefix` (both
   `ESHU_SESSION_ID` and `ESHU_SESSION_SIG`).
4. End with `session_summary(session_id="<id>")`.
5. One name and one id for the whole task; never reuse them for another task.

The prefix is **stripped before policy evaluation** — it never changes how a
command is classified (whitelist / JIT / FATAL). Keys coexist and can be
combined in any order, including with a window token:

```bash
ssh eshu-gateway@<host> "ESHU_WINDOW_TOKEN=<token> ESHU_SESSION_ID=<id> <command>"
```

- **Prefix values are validated before anything else.** A value outside its
  allowed characters is refused with
  `[LOCKED] Invalid ESHU_<NAME> prefix value — allowed: …` and the command does
  not run:
  - `ESHU_SESSION_ID`: `[A-Za-z0-9_-]{1,64}`
  - `ESHU_SESSION_SIG`: exactly 64 lowercase hex characters
  - `ESHU_EXECUTION_ID`: `[A-Za-z0-9_.:-]{1,64}`
  - `ESHU_WINDOW_TOKEN`: `[A-Za-z0-9]{1,64}`
- `<id>` is an opaque `[A-Za-z0-9_-]{1,64}` string. `unknown` marks a context-less
  run (fire-and-forget cron) — the tickets are kept but left ungrouped.
- Commands that share the same `ESHU_SESSION_ID` are grouped into one
  collapsible Session in the operator's queue.

> **Subagents / cron:** resolve the id client-side before invoking ssh — e.g.
> `ESHU_SESSION_ID="${PARENT_CHAT_ID:-${CRON_ORIGIN_CHAT_ID:-unknown}}"` so a
> subagent inherits its parent's session and cron jobs fall back to `unknown`.

### MCP calls (integrations) — session IDs

Tool calls over MCP (the `Integrations` surface — Proxmox, Home Assistant, Omada,
etc.) group with your SSH commands using the **same** session fields. Each Eshu
tool's input schema exposes two optional parameters, `session_id` and
`execution_id`; include them alongside the tool's real arguments:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "proxmox_get_vm_status",
    "arguments": {
      "node": "pve",
      "vmid": 109,
      "session_id": "cf0f09869854",
      "execution_id": "20260829_201145_02deab"
    }
  }
}
```

Or over plain HTTP against the `/mcp` endpoint:

```bash
curl -s http://<dashboard>:8000/mcp \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {
      "name": "proxmox_get_vm_status",
      "arguments": {
        "node": "pve", "vmid": 109,
        "session_id": "'"$SESSION_ID"'",
        "execution_id": "'"$EXECUTION_ID"'"
      }
    }
  }'
```

Rules:

- `session_id` / `execution_id` are **optional**; omit them only for genuinely
  context-less calls. Without one, the call is grouped under the MCP connection's
  transport session and its result carries `_eshu.session_id` so you can reuse it.
- They are accepted as **tool arguments** (snake_case names) because that is what
  your client sends — the dashboard strips them before the call and they **never
  reach the upstream API**.
- `<id>` is the same opaque `[A-Za-z0-9_-]{1,64}` string you use in the SSH prefix.
  Reuse the **same** `session_id` across SSH **and** MCP calls in one conversation
  so they collapse into a single Session. Send `unknown` for fire-and-forget runs.
- **Subagents:** resolve the id client-side first and pass it down — e.g. use your
  parent conversation/chat id — so a subagent's MCP calls join the parent's Session.
- Read-only tools run immediately; mutating tools return a pending `id` — poll
  `check_approval(call_id)` until the operator approves or denies. `check_approval`
  serves **MCP integration** calls only; SSH gateway JIT ids are a separate
  sequence — use `check_request(id)` for those (see §4).
- `check_approval` and `check_request` are always available, even under a strict
  tool allowlist, so an agent can always recover.

---

## 2. Command lifecycle

```
Agent runs ──▶ ssh eshu-gateway@host "<cmd>"
                   │
                   ▼
           -1. Invalid ESHU_* prefix value? ──yes──▶ ❌ Rejected (see prefix rules)
                   │no
                   ▼
            0. Strict session? ──no──▶ ❌ Rejected — call session_begin
                   │yes / strict off
                   ▼
            1. Blocklisted? ──yes──▶ ❌ Rejected — report to operator
                   │no
                   ▼
            1.5 Encoded payload, shell chaining/redirection, or broad
                self-protection match? ──yes──▶ Force JIT
                   │no                (never auto-approved by allowlist or
                   ▼                   prefix window; exact windows still apply)
            2. Whitelisted? ──yes──▶ ✅ Auto-approved — executed
                   │no
                   ▼
            3. Window token present? ──yes──▶ Token valid?
                   │no                     │yes
                   ▼                       ▼
            4. JIT ticket? ──yes──▶ Execute (claim & burn)
                   │no
                   ▼
            5. Submit JIT request ──▶ Operator decides
```

### Encoded commands

Do not wrap a command in `base64 -d`, `eval`, `bash -c "$(...)"` or a similar
decoder to get it past the policy. Those wrappers are detected and **forced to
JIT regardless of allowlists or windows**; the dashboard decodes the payload,
scans the decoded text against the blocklist before creating a ticket, and shows
the operator a **decoded** preview next to the wrapper. If the decoded text
matches a blocked pattern, the request is refused.

Prefer readable commands. For multi-step work, send separate SSH invocations
(each with the same session prefix) so each step is visible, approved and
attributable.

---

## 3. Auto-approved commands

Commands in the whitelist files are auto-approved **unless** an approval-only
gate applies: shell chaining/redirection (`;` `&` `|` `` ` `` `$(` `>` `<` `\`
newline), an encoded/eval wrapper, a broad self-protection match, or a
Zero-Trust gateway. So `uptime` auto-runs, but `uptime; whoami` and
`dmesg | grep -i usb` go to JIT even if a rule seems to cover them. Send one
plain command per SSH call when you want the allowlist to apply.

> **Operator pre-flight only (2026-11 hardening).** `/api/policies/test` now
> requires a dashboard session; an agent bearer token is not accepted. Ask the
> operator to run **Controls → Tester**, or simply send the command — the
> gateway auto-runs allowlisted commands and turns everything else into a JIT
> request. The Tester's response shape is:

```json
{
    "command": "uptime",
    "matched": true,
    "action": "auto_approved",
    "details": [{"type": "exact_whitelist", "matched_line": "uptime", "match": true}],
    "risk": null,
    "dry_run": null
}
```

`action` is one of:

| `action` | Meaning |
|----------|---------|
| `auto_approved` | Whitelisted — will execute without operator approval |
| `blocked` | Rejected — either the dashboard blocklist (includes the shipped command-safety patterns) or the **hardcoded FATAL tier** (`tier: "fatal"` — Eshu self-protection / `$(which`-evasion, e.g. `cat /etc/eshu-freeze`) |
| `jit` | Will require operator approval — no rule matched, or an approval-only gate applies (`detail_type: "shell_meta"` for chaining/redirection, `"self_protection_jit"` for broad self-protection matches) |

Use this to pre-flight a command. `tier: "fatal"` means the gateway hard-blocks
it and it can never run — don't attempt it. Commands like `rm -rf` and `reboot`
are blocked too, but via the blocklist (which the operator can relax), so they
report `action: "blocked"` without the fatal tier. If whitelisted, the command
passes stages 1-4 and is auto-approved. If not, it falls through to JIT (stage 5).

> **Zero-Trust gateways:** on a gateway with **Zero-Trust** enabled (the operator marks
> it in the dashboard), even *whitelisted* commands are **not** auto-approved — everything
> routes to JIT, so expect an operator approval request instead of instant execution.
> Approved-window tokens still auto-run (they were pre-approved by the operator).

> **Policy membership check (`/api/policies/check`):** the operator's Tester uses a
> separate endpoint to ask "is this command already in a policy list?" — it returns
> `in_exact_whitelist` / `in_regex_whitelist` / `in_regex_blacklist` booleans. It is
> **dashboard-session only**, as is `/api/policies/test`; agents cannot run either.

---

## 4. Standard JIT flow

Submit a command for human approval:

```bash
ssh eshu-gateway@host "apt update"
```

If not auto-approved or covered by a window token, the gateway returns:

```
[LOCKED] Command blocked. JIT request #000XXX sent to Dashboard.
   Auto-polling for approval (up to 90s)...
```

The `#000XXX` is an **SSH JIT id** in the gateway's `requests` namespace. If the
SSH client dies before the result arrives, call `check_request(XXX)` with the
integer (drop the padding) to read its state: `pending`, `approved`, `consumed`,
`denied`, `blocked`, `expired` or `abandoned`. Do **not** pass it to
`check_approval` — that tool serves MCP integration approvals and will answer
`wrong_namespace`. The gateway only executes after the dashboard records the
ticket claim, so an executed command can no longer look merely "approved".

The gateway handles all status polling internally — the agent only needs to wait for the SSH result.

### Handling timeouts

The polling loop times out after 90 seconds. If it times out, the command was not
approved in time. This is normal — the operator may be away. You can reschedule.

---

## 5. Approved Windows

Approved Windows let the operator pre-authorize a command to run during a scheduled time
window **without JIT** — for predictable workflows like cron jobs, nightly updates, and
periodic diagnostics. There are two ways a window becomes available:

1. **Operator-created** — created in the dashboard; the operator gives you the token.
2. **AI-requested** — you submit a request and a human operator approves it.

### Request a window (AI-requested)

`POST /api/window-requests` (agent bearer token or gateway token; `Content-Type: application/json`):

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `gateway_ip` | string | required | The gateway IP this window applies to (`TARGET_IP`). |
| `command` | string | required | Exact or prefix command to pre-approve. |
| `match_type` | string | `"exact"` | `"exact"` or `"prefix"` (prefix matches any command starting with `command`). |
| `days_of_week` | int | `0` | Bitmask of allowed UTC days (`0` = every day); see the bitmask table below. |
| `execution_time` | int | `0` | Minutes since midnight UTC. Non-zero = recurring schedule. |
| `window_start` | int | `0` | Epoch UTC when a **single-use** window activates (required when `days_of_week=0` and `execution_time=0`). |
| `expires_at` | int | `null` | Epoch UTC when the window expires (`null` = never). |
| `max_executions` | int | `0` | Max token uses (`0` = unlimited). |
| `label` | string | `""` | Optional label. |

Rules: **recurring** = `days_of_week` + `execution_time` (UTC); **single-use** = `window_start`
epoch. An **immediate one-off** should use standard JIT approval, not a window.

Example — nightly apt update:

```bash
curl -s -X POST "$DASHBOARD_URL/api/window-requests" \
  -H "Authorization: Bearer $ESHU_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "gateway_ip": "'"$TARGET_IP"'",
    "command": "apt update && apt upgrade -y",
    "match_type": "exact",
    "days_of_week": 0,
    "execution_time": 150,
    "label": "Nightly apt update"
  }'
# → {"id": 42, "retrieval_key": "dG9rZW5tZWlzdGVy…", "status": "pending_review"}
```

### Poll for approval

`GET /api/window-requests/{retrieval_key}` — read-only, no auth. Use the opaque
`retrieval_key` returned by the request (not the numeric `id` — the numeric id is
only honoured for the session-authed UI, and is not usable to retrieve tokens).
Poll until the status changes:

```bash
for i in $(seq 1 120); do
  STATUS=$(curl -s "$DASHBOARD_URL/api/window-requests/$RETRIEVAL_KEY" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('status','pending'))")
  if [ "$STATUS" = "approved" ]; then
    TOKEN=$(curl -s "$DASHBOARD_URL/api/window-requests/$RETRIEVAL_KEY" | \
      python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")
    echo "TOKEN=$TOKEN"
    break
  fi
  if [ "$STATUS" = "denied" ]; then
    echo "Denied — adjust parameters and re-request."
    exit 1
  fi
  sleep 3
done
```

The token is returned only at approval: `{"status": "approved", "token": "..."}` — store it securely.

### List windows & inspect one (read-only; agent/gateway token or session)

```bash
curl -s -H "Authorization: Bearer $ESHU_AGENT_TOKEN" "$DASHBOARD_URL/api/approved-windows" | python3 -m json.tool  # all windows (token omitted in the list)
curl -s "$DASHBOARD_URL/api/approved-windows/$RETRIEVAL_KEY" | python3 -m json.tool  # full record + token by opaque key
curl -s "$DASHBOARD_URL/api/window-by-token/<TOKEN>" | python3 -m json.tool    # parameters for an existing token
```

Creating/editing/toggling windows and approving/denying requests remain **operator-only**.

### Operator-created windows

If the operator pre-configures a window and hands you the token, use
`/api/window-by-token/<TOKEN>` to read its command/schedule/remaining executions, then run
it on schedule:

```bash
ssh eshu-gateway@host "ESHU_WINDOW_TOKEN=<token> <command>"
```

### Use the token

SSH forced commands strip environment variables, so embed the token at the **start of the
command string**:

```bash
ssh eshu-gateway@host "ESHU_WINDOW_TOKEN=aB3dEfGhIjKlMnOpQrStUvWx docker logs -n 50 my-app"
# from a cron job on the gateway itself:
ESHU_WINDOW_TOKEN="aB3dEfGhIjKlMnOpQrStUvWx" /usr/local/bin/eshu-gateway bash -c "docker logs -n 50 my-app"
```

Window tokens are 24 letters/digits (older 10-character tokens keep working).

The gateway validates the token, checks expiry + schedule + command match, notifies the
dashboard to increment the execution counter, and auto-disables the window once
`execution_count >= max_executions`. **Do not call `/api/approved-windows/execute/{token}`
yourself** — the gateway does it. If the dashboard answers that claim with anything
other than success, the window is rejected; only when the dashboard cannot be reached
at all does the gateway fall back to its local window cache.

A **prefix** window never approves a command that contains shell chaining or
redirection (`;` `&` `|` `` ` `` `$(` `>` `<` `\` newline) — the attempt is a
window reject. An **exact** window runs only the exact string stored in the
window. If that stored string itself contains chaining (for example
`apt update && apt upgrade -y`), it runs as stored; adding anything to it (such
as `; id`) is a `command mismatch` reject.

If a command is also matched by an allowlist rule, the allowlist runs it first
and the window is not used (its execution count does not change). Use windows
for commands that are not already allowlisted.

### Rejected windows (hard reject)

If a token is presented but fails validation — wrong time/day, wrong gateway, command
mismatch, expired, exhausted, or **no windows configured on that host** — the attempt is a
**hard reject**. You get `[LOCKED] Window rejected: <reason>` and exactly **one**
`Window Rejected` ticket appears in the operator's queue (reason visible on hover). **No JIT
ticket is created** and the command does **not** run. To proceed, use a valid window token
or request standard JIT approval **without** a token.

### Tolerance & time zones

- `execution_time` is **UTC minutes since midnight**; the gateway allows an
  **inclusive** `[execution_time − 2, execution_time + 2]` minute window (i.e.
  from 2 minutes before to 2 minutes after the target time). This tolerance is
  currently fixed and not configurable — schedule your cron within that slot,
  and account for scheduler jitter/retries (configurable window duration is a
  planned enhancement).
- Single-use `window_start` is a **UTC epoch**; valid from then until `expires_at` (or indefinitely).

### `days_of_week` bitmask

| Day | Bit | | Day | Bit |
|-----|-----|---|-----|-----|
| Monday | 1 | | Friday | 16 |
| Tuesday | 2 | | Saturday | 32 |
| Wednesday | 4 | | Sunday | 64 |
| Thursday | 8 | | | |

Weekdays = `31` (`1+2+4+8+16`) · weekends = `96` (`32+64`) · every day = `0`.

---

## 6. Rate limits

The dashboard enforces a sliding-window rate limit of **60 requests per 60 seconds**
per source IP (or per gateway) on the gateway and agent endpoints (request,
register, heartbeat, window-requests, poll, window lookups). If exceeded:

```
429 Too many requests. Slow down.
```

Wait a few seconds and retry with exponential backoff.

---

## 7. Error handling & recovery

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| `Command blocked` + JIT | Not whitelisted, no window token | Wait for operator approval, or request a window |
| `WINDOW TOKEN` + `[LOCKED] Window rejected: <reason>` | Token presented but invalid/expired/mismatch | Check window start time, target gateway, command match — no JIT is created for a rejected window |
| `Invalid or expired window token` | Token exhausted, expired, or wrong cache | Re-request window or use JIT |
| `[LOCKED] Window rejected: window claim failed (HTTP <code>)` | The dashboard refused the window claim (disabled, exhausted, rate-limited, error) | Do not retry in a loop — use JIT or ask the operator |
| `[LOCKED] Invalid ESHU_<NAME> prefix value` | A prefix value has characters outside its allowed set | Fix the value (see the prefix rules in §1); re-run `session_begin` for a fresh `ssh_prefix` |
| `[LOCKED] Approved command does not match the requested command` | The approved ticket was for a different command | Do not retry — report to the operator |
| An allowlisted command went to JIT | It contains chaining/redirection, an encoded wrapper, a broad self-protection match, or the gateway is Zero-Trust | Split it into plain single commands, or wait for approval |
| `Cannot connect to dashboard` | Dashboard unreachable | Retry later. Gateway falls back to last synced policy |
| `429 Too many requests` | Rate limited | Wait a few seconds, then retry |
| `script_not_found` error | Gateway script missing | Contact operator |
| JIT times out (90s) | Operator didn't respond | Reschedule for when operator is available |
| `Host key verification failed.` (no `[LOCKED]`) | SSH transport failure before the gateway ran (unknown host key, network, auth) | Fix the client's known_hosts/connection; this is not a policy refusal. Only gateway output carries `[LOCKED]` |
| JIT id unqueryable after the client disconnects | Wrong tool | Use `check_request(<integer>)` for SSH JIT ids; `check_approval` is MCP-only |
| `Window request denied` | Operator denied request | Adjust parameters (time, command, label) and submit new request |

### Retry policy

1. On transient errors (network, rate limit): wait 5s, retry, with exponential backoff
   up to 60s max.
2. On auth errors (401/403): STOP retrying — report to operator.
3. On validation errors (400/404): STOP — check parameters and fix the request.

---

## 8. Quick reference — API endpoints

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/api/policies/test?command=` | GET | Pre-flight a command — returns `action` (auto_approved / blocked / jit) + `tier: "fatal"` for the non-relaxable self-protection / evasion blocklist | Session |
| `/mcp` | POST/GET | MCP streamable-HTTP endpoint — integration tool calls (auth: `Authorization: Bearer <agent-token>`; read-only auto-runs, mutations poll `check_approval`) | Agent token |
| `/api/window-requests` | POST | Submit window request | Agent or gateway token |
| `/api/window-requests/{retrieval_key}` | GET | Poll window request status + token (read-only) | Public |
| `/api/approved-windows` | GET | List windows (full status; token omitted for non-session callers; a gateway token sees only its own) | Agent/gateway token or session |
| `/api/approved-windows/{retrieval_key}` | GET | Single window details + token (read-only) | Public (read) |
| `/api/window-by-token/{token}` | GET | Parameters for an existing token (token is the auth) | Public |
| `/api/docs/agent-windows` | GET | Alias for this manual (full guide incl. Approved Windows) | Public |
| `/api/docs/agent-manual` | GET | This manual | Public |

---

## 9. Complete example workflow

This example shows the full flow: request a window and use it. (Pre-flight is
operator-only; if you are unsure whether a command is allowlisted, just send
it — the gateway auto-runs it or creates a JIT request.)

```bash
#!/bin/bash
# Example: AI agent deploying a scheduled cleanup
DASHBOARD="http://192.168.1.100:8000"
GATEWAY="192.168.1.201"
COMMAND="docker system prune -f --volumes"
ESHU_AGENT_TOKEN="${ESHU_AGENT_TOKEN:?set the agent token}"

# 1. Request a window for tomorrow at 03:00 UTC
TOMORROW=$(date -u -d "+1 day 03:00:00" +%s)
echo "Requesting approved window for $TOMORROW..."
WINDOW_RESPONSE=$(curl -s -X POST "$DASHBOARD/api/window-requests" \
    -H "Authorization: Bearer $ESHU_AGENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"gateway_ip\":\"$GATEWAY\",\"command\":\"$COMMAND\",\"match_type\":\"prefix\",\"window_start\":$TOMORROW,\"max_executions\":1,\"label\":\"Scheduled cleanup\"}")
RETRIEVAL_KEY=$(echo "$WINDOW_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('retrieval_key',''))")

if [ -z "$RETRIEVAL_KEY" ]; then
    echo "⚠️  Window request failed — falling back to JIT"
    ssh eshu-gateway@"$GATEWAY" "$COMMAND"
    exit $?
fi

echo "🪟 Window request submitted — waiting for operator approval..."

# 2. Poll by retrieval_key (read-only, no auth) for up to 5 minutes
for i in $(seq 1 100); do
    STATUS=$(curl -s "$DASHBOARD/api/window-requests/$RETRIEVAL_KEY" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "pending")
    if [ "$STATUS" = "approved" ]; then
        TOKEN=$(curl -s "$DASHBOARD/api/window-requests/$RETRIEVAL_KEY" | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
        echo "🔑 Token received: $TOKEN"
        # 3. Schedule the cron-job
        echo "$TOMORROW ESHU_WINDOW_TOKEN=$TOKEN ssh eshu-gateway@$GATEWAY \"ESHU_WINDOW_TOKEN=$TOKEN $COMMAND\"" | at -t $(date -d "@$TOMORROW" +%Y%m%d%H%M) 2>/dev/null || true
        echo "✅ Scheduled — command will run at 03:00 UTC with the window token."
        echo "   The gateway auto-approves it without JIT."
        break
    elif [ "$STATUS" = "denied" ]; then
        echo "❌ Window request denied. Adjust parameters and re-request."
        exit 1
    fi
    sleep 3
done
```
