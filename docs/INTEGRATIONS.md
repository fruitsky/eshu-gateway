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

## Mediated web fetch (`web_fetch_url`)

The household agent can read links without giving it raw `web_extract` access.
`web_fetch_url` asks Eshu to fetch a URL; Eshu decides whether the destination is
permitted and makes the request from the dashboard host.

- **Principal:** the tool's caller gate is synced from the enabled per-agent
  policies in `web_fetch_policies`. An agent with an enabled policy is allowed
  (and, in allowlist tool mode, `web_fetch_url` is added to its tool allowlist);
  every other agent is refused and does not see the tool. With no enabled
  policy the tool falls back to the inert `household` placeholder name.
- **Grouping:** pass the usual optional `session_id`/`execution_id` arguments to
  attach the fetch to the same dashboard session as the agent's HA calls.
- **No generic passthrough:** `webfetch` is in `NO_GENERIC_KINDS`, so the
  integration does not inherit `read`/`write` against a placeholder base URL.
- **Per-agent allowlist:** stored in `web_fetch_policies`, keyed by agent
  *name* (not token id), so it survives token rotation. The UI is **allowlist
  only**: a host must match an entry; everything else is blocked, and an empty
  allowlist denies every fetch. Matching is label-boundary suffix based:
  `bromley.gov.uk` matches `www.bromley.gov.uk`, never `evilbromley.gov.uk` or
  `bromley.gov.uk.attacker.tld`. An optional `deny` override remains available
  in the API (evaluated first) for carving a specific host out of a broad allow
  entry; it is not required and is hidden from the UI.
- **Enforcement:** the allowlist is checked before DNS. Every resolved A/AAAA must
  be globally routable (no private, loopback, link-local, CGNAT, IPv4-mapped
  private, or metadata addresses). The connection is pinned to the validated IP
  with TLS SNI/cert verification against the original hostname. Redirects are
  manual (max 3), re-validated per hop, and `https`→`http` is refused.
- **Limits:** text only (`text/html`, `application/xhtml+xml`, `text/plain`,
  `text/markdown`, `text/calendar`); 2 MB raw cap; 5 s connect / 10 s total;
  20 fetches/minute and 200/hour per principal; 3 concurrent. PDFs/images are
  refused in v1. This limiter is separate from the MCP-wide per-agent
  `rate_limit_per_min`; leave that unset or above 20 so the fetch-specific
  message is the one the agent sees. The household token is configured at
  60/min so browsing does not compete with its Home Assistant calls.
- **Output:** a `<untrusted_tool_result source="web_fetch_url">` envelope so the
  agent treats the page as data. Failures are `{"error":"forbidden"|"error",
  "code":"…","reason":"…","retryable":…}`; `forbidden` is final.
- **Audit:** one `integration_calls` row per attempt (`host`, `resolved_ips`,
  `reason_code`, `final_url`, status, bytes, latency), grouped by `session_id`.
  Bodies and query values are never stored. The web integration keeps 90 days of
  audit rows (others default to 30).

### Deploying it

**UI path:** Integrations → Kind **Web fetch** → name it `web` → **Add
Integration** → **Seed** → enable it. Then open **Controls → Web Fetch
Allowlist**, select the household agent token (e.g. `rowana-household`), tick
**Enabled**, paste the allowed domains (one per line), and **Save Policy**.
Saving an enabled policy also adds `web_fetch_url` to that agent's tool
allowlist when the agent is in allowlist mode. The panel reads and writes
`/api/web-fetch-policies`; no base URL or credential is needed.

**API path (scripted):** create the integration disabled, seed it, enable it, and
set the allowlist (the same caller sync runs after these calls):

```bash
# 1. Create (kind webfetch -> no generic tools). base_url is unused by the tool.
curl -s -X POST http://<dashboard>:8000/api/integrations \
  -b 'eshu_session=<session-cookie>' -H 'Content-Type: application/json' \
  -d '{"name":"web","base_url":"https://invalid.local","auth_type":"none","kind":"webfetch","enabled":false,"mcp_mode":"joined","retention_days":90}'

# 2. Seed the single fetch_url tool (caller list comes from enabled policies).
curl -s -X POST http://<dashboard>:8000/api/integrations/web/seed \
  -b 'eshu_session=<session-cookie>'

# 3. Enable the integration and the tool in the UI, or:
curl -s -X PUT http://<dashboard>:8000/api/integrations/web \
  -b 'eshu_session=<session-cookie>' -H 'Content-Type: application/json' \
  -d '{"enabled":true}'

# 4. Set the allowlist for the household agent token (hot reload — no restart).
#    This also grants web_fetch_url to that agent. `deny` is an optional
#    advanced API-only carve-out; it is not required.
curl -s -X PUT http://<dashboard>:8000/api/web-fetch-policies/rowana-household \
  -b 'eshu_session=<session-cookie>' -H 'Content-Type: application/json' \
  -d '{"enabled":true,
       "allow":["bromley.gov.uk","gov.uk","tesco.com","sainsburys.co.uk","ocado.com"]}'

# 5. Reload the MCP client's tool list (e.g. /reload-mcp in Hermes).
```

Once the integration is enabled, `web_fetch_url` is registered on `/mcp`
immediately (`refresh_mcp_tools` runs after the API calls). No gateway restart is
needed; the MCP client still re-discovers tools on connect, so run `/reload-mcp`
(or restart Hermes) after enabling it.

> The fetch runs on the **Eshu host**, so its DNS queries appear in that host's
> resolver log, not the agent container's. Confirm the dashboard host uses the
> Pi-hole resolvers before using "no query in Pi-hole" as the S1 oracle.

**Rollback:** set the policy `enabled:false`, disable the integration/tool, or
remove `web_fetch_url` from the household allowlist. All fail closed; no agent
change is needed.

## Context & efficiency

Every MCP tool you expose is loaded into the agent's context window at connect
time (name + description + input schema). With a large catalog this can consume
tens of thousands of tokens before the conversation even starts, which degrades
reasoning and raises cost. The fix is to load **fewer, relevant tools** — not
more.

Three levers, in order of impact:

1. **Enable only what you use (biggest win).** Disable the long tail of tools you
   rarely call. In **Integrations → Tools** use **Disable all** to start from
   zero, then enable the handful you actually use, or use the per-tool toggles.
   The header shows an `enabled / total` count. Fewer enabled tools = less
   context, fewer wrong-tool-selection errors.

2. **Mount per-integration MCP servers (least-privilege scoping).** Each
   integration has an **MCP surface** setting (Integrations → Add/Edit → *MCP
   surface*):
   - **Shared `/mcp`** (default) — tools served on the all-tools endpoint,
     namespaced (`proxmox_list_nodes`).
   - **Own endpoint `/mcp/<name>`** — tools served only at `/mcp/proxmox`
     (un-namespaced, e.g. `list_nodes`), excluded from `/mcp`.
   - **Both** — on both surfaces.

   Set an integration to *own endpoint* when you want a session to reach it
   **only** through its own server — e.g. a coding session shouldn't touch
   Proxmox at all. Mount the per-integration server in Hermes:

   ```bash
   hermes mcp add eshu-proxmox --url https://<dashboard>/mcp/proxmox --auth header
   ```

   Changing an integration's MCP surface takes effect on the next dashboard
   restart (the per-integration mounts are built at startup).

   For pure context savings with Hermes, you normally **don't** need this — see
   the client-side note below.

3. **Compact tool descriptions.** Descriptions are the bulk of each tool's
   token cost. Eshu's curated catalogs keep them tight (safety/guardrail notes
   are preserved); a custom tool's `description` field is what you write, so keep
   it concise but disambiguating.

> **Client-side note — the recommended context lever.** Hermes ships **Tool
> Search** (opt-in), which defers MCP tool schemas until the model searches for
> them — the model sees a searchable listing of tool names + short descriptions
> instead of every full schema. Enable it in Hermes `config.yaml`:
>
> ```yaml
> tools:
>   tool_search:
>     enabled: on          # or "auto"
>     threshold_pct: 10    # listing budget as % of context
> ```
>
> With Tool Search on the single `/mcp`, you get "load only what you need" with
> one MCP server and no tool renames — per-integration endpoints are only needed
> for least-privilege scoping, not for context.

## Supported integrations

Eshu ships **curated tool catalogs** for these kinds — seeded via **Integrations → Add Integration → Seed**, with auth details:

| Kind | Auth type | Notes |
|------|-----------|-------|
| **Proxmox** | `header` (`Authorization: PVEAPIToken=…`) | reads + approval-gated VM lifecycle tools |
| **Omada** | `oauth2` | client-credentials token exchange; auto re-auth on expiry. List reads auto-inject `page/pageSize`; ACL creates return the created rule's id; `acl_reorder` rebuilds the full rule map server-side (approval-gated). Read completeness: `list_known_clients` enumerates **all** clients (online + offline, v2 `scope=0`), `list_networks`, `list_acls`, `list_dhcp_reservations`, `get_device` and `list_client_events` close the read side, and the list tools report `totalRows`/`returned`/`truncated` |
| **Home Assistant** | `bearer` (long-lived token) | `call_service` is mutating → approval |
| **Pulse** | `bearer`/`header` | trends, backups (large payloads truncated to 1MB) |
| **Jellyfin** | `header` (`X-Emby-Token`) | mutations always gated. Read-only generic `read` (paths relative to `/`), closing the un-curated gaps: `/System/Configuration/encoding`, `/Items?fields=MediaSources,MediaStreams`, `/Library/VirtualFolders`, `/Plugins/{id}/Configuration`. No generic write (admin key) |
| **Pi-hole** | `query_token` (`?auth=…`) | multi-instance via name-based namespacing |
| **Sonarr** / **Radarr** | `header` (`X-Api-Key`) | parameterized *arr catalog; search flags guarded. Incidents: `episodes`, `episode_files`/`movie_files` (per-file `mediaInfo`), `release_search` (per-release CF score + rejections), `quality_profiles` (CF→score `formatItems`), `custom_formats` (`full` shows the regex `fields`). Gated writes: `custom_format_create` / `custom_format_update` / `quality_profile_update` / `quality_definition_update` (native per-quality size limits, MB/min — read→merge→PUT→read-back). Also a generic **read-only** `read` passthrough (paths relative to `/api/v3`); no generic write |
| **Prowlarr** | `header` (`X-Api-Key`) | **read-only projection** — indexer `fields[]` credentials are never surfaced (the generic read is safe because the scrubber drops list-shaped `fields`). Gated `search` (live Torznab query). Generic read-only `read` (relative to `/api/v1`); no generic write |
| **Bazarr** | `header` (`X-API-KEY`) | subtitle management. Base URL is the origin, e.g. `http://<ip>:6767` (API under `/api/...`). Reads: `series`, `episodes`, `history`, `system_status`. Gated write: `search_subtitles` (`PATCH /api/series` action=`search-missing`). Generic `read`/`write` (relative to `/api`); the generic write is **always** approval-gated and `/system/*` writes are form-encoded (Bazarr reads `request.form` only) |
| **Nginx Proxy Manager** | `session` (JWT + CSRF) | create a dedicated NPM admin user; Client ID = its email, Client Secret = its password, Token URL = `http://<ip>:81/api/tokens`. Base URL is `http://<ip>:81/api` (the admin port — never 443 for API calls). Fully curated; writes always approval-gated |
| **Web fetch** (`webfetch`) | n/a | mediated outbound `web_fetch_url` (see above); no base URL, no generic floor; household principal only |

Curated kinds are excluded from the generic floor (`NO_GENERIC_KINDS`: Pi-hole, NPM, Seerr, web fetch). Any other integration type seeds a generic **`read`**/**`write`** pair that can call arbitrary REST endpoints on the base URL (read-only `read` auto-runs; `write` is gated). `read` accepts a `method` param — `HEAD` returns `{status, content_length, content_type, url}` metadata with no body (e.g. checking a media file's size without downloading it). Three refinements: kinds whose app exposes the API under a fixed prefix (Sonarr/Radarr `/api/v3`, Prowlarr `/api/v1`, Bazarr `/api`) get that prefix injected, so agents pass paths relative to the API root; kinds in `READ_ONLY_GENERIC_KINDS` (Sonarr/Radarr/Prowlarr/Jellyfin) get the generic **read only** (an un-curated *arr write can trigger torrent searches or delete media; Jellyfin's key is admin-level), so writes stay on the curated, guardrailed catalog; and form-encoded apps (Bazarr `/system/*`) get an `application/x-www-form-urlencoded` body. All generic **writes** return an evidence envelope (`{status, method, path, content_type, sent_body_keys, response_body}`, with `verified:false` + a note when a 2xx body is empty) and accept `verify_path` for a post-write read-back.

## Tool namespacing

Every MCP tool is namespaced by its **integration name** (sanitized to `[a-z0-9_]`) so tools from different services can't collide, ownership is obvious, and the same kind can be added multiple times (e.g. two Pi-hole instances) without conflict:

| Integration name | Example tools |
|------------------|---------------|
| `proxmox` | `proxmox_list_nodes`, `proxmox_get_vm_status`, `proxmox_start_vm` |
| `omada` | `omada_list_sites`, `omada_list_known_clients`, `omada_get_client`, `omada_list_acls`, `omada_block_client` |
| `home-assistant` | `home_assistant_list_entities`, `home_assistant_call_service` |
| `pihole-main` | `pihole_main_get_summary`, `pihole_main_get_top_clients` |
| `jellyfin` | `jellyfin_system_info`, `jellyfin_sessions`, `jellyfin_read` |
| `sonarr` | `sonarr_series`, `sonarr_episode_files`, `sonarr_release_search`, `sonarr_read` |
| `radarr` | `radarr_movies`, `radarr_movie_files`, `radarr_read` |
| `prowlarr` | `prowlarr_indexers`, `prowlarr_indexer_stats`, `prowlarr_search` |
| `bazarr` | `bazarr_series`, `bazarr_episodes`, `bazarr_search_subtitles`, `bazarr_read` |
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

## List completeness (totals + truncation)

List tools that opt in with `totals: true` (the Omada read tools, e.g.
`omada_list_site_devices`) wrap their result as
`{"collectionRows": N, "totalMatched": M, "returned": K, "truncated": bool, "rows": [...]}`
so a filtered or paged view is never mistaken for the whole set.
`collectionRows` is the whole collection (upstream grid total, captured before
projection); `totalMatched` is the size of the query's own universe
(post-filter/search, pre-limit); `truncated` is true **only when the response
omitted rows the query could have returned** (paging/limit) — a filtered call
that returns every match is not truncated. The Omada transform-backed reads
(`list_known_clients`, `list_networks`, `list_acls`, `list_dhcp_reservations`,
`list_client_events`) emit the same envelope; `list_client_events` also reports
`scanned` and `scanCapped` because its scan is capped. Errors are typed as
`{"error": {"code", "message", "upstream"?}}`; an Omada `-1505` on a site-scoped
call is classified as `unknown_site` vs `permission_denied`. Upstream logical
errors (e.g. Omada `errorCode -1` inside an HTTP 200 body) are reported with a
non-2xx `status_code`, never 200.

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
- **Lovelace dashboards**: six curated `lovelace_*` tools (namespaced by the
  integration name, e.g. `home_assistant_lovelace_dashboard`) wrap the
  WebSocket-only Lovelace surface (there is no REST API for it):
  - `lovelace_dashboards` / `lovelace_dashboard` — reads. The single-dashboard
    read is **summarised** by default (top-level keys + per-view cards with
    best-effort entity/statistic targets); pass `full: true` for the verbatim
    config, or `view: <index|path|title>` to narrow. Omit `url_path` for the
    default dashboard (only if one has a saved config).
  - `lovelace_create_dashboard` / `lovelace_update_dashboard` — metadata writes.
    `url_path` must contain a hyphen unless `allow_single_word: true`; update
    accepts `dashboard_id` **or** `url_path` (resolved server-side).
  - `lovelace_save_config` — the safe edit path. Prefer `ops`
    (`append_card`/`set_card`/`delete_card`/`set_view`/`delete_view`); the tool
    reads → patches → diffs → writes → **re-reads to verify** (HA's raw
    `lovelace/config/save` is a blind full replace with no locking). `dry_run:
    true` returns the diff without writing and runs without approval;
    `expect_hash` refuses a write if the live config changed; unknown top-level
    keys (e.g. `kiosk_mode`) are preserved. **Gated** → operator approval.
  - `lovelace_delete_dashboard` — storage-mode deletes; HA removes the stored
    config too, so no orphan is left. YAML-mode dashboards are refused
    (file-backed). **Gated** → operator approval.
  - Gotchas encoded in the tools: all Lovelace commands are admin-only; the
    update/delete key is `dashboard_id`, while config read/save use `url_path`;
    YAML-mode dashboards are read-only.
- **Behind a reverse proxy**: ensure it forwards the `Authorization` header, e.g.
  NPM Advanced config `proxy_set_header Authorization $http_authorization;` —
  otherwise HA returns 401 and the **Test** button will surface it.
