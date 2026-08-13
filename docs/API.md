# Eshu Gateway API Reference

Auth legend:
- **Public** — no authentication (rate-limited).
- **Gateway** — caller must send `X-Gateway-Token: <token>` matching the gateway's IP.
- **Session** — caller must be logged into the dashboard (session cookie).
- **Token-in-path** — the value in the URL *is* the secret (e.g. a window token).

All public and gateway endpoints are rate-limited (60 req/60s per source IP).
Session-protected endpoints are **not** reachable by AI agents or gateways —
only the dashboard UI after login.

## Gateway Endpoints (no authentication required)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/version` | Product version (e.g. `v0.1.0`, `dev_mode` on non-master branches). **Blocked for gateway-token callers** — dashboard UI and gateways (no token header) only | Public |
| `GET` | `/api/cmd-descs` | Static + `whatis` command description dictionary for the UI | Public |
| `POST` | `/api/register` | Register/update a gateway, mint/return its API token | Public |
| `POST` | `/api/request` | Submit a JIT approval request | Public |
| `POST` | `/api/log` | Log auto-approved/blocked events from the gateway | Public |
| `POST` | `/api/gateway-heartbeat` | Health heartbeat from `eshu-logger` (poller/gateway/reachability flags) | Public |
| `GET` | `/api/poll/{ip}` | Poll for approved JIT tickets | Gateway |
| `GET` | `/api/policy/{ip}` | Fetch policies + trigger flags + windows for a gateway | Gateway |
| `GET` | `/api/ticket/{id}` | Direct ticket claim by request ID | Gateway |
| `GET` | `/api/request_status/{id}` | Check JIT request status | Public |
| `GET` | `/api/gateway-script` | Raw golden installer (UTF-8) | Public |
| `GET` | `/api/gateway-script-rollback` | Previous golden installer (for rollback) | Public |
| `POST` | `/api/approved-windows/execute/{token}` | Increment usage on a valid window token (called by gateway) | Gateway |
| `POST` | `/api/uninstall-started/{ip}` | Poller confirmation that the transient uninstall service launched (clears the re-spawn trigger) | Public |
| `POST` | `/api/uninstall-progress` | Progress updates from the uninstall service | Public |
| `GET` | `/api/docs/agent-windows` | Alias for the AI agent manual | Public |
| `GET` | `/api/docs/agent-manual` | Full AI agent manual (gateway interaction + Approved Windows) | Public |

## Approved Windows (read open for agents; writes require dashboard auth)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/api/window-requests` | AI-initiated window request → returns `{id, retrieval_key, status}` | Public |
| `GET` | `/api/window-requests/{retrieval_key}` | Poll request status + token by the opaque **retrieval_key**; numeric `id` only for session-authed callers | Public |
| `GET` | `/api/window-requests/pending` | List pending AI requests | Session |
| `POST` | `/api/window-requests/{id}/approve` | Approve a request and reveal its token | Session |
| `POST` | `/api/window-requests/{id}/deny` | Deny a request | Session |
| `GET` | `/api/approved-windows` | List windows (`?ip=` filter) — `token` + `retrieval_key` omitted for unauthenticated callers | Public (read) |
| `GET` | `/api/approved-windows/{retrieval_key}` | Single window (incl. token) by opaque key; numeric `id` only for session-authed callers | Public (read) |
| `GET` | `/api/approved-windows/{retrieval_key}/executions` | Usage history for a window (opaque key; numeric `id` session-only) | Public (read) |
| `GET` | `/api/approved-windows/recent-jit` | Recent JIT approvals for the window-creation wizard | Session |
| `POST` | `/api/approved-windows` | Create a window | Session |
| `PUT` | `/api/approved-windows/{id}` | Update a window | Session |
| `DELETE` | `/api/approved-windows/{id}` | Delete a window | Session |
| `POST` | `/api/approved-windows/{id}/toggle` | Enable/disable a window | Session |
| `GET` | `/api/window-by-token/{token}` | Full window parameters for an existing token (for scheduling) | Token-in-path |

## Dashboard Endpoints (authentication required)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/requests` | List the last 200 requests (`?search=` filter) | Session |
| `POST` | `/api/approve/{id}` | Approve a JIT request | Session |
| `POST` | `/api/deny/{id}` | Deny a JIT request | Session |
| `DELETE` | `/api/requests?older_than=` | Purge request history (`30m`/`1h`/`1d`/`2d`/`7d`/`all`) | Session |
| `GET` | `/api/gateways` | List registered gateways (incl. token status, policy sync, override state) | Session |
| `POST` | `/api/gateways/{ip}/uninstall` | Trigger remote uninstall | Session |
| `POST` | `/api/gateways/{ip}/override` | Start Override Mode (body: `{"minutes": 30, "reason": "..."}`; 1-1440 min) | Session |
| `DELETE` | `/api/gateways/{ip}/override` | Cancel Override Mode early | Session |
| `POST` | `/api/gateways/{ip}/zero-trust` | Toggle Zero-Trust on a gateway (body: `{"enabled": true/false}`) | Session |
| `PUT` | `/api/gateways/{ip}/mode` | Set gateway mode (`dev`/`prod`) | Session |
| `DELETE` | `/api/gateways/{ip}` | Deregister a gateway (session or matching gateway token) | Session/Gateway |
| `POST` | `/api/freeze` | Freeze the fleet — every gateway rejects all commands until unfrozen | Session |
| `POST` | `/api/unfreeze` | Unfreeze the fleet | Session |
| `GET` | `/api/freeze/status` | Freeze state: `{"frozen": bool, "triggered_at": epoch or null}` | Session |

## Policy (dashboard authentication required)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET/POST` | `/api/policies` | Read/write policy lists | Session |
| `POST` | `/api/policies/commit` | Bump the policy version (gateways pick it up on next poll) | Session |
| `POST` | `/api/policies/trigger-update` | Force gateways to re-sync policies now | Session |
| `GET` | `/api/policies/test?command=` | Pre-flight a command → `action` (`auto_approved`/`blocked`/`jit`) + `tier: "fatal"` for the hardcoded blocklist. **Public** so agents can pre-flight | Public |
| `GET` | `/api/policies/check?command=` | Membership booleans (`in_exact_whitelist`, `in_regex_whitelist`, `in_regex_blacklist`) for the operator's Tester | Session |
| `GET` | `/api/policy_changes` | Policy change history | Session |
| `GET` | `/api/policies/rollback-status` | Is a rollback backup available + is a rollback triggered | Session |
| `POST` | `/api/policies/rollback/{change_id}` | Roll a policy back to a prior change | Session |
| `POST` | `/api/policies/dismiss-gap` | Dismiss a command from the Policy Suggestions list (body: `{"command": "..."}`) | Session |

## Policy Suggestions (dashboard authentication required)

Background analysis across all gateways — finds commands repeatedly approved via
JIT that aren't yet allowlisted.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/learning/gaps` | Cached gap analysis (per-gateway, new/seen flags) | Session |
| `POST` | `/api/learning/gaps/refresh` | Force a background rescan | Session |
| `POST` | `/api/learning/gaps/mark-seen` | Mark all current gaps as seen | Session |

## Fleet Run (dashboard authentication required)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/api/fleet/commands` | Compose + dispatch a fleet command immediately (body: `{"command", "target_ips", "timeout", "reason"?}`). Frozen → 409; hard blocklist → 400; regex-blacklist → 400 unless `override` | Session |
| `GET` | `/api/fleet/commands` | List fleet commands + per-gateway results | Session |
| `GET` | `/api/fleet/commands/{id}/output/{gateway_ip}` | Full stored output for one gateway's result | Session |
| `POST` | `/api/fleet/commands/{id}/result` | Gateway result callback (gateway token must match `gateway_ip`) | Gateway |
| `DELETE` | `/api/fleet/commands/{id}` | Delete a fleet command | Session |
| `DELETE` | `/api/fleet/commands/{id}/result/{gateway_ip}` | Clear (skip) one gateway's queued result | Session |

## Feature Flags (dashboard authentication required)

The flag → script → sync pipeline. Only used by the dashboard UI — not open to agents.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/feature-flags` | List feature flags + scopes | Session |
| `POST` | `/api/feature-flags/{name}/toggle` | Enable/disable a flag (body: `{"enabled": true}`) | Session |
| `POST` | `/api/feature-flags/{name}/state` | Set flag state (`off`/`dev`/`prod`; body: `{"state": "..."}`) | Session |

## Development & Deployment Pipeline (dashboard authentication required)

The Build → Edge → Fleet update pipeline. Hidden in the UI behind the "Show
development tools" Settings toggle; not reachable by gateways or AI agents.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/dev/status` | Pipeline state (`needs_seed`/`ready_for_dev`/`dev_in_progress`/`ready_for_promote`/`clear`) + hashes + product version | Session |
| `POST` | `/api/dev/seed` | Copy the Build installer to the Edge (dev) channel | Session |
| `POST` | `/api/dev/promote` | Deploy Edge to Fleet — backup Build, copy Edge to served installer, trigger fleet-wide update | Session |
| `POST` | `/api/dev/rollback` | Restore previous Build from backup and trigger fleet-wide revert | Session |
| `GET` | `/api/dev-gateways` | List dev-mode gateways | Session |
| `POST` | `/api/dev-gateways/push` | Set the dev update trigger for dev-mode gateways | Session |

## Enrollment & Auth

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET/PUT` | `/api/enroll/keys` | Manage the Eshu Gateway SSH key | Session |
| `POST` | `/api/enroll/generate` | Create an enrollment token | Session |
| `GET` | `/api/enroll?token=` | Serve the enrollment one-liner script | Token-in-path |
| `GET` | `/api/enroll/token-status?token=` | Check if an enrollment token has been consumed | Token-in-path |
| `GET` | `/api/auth/status` | Auth state (`password_set`, `authenticated`) | Public |
| `POST` | `/api/auth/login` | Login with password → session cookie | Public |
| `POST` | `/api/auth/logout` | Clear the session cookie | Public |
| `POST` | `/api/auth/set-password` | Set/change password (required on first launch; cannot be removed) | Session (first-run: none) |
| `GET/PUT` | `/api/settings/dev-tools` | Get/set the "Show development tools" flag | Session |
| `GET/PUT` | `/api/notify-config` | External webhook config (URL, subscribed events, dashboard URL for the 🔗 link) | Session |
| `POST` | `/api/notify-test` | Send a test webhook; returns `delivered: true/false` | Session |

## Statistics & Monitoring (dashboard authentication required)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/api/statistics?days=14` | Per-gateway & daily command stats | Session |
| `GET` | `/api/statistics?days=14&extended=1` | Extended: automation trend, command categories, denied commands, gateway health | Session |
| `GET` | `/api/statistics/export?days=14&format=csv` | Download stats as CSV (`format=json` for JSON) | Session |
| `GET` | `/api/audit_log` | Audit log events (`?search=` filter) | Session |
| `GET/POST` | `/api/notes` | Read/write admin notes | Session |
