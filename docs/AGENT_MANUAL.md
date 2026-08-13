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
2. **Whitelist** — exact/regex match → auto-approved
3. **Feature scripts** — approved windows, future features loaded at runtime
4. **JIT lockbox** — claim-and-burn tickets from approved requests
5. **JIT human approval** — operator approves/denies via dashboard

If a command passes a stage, it executes immediately. The pipeline stops on first match.

---

## 2. Command lifecycle

```
Agent runs ──▶ ssh eshu-gateway@host "<cmd>"
                   │
                   ▼
            1. Blocklisted? ──yes──▶ ❌ Rejected — report to operator
                   │no
                   ▼
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

---

## 3. Auto-approved commands

Commands in the whitelist files are always auto-approved. You can check whether
a command would be auto-approved without sending it:

```bash
curl -s "http://<dashboard>:8000/api/policies/test?command=uptime" | python3 -m json.tool
```

Example response:
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
| `blocked` | Rejected — either the dashboard blocklist or the **hardcoded FATAL tier** (`tier: "fatal"`, e.g. `reboot`, `rm -rf`) |
| `jit` | No rule matched — will require operator approval |

Use this to pre-flight a command. `tier: "fatal"` means the gateway hard-blocks
it and it can never run — don't attempt it. If whitelisted, the command passes
stages 1-4 and is auto-approved. If not, it falls through to JIT (stage 5).

> **Zero-Trust gateways:** on a gateway with **Zero-Trust** enabled (the operator marks
> it in the dashboard), even *whitelisted* commands are **not** auto-approved — everything
> routes to JIT, so expect an operator approval request instead of instant execution.
> Approved-window tokens still auto-run (they were pre-approved by the operator).

> **Policy membership check (`/api/policies/check`):** the operator's Tester uses a
> separate endpoint to ask "is this command already in a policy list?" — it returns
> `in_exact_whitelist` / `in_regex_whitelist` / `in_regex_blacklist` booleans. It is
> **dashboard-session only** (not open to agents); use `/api/policies/test` above for
> pre-flight.

---

## 4. Standard JIT flow

Submit a command for human approval:

```bash
ssh eshu-gateway@host "apt update"
```

If not auto-approved or covered by a window token, the gateway returns:

```
[LOCKED] Command blocked. JIT Approval #000XXX sent to Dashboard.
   Auto-polling for approval (up to 90s)...
```

The gateway auto-polls the dashboard for 90 seconds. Meanwhile, an operator sees a
pending ticket in the dashboard's main queue. They can approve or deny it.

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

`POST /api/window-requests` (no auth; `Content-Type: application/json`):

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

### List windows & inspect one (read-only, no auth)

```bash
curl -s "$DASHBOARD_URL/api/approved-windows" | python3 -m json.tool          # all windows (token omitted in the list)
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
ssh eshu-gateway@host "ESHU_WINDOW_TOKEN=aB3dEfGhIj docker logs -n 50 my-app"
# from a cron job on the gateway itself:
ESHU_WINDOW_TOKEN="aB3dEfGhIj" /usr/local/bin/eshu-gateway bash -c "docker logs -n 50 my-app"
```

The gateway validates the token, checks expiry + schedule + command match, notifies the
dashboard to increment the execution counter, and auto-disables the window once
`execution_count >= max_executions`. **Do not call `/api/approved-windows/execute/{token}`
yourself** — the gateway does it.

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
per source IP on all unauthenticated endpoints (request, log, register, heartbeat,
window-requests, poll, policy). If exceeded:

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
| `Cannot connect to dashboard` | Dashboard unreachable | Retry later. Gateway falls back to last synced policy |
| `429 Too many requests` | Rate limited | Wait a few seconds, then retry |
| `script_not_found` error | Gateway script missing | Contact operator |
| JIT times out (90s) | Operator didn't respond | Reschedule for when operator is available |
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
| `/api/policies/test?command=` | GET | Pre-flight a command — returns `action` (auto_approved / blocked / jit) + `tier` for the FATAL blocklist | Public |
| `/api/window-requests` | POST | Submit window request | Public |
| `/api/window-requests/{retrieval_key}` | GET | Poll window request status + token (read-only) | Public |
| `/api/approved-windows` | GET | List windows (full status; token omitted) | Public (read) |
| `/api/approved-windows/{retrieval_key}` | GET | Single window details + token (read-only) | Public (read) |
| `/api/window-by-token/{token}` | GET | Parameters for an existing token (token is the auth) | Public |
| `/api/docs/agent-windows` | GET | Alias for this manual (full guide incl. Approved Windows) | Public |
| `/api/docs/agent-manual` | GET | This manual | Public |

---

## 9. Complete example workflow

This example shows the full flow: check a command, try auto-approval, request a window,
and use it.

```bash
#!/bin/bash
# Example: AI agent deploying a scheduled cleanup
DASHBOARD="http://192.168.1.100:8000"
GATEWAY="192.168.1.201"
COMMAND="docker system prune -f --volumes"

# 1. Check if command is auto-approved
echo "Checking if command is auto-approved..."
RESULT=$(curl -s "$DASHBOARD/api/policies/test?command=$(echo "$COMMAND" | python3 -c "import urllib.parse; print(urllib.parse.quote(input()))")")
if echo "$RESULT" | grep -q '"action": "auto_approved"'; then
    echo "✅ Command is auto-approved — executing directly"
    ssh eshu-gateway@"$GATEWAY" "$COMMAND"
    exit $?
fi

# 2. Request a window for tomorrow at 03:00 UTC
TOMORROW=$(date -u -d "+1 day 03:00:00" +%s)
echo "Requesting approved window for $TOMORROW..."
WINDOW_RESPONSE=$(curl -s -X POST "$DASHBOARD/api/window-requests" \
    -H "Content-Type: application/json" \
    -d "{\"gateway_ip\":\"$GATEWAY\",\"command\":\"$COMMAND\",\"match_type\":\"prefix\",\"window_start\":$TOMORROW,\"max_executions\":1,\"label\":\"Scheduled cleanup\"}")
RETRIEVAL_KEY=$(echo "$WINDOW_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('retrieval_key',''))")

if [ -z "$RETRIEVAL_KEY" ]; then
    echo "⚠️  Window request failed — falling back to JIT"
    ssh eshu-gateway@"$GATEWAY" "$COMMAND"
    exit $?
fi

echo "🪟 Window request submitted — waiting for operator approval..."

# 3. Poll by retrieval_key (read-only, no auth) for up to 5 minutes
for i in $(seq 1 100); do
    STATUS=$(curl -s "$DASHBOARD/api/window-requests/$RETRIEVAL_KEY" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "pending")
    if [ "$STATUS" = "approved" ]; then
        TOKEN=$(curl -s "$DASHBOARD/api/window-requests/$RETRIEVAL_KEY" | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || echo "")
        echo "🔑 Token received: $TOKEN"
        # 4. Schedule the cron-job
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
