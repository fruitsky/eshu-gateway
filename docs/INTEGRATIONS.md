# Integrations & MCP — Agent API Gateway

Eshu's integration layer lets an AI agent (Hermes, Claude Desktop, Cursor, or any
MCP client) call your homelab APIs **through Eshu** instead of holding raw API
keys. Eshu holds the credentials server-side, exposes a curated set of tools over
the MCP protocol, audits every call, and gates mutating operations behind operator
approval — the same human-in-the-loop model as the SSH gateway.

This page is the operator's setup guide. For the endpoint reference see
[API.md](API.md) → **Integrations & MCP**.

## How it fits together

```
Agent (Hermes) ── MCP (Streamable HTTP) ──> /mcp ──> Eshu dashboard
                                                      │  (agent-token auth)
                                                      ├─ credential vault (server-side only)
                                                      ├─ allowlist (enabled tools)
                                                      ├─ DNS-rebinding host allowlist
                                                      ├─ audit log (every call)
                                                      └─ approval queue (mutating tools)
                                                           │
                                                           ▼
                                                     Upstream API (Proxmox, Omada, HA…)
```

- **Agent tokens** — long-lived bearer tokens Hermes presents to `/mcp`. Only a
  SHA-256 hash is stored; the raw token is shown once at creation.
- **Integrations** — a named upstream API (base URL + auth type + secret). The
  secret lives in the dashboard DB and is never returned by any endpoint.
- **Tools** — typed operations per integration (e.g. `proxmox_list_nodes`).
  Read-only tools auto-run; mutating tools require approval.
- **MCP Access** — the DNS-rebinding host allowlist that controls which `Host`
  header values may reach the MCP endpoint.

## Setup

### 1. Create an agent token

**Integrations → Agent Tokens → Create Token** (name it e.g. `hermes`). The raw
token is shown exactly once — copy it now.

> **Header format (the #1 gotcha).** Paste the **raw token** into your client —
> do **not** prefix it with `Bearer `. When you use Hermes's `--auth header`
> ("API key / Bearer token" prompt), Hermes adds the `Bearer ` prefix itself, so
> a value like `Bearer <token>` in your config produces
> `Authorization: Bearer Bearer <token>` and every call fails with `401`.
> If you instead configure the header explicitly in `config.yaml`, you *do* write
> the prefix yourself: `Authorization: "Bearer <token>"`.

### 2. Add an integration (Proxmox example)

**Integrations → Add Integration**:

| Field | Value |
|-------|-------|
| Name | `proxmox` |
| Base URL | `https://<pve-host>:8006/api2/json` |
| Auth type | `header` |
| Header name | `Authorization` |
| Secret | `PVEAPIToken=<user>@<realm>!<tokenid>=<uuid>` |

Then click **Seed** to populate the curated Proxmox tool catalog (reads + a few
approval-gated mutating tools), and enable the subset you want with the toggles.

> **Role guidance.** Use a least-privilege Proxmox token. `PVEAuditor` (read-only)
> is right for read-only testing; escalate to `PVEVMAdmin` scoped to the VMs you
> manage only when you want the approval-gated mutations to actually execute.
> Don't use `PVEAdmin`.

### 3. Configure MCP Access (DNS-rebinding allowlist)

The MCP endpoint only accepts requests whose `Host` header is in its allowlist
(loopback is always allowed). If you reach the dashboard at a real hostname or
IP — especially behind a reverse proxy — add it:

**Integrations → MCP Access**: `eshu.local.example.com, 192.168.1.114`

It applies live; no restart needed.

### 4. Reverse-proxy note (Nginx Proxy Manager / OpenResty)

If the dashboard is behind a TLS-terminating reverse proxy, make sure it forwards
the `Authorization` header to the backend, otherwise every MCP call 401s at the
proxy. In NPM's **Advanced** tab for the proxy host:

```nginx
proxy_set_header Authorization $http_authorization;
```

(NPM forwards `X-Forwarded-Proto` by default; Eshu uses it so its redirects and
URLs keep the `https://` scheme.)

## Connecting a client (Hermes)

```bash
hermes mcp add eshu-mcp --url https://eshu.local.example.com/mcp --auth header
# at the "API key / Bearer token" prompt, paste the RAW token (no "Bearer " prefix)
```

Hermes auto-discovers the enabled tools on connect and uses them like any other
tool — you don't call them by name; just ask in natural language
("list my VMs", "what's the status of VM 100"). Hermes registers them under its
`mcp_<server>_<tool>` prefix.

- **Read-only tools** run immediately and the call is audited.
- **Mutating tools** (`proxmox_start_vm`, `proxmox_stop_vm`, …) do **not**
  execute. They return a "pending" result; you **approve or deny in the dashboard**
  (**Integrations → Pending API Approvals**), then Hermes polls `check_approval(id)`
  for the outcome.

**Tool changes** (enable/disable/add) apply server-side immediately, but a client
only re-discovers on connect — run `/reload-mcp` in Hermes (or restart it) after
changing tools.

## Tool namespacing

Every MCP tool is namespaced by its integration so tools from different services
can't collide and ownership is obvious:

| Integration | Example tools |
|-------------|---------------|
| Proxmox | `proxmox_list_nodes`, `proxmox_get_vm_status`, `proxmox_start_vm` |
| Omada | `omada_list_clients`, `omada_get_site_status` |
| Home Assistant | `ha_call_service`, `ha_get_state` |

## Adding more integrations

Same flow each time: add the integration (name, base URL, auth) → add or seed its
tools → enable the subset → ensure its hostname is in **MCP Access** if needed.
The catalog is per-integration and namespaced, so integrations are independent —
no tool-name conflicts, no cross-talk.
