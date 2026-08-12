# Eshu Gateway API Reference

## Gateway Endpoints (no authentication required)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/version` | Get the product version (e.g. `v0.1.0`, returns `dev_mode` on non-master branches). **Blocked for gateway-token callers (AI agents)** — only the dashboard UI and gateways (no token header) may call it |
| `GET` | `/api/cmd-descs` | Static + whatis command description dictionary for the UI |
| `POST` | `/api/register` | Register/update gateway heartbeat |
| `POST` | `/api/request` | Submit JIT approval request |
| `POST` | `/api/log` | Log auto-approved/blocked events |
| `GET` | `/api/poll/{ip}` | Poll for approved tickets |
| `GET` | `/api/policy/{ip}` | Fetch policies + trigger flags |
| `GET` | `/api/ticket/{id}` | Direct ticket claim by request ID |
| `GET` | `/api/request_status/{id}` | Check JIT request status |
| `GET` | `/api/gateway-script` | Raw installer (UTF-8) |
| `GET` | `/api/docs/agent-windows` | Alias for the full AI agent manual (`AGENT_MANUAL.md`) |
| `GET` | `/api/docs/agent-manual` | Full AI agent manual for gateway interaction + Approved Windows |
| `POST` | `/api/window-requests` | AI-initiated window request |
| `GET` | `/api/window-requests/{retrieval_key}` | Poll window request status + token by the opaque **retrieval_key** — **open (no auth)**; returns the approved token when approved. The numeric `id` is only honoured for session-authed callers |

## Approved Windows (read open for agents; writes require dashboard auth)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/approved-windows` | List windows (supports `?ip=`) — **open (read)**; `token` and `retrieval_key` omitted for unauthenticated callers |
| `GET` | `/api/approved-windows/{retrieval_key}` | Get a single window (incl. token) by the opaque **retrieval_key** — **open (read)**; numeric `id` only for session-authed callers |
| `POST` | `/api/approved-windows` | Create a window (auth) |
| `PUT` | `/api/approved-windows/{id}` | Update a window (auth) |
| `DELETE` | `/api/approved-windows/{id}` | Delete a window (auth) |
| `POST` | `/api/approved-windows/{id}/toggle` | Enable/disable a window (auth) |
| `GET` | `/api/approved-windows/recent-jit` | Recent JIT approvals for the wizard (auth) |

## Window Request Approvals (dashboard authentication required)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/window-requests/pending` | List pending AI requests |
| `POST` | `/api/window-requests/{id}/approve` | Approve a request and reveal token |
| `POST` | `/api/window-requests/{id}/deny` | Deny a request |

## Gateway-only Window Usage

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/approved-windows/execute/{token}` | Increment usage on a valid window token (called by gateway) |

## Dashboard Endpoints (authentication required)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/requests` | List last 200 requests (supports `?search=`) |
| `POST` | `/api/approve/{id}` | Approve a JIT request |
| `POST` | `/api/deny/{id}` | Deny a JIT request |
| `DELETE` | `/api/requests?older_than=` | Purge history |
| `GET` | `/api/gateways` | List registered gateways |
| `POST` | `/api/gateways/{ip}/uninstall` | Trigger remote uninstall |
| `POST` | `/api/gateways/{ip}/override` | Start Override Mode — auto-approve all JIT for `minutes` (body: `{"minutes": 30, "reason": "..."}`) |
| `DELETE` | `/api/gateways/{ip}/override` | Cancel Override Mode early |
| `POST` | `/api/freeze` | Freeze the fleet — every gateway rejects all commands until unfrozen |
| `POST` | `/api/unfreeze` | Unfreeze the fleet |
| `GET` | `/api/freeze/status` | Freeze state: `{"frozen": bool, "triggered_at": epoch or null}` |
| `POST` | `/api/fleet/commands` | Compose + dispatch a fleet command immediately (session only). Body: `{"command", "target_ips", "reason" (optional), "timeout", "override"}` — frozen → 409, hard blocklist → 400, regex-blacklist → 400 unless `override: true` |
| `GET` | `/api/fleet/commands` | List fleet commands + per-gateway results |
| `POST` | `/api/fleet/commands/{id}/result` | Gateway poller result callback (gateway-token must match `gateway_ip`) |
| `DELETE` | `/api/gateways/{ip}` | Deregister a gateway |
| `GET/POST` | `/api/policies` | Read/write policies |
| `POST` | `/api/policies/commit` | Bump policy version |
| `GET` | `/api/policies/test?command=` | Test a command against policies |
| `GET` | `/api/policies/check?command=` | Check policy membership |
| `GET` | `/api/policy_changes` | Policy change history |
| `POST` | `/api/policies/dismiss-gap` | Dismiss a command from the Policy Gaps widget (body: `{"command": "..."}`) |

## Policy Suggestions (dashboard authentication required)

Background analysis across all gateways — finds commands repeatedly approved via JIT that aren't yet allowlisted.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/learning/gaps` | Get cached gap analysis (per-gateway, new/seen flags) |
| `POST` | `/api/learning/gaps/refresh` | Force a background rescan |
| `POST` | `/api/learning/gaps/mark-seen` | Mark all current gaps as seen |

## Development & Deployment Pipeline (dashboard authentication required)

The Build → Edge → Fleet update pipeline. All endpoints are session-protected — not reachable by gateways or AI agents.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/dev/status` | Pipeline state (needs_seed / ready_for_dev / dev_in_progress / ready_for_promote / clear) + hashes + versions |
| `POST` | `/api/dev/seed` | Copy the current Build installer to the Edge (dev) channel |
| `POST` | `/api/dev/promote` | Deploy Edge to Fleet — backup current Build, copy Edge to the served installer, trigger fleet-wide update |
| `POST` | `/api/dev/rollback` | Restore previous Build from backup and trigger fleet-wide revert |
| `GET` | `/api/dev-gateways` | List dev-mode gateways (`{ip, hostname}`) |
| `POST` | `/api/dev-gateways/push` | Set the dev update trigger so dev-mode gateways pull the latest Edge installer |

## Enrollment & Auth

| Method | Path | Purpose |
|--------|------|---------|
| `GET/PUT` | `/api/enroll/keys` | Manage the Eshu Gateway SSH key |
| `POST` | `/api/enroll/generate` | Create enrollment token |
| `GET` | `/api/enroll?token=` | Serve enrollment script |
| `GET` | `/api/enroll/token-status?token=` | Check if token has been consumed |
| `GET/POST` | `/api/auth/status` | Check auth state |
| `POST` | `/api/auth/login` | Login with password |
| `POST` | `/api/auth/logout` | Clear session |
| `POST` | `/api/auth/set-password` | Set/change password (required on first launch; cannot be removed) |
| `GET/PUT` | `/api/settings/dev-tools` | Get/set the "Show development tools" flag (hides the Build → Edge → Fleet pipeline by default) |
| `GET/PUT` | `/api/notify-config` | Get/set external webhook config (URL, subscribed events, dashboard URL for the 🔗 link) |
| `POST` | `/api/notify-test` | Send a test webhook; returns `delivered: true/false` |

## Statistics & Monitoring

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/statistics?days=14` | Per-gateway & daily command stats |
| `GET` | `/api/statistics?days=14&extended=1` | Extended stats: automation trend, command categories, denied commands, policy gaps, gateway health |
| `GET` | `/api/statistics/export?days=14&format=csv` | Download daily stats as CSV (`format=json` for JSON) |
| `GET` | `/api/audit_log` | Audit log events (supports `?search=`) |
| `GET/POST` | `/api/notes` | Read/write admin notes |