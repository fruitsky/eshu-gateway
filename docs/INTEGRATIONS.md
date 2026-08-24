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

## Supported integrations

Eshu ships **curated tool catalogs** for these kinds — seeded via **Integrations → Add Integration → Seed**, with auth details:

| Kind | Auth type | Notes |
|------|-----------|-------|
| **Proxmox** | `header` (`Authorization: PVEAPIToken=…`) | reads + approval-gated VM lifecycle tools |
| **Omada** | `oauth2` | client-credentials token exchange; auto re-auth on expiry |
| **Home Assistant** | `bearer` (long-lived token) | `call_service` is mutating → approval |
| **Pulse** | `bearer`/`header` | trends, backups (large payloads truncated to 1MB) |
| **Jellyfin** | `header` (`X-Emby-Token`) | mutations always gated |
| **Pi-hole** | `query_token` (`?auth=…`) | multi-instance via name-based namespacing |
| **Sonarr** / **Radarr** | `header` (`X-Api-Key`) | parameterized *arr catalog; search flags guarded |
| **Prowlarr** | `header` (`X-Api-Key`) | **read-only projection** — indexer `fields[]` credentials are never surfaced |

Curated kinds are excluded from the generic floor (`NO_GENERIC_KINDS`). Any other integration type seeds a generic **`read`**/**`write`** pair that can call arbitrary REST endpoints on the base URL (read-only `read` auto-runs; `write` is gated). `read` accepts a `method` param — `HEAD` returns `{status, content_length, content_type, url}` metadata with no body (e.g. checking a media file's size without downloading it).

## Tool namespacing

Every MCP tool is namespaced by its **integration name** (sanitized to `[a-z0-9_]`) so tools from different services can't collide, ownership is obvious, and the same kind can be added multiple times (e.g. two Pi-hole instances) without conflict:

| Integration name | Example tools |
|------------------|---------------|
| `proxmox` | `proxmox_list_nodes`, `proxmox_get_vm_status`, `proxmox_start_vm` |
| `omada` | `omada_list_sites`, `omada_search_devices`, `omada_block_client` |
| `home-assistant` | `home_assistant_list_entities`, `home_assistant_call_service` |
| `pihole-main` | `pihole_main_get_summary`, `pihole_main_get_top_clients` |
| `jellyfin` | `jellyfin_get_media_items`, `jellyfin_scan_library` |
| `sonarr` | `sonarr_get_series`, `sonarr_get_queue` |
| `radarr` | `radarr_get_movies`, `radarr_get_missing_movies` |
| `prowlarr` | `prowlarr_list_indexers`, `prowlarr_indexer_stats` |
| `pulse` | `pulse_health`, `pulse_get_backups` |

## Response projection

Read-only tools that return large payloads project the response down to a lean
field set by default, so the agent doesn't pay context cost for fields it rarely
needs (e.g. `proxmox_list_vms` returns only `vmid, name, status, type` instead
of the full VM objects). Every projected tool exposes a **`full`** parameter —
pass `full: true` to get the complete, unprojected upstream object. Projection is
defined per tool as its `fields` list in the catalog.

## Search & limit on list tools

List tools that declare a `search_field` also expose **`search`** (case-insensitive
substring filter on that field) and **`limit`** (max results, default 50), so the
agent can bound large lists client-side (e.g. `ha_list_entities(search="light")`
returns only `light.*` entities). These are client-side shaping — they filter/trim
the response in the proxy and are never forwarded upstream.

## Adding more integrations

Same flow each time: add the integration (name, base URL, auth) → add or seed its
tools → enable the subset → ensure its hostname is in **MCP Access** if needed.
The catalog is per-integration and namespaced, so integrations are independent —
no tool-name conflicts, no cross-talk.

## Home Assistant

- **Base URL**: `https://<ha-host>/api` (or `http://<ha-host>:8123/api`)
- **Auth**: `bearer`, secret = a **long-lived access token** from your HA profile.
- **Seed tools**: `ha_list_entities`, `ha_get_entity` (reads, auto-run, projected
  to lean fields) and `ha_call_service` (mutating → operator approval). Pass
  service data as a JSON object, e.g. `call_service(domain="light", service="turn_on", data={"entity_id": "light.living_room"})`.
- **Behind a reverse proxy**: ensure it forwards the `Authorization` header, e.g.
  NPM Advanced config `proxy_set_header Authorization $http_authorization;` —
  otherwise HA returns 401 and the **Test** button will surface it.
