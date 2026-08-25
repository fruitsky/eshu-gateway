"""Curated seed catalog for Pulse (Proxmox monitoring, v5.1.36).

Auth is a simple `X-API-Token: <token>` header (Bearer also accepted) — the
integration is created with `auth_type: header` / `auth_header_name:
X-API-Token` / gate_mode per operator preference.

Read tools auto-run (with compact response transforms + search/limit shaping);
write tools are mutating and route through the operator approval queue. The
response transforms live in `core.transforms`; tools here tag `transform:
<name>` where a flat `fields` projection can't express the shape (fleet
summary, charts downsampling, backup merge, health+version, ...).

Notes that matter:
- `local: True` params are consumed by the transform / shaping only and are
  never forwarded to the upstream API (e.g. charts `maxPoints`).
- `redact: True` params (node token/password) are masked in audit/UI display.
- `not_implemented: True` tools are the v6 upgrade markers — they register so
  agents see the roadmap but return a clear error when called.
"""

PULSE_SEED_TOOLS = [
    # ── Read tools ──────────────────────────────────────────────────────
    {
        "name": "health",
        "description": "Check Pulse API health and version (status, uptime, version, channel, deployment type). No auth required upstream; audited here.",
        "method": "GET",
        "path_template": "/health",
        "params": [],
        "transform": "pulse_health",
        "example": '{"status": "healthy", "uptime": 3171279.7, "version": "5.1.36", "channel": "stable", "deploymentType": "proxmoxve"}',
        "read_only": True,
    },
    {
        "name": "fleet_summary",
        "description": "List all monitored resources (containers, VMs, storage, hosts) with a compact per-resource projection (id, name, type, status, cpu/mem/disk percent, IPs, alerts). Use search (name/id substring) and limit to narrow. full=true adds node, OS, uptime, last backup, tags, traffic.",
        "method": "GET",
        "path_template": "/resources",
        "params": [],
        "fields": ["id", "name", "type", "status", "cpuPct", "memPct", "diskPct", "ip", "alerts"],
        "search_field": "name",
        "transform": "pulse_fleet_summary",
        "example": '{"count": 12, "resources": [{"id": "pve:pve3:104", "name": "CloudFlare", "type": "container", "status": "stopped", "cpuPct": 0}]}',
        "read_only": True,
    },
    {
        "name": "get_resource",
        "description": "Get a single monitored resource by id (e.g. pve:pve3:104). Returns identity, status, cpu/mem/disk percent and bytes, IPs, OS, uptime, last backup and alerts. full=true adds tags and traffic.",
        "method": "GET",
        "path_template": "/resources/{resourceId}",
        "params": [
            {"name": "resourceId", "type": "string", "description": "Resource id (from pulse_fleet_summary).", "required": True},
        ],
        "fields": ["id", "name", "type", "status", "osName", "ip", "alerts"],
        "transform": "pulse_get_resource",
        "example": '{"id": "pve:pve3:104", "name": "CloudFlare", "type": "container", "status": "stopped", "osName": "Ubuntu", "ip": ["192.168.1.240"]}',
        "read_only": True,
    },
    {
        "name": "list_alerts",
        "description": "List active (default) or historical alerts (scope=history). Use search (substring over alert message, resource name, or id) to narrow. Returns id, level, type, resource, node, message, value, threshold, timestamps, acknowledged.",
        "method": "GET",
        "path_template": "/alerts/{scope}",
        "params": [
            {"name": "scope", "type": "string", "description": "active or history (default active).", "required": False, "default": "active"},
            {"name": "limit", "type": "integer", "description": "Max results (default 50).", "required": False, "default": 50},
            {"name": "search", "type": "string", "description": "Substring filter on alert message, resource name, or alert id.", "required": False, "local": True},
        ],
        "transform": "pulse_list_alerts",
        "example": '[{"id": "pve:pve:200-memory", "level": "warning", "type": "memory", "resourceName": "TrueNas-Scale", "message": "VM memory at 90.8%", "acknowledged": true}]',
        "read_only": True,
    },
    {
        "name": "get_charts",
        "description": "Get chart series for a time range. range is required (5m|15m|30m|1h|4h|12h|24h|7d). resource narrows to one resource id; without it all resources are returned downsampled. metric picks cpu|memory|disk|diskread|diskwrite|netin|netout (default all). Points are downsampled to at most maxPoints per metric (default 200).",
        "method": "GET",
        "path_template": "/charts",
        "params": [
            {"name": "range", "type": "string", "description": "Time range: 5m, 15m, 30m, 1h, 4h, 12h, 24h or 7d.", "required": True},
            {"name": "resource", "type": "string", "description": "Resource id to narrow to (optional).", "required": False, "local": True},
            {"name": "metric", "type": "string", "description": "One metric to return (cpu, memory, disk, diskread, diskwrite, netin, netout). Default all.", "required": False, "local": True},
            {"name": "maxPoints", "type": "integer", "description": "Max points per metric after downsampling (default 200).", "required": False, "default": 200, "local": True},
        ],
        "transform": "pulse_get_charts",
        "example": '{"pve:pve3:104": {"cpu": {"points": [{"t": 1724170000, "v": 3.2}]}}}',
        "read_only": True,
    },
    {
        "name": "list_backups",
        "description": "List backups merged from PBS backups and recent backup tasks, newest first. vmid filters to one guest; limit bounds the result. Returns vmid, source, time, size, protected, verified, datastore, status.",
        "method": "GET",
        "path_template": "/backups/unified",
        "params": [
            {"name": "vmid", "type": "integer", "description": "Guest VMID to filter to (optional).", "required": False, "local": True},
            {"name": "limit", "type": "integer", "description": "Max results (default 50).", "required": False, "default": 50, "local": True},
        ],
        "transform": "pulse_list_backups",
        "example": '[{"vmid": 104, "source": "pbs", "time": 1724170000, "size": 8589934592, "protected": true, "verified": true, "datastore": "backups", "status": "ok"}]',
        "read_only": True,
    },
    {
        "name": "list_storage",
        "description": "List monitored storage pools (type=storage resources) with used/total/free bytes and percent. Use search (name/id substring) and limit to narrow. Prefer over the raw /api/storage endpoint (400s without an id).",
        "method": "GET",
        "path_template": "/resources",
        "params": [],
        "fields": ["id", "name", "status", "used", "free", "total", "pct"],
        "search_field": "name",
        "transform": "pulse_list_storage",
        "example": '[{"id": "prox-cluster-cluster-pbs_backups", "name": "pbs_backups", "status": "online", "used": 1099511627776, "total": 4398046511104, "pct": 25}]',
        "read_only": True,
    },
    {
        "name": "list_nodes",
        "description": "List configured Pulse nodes / Proxmox clusters. Credentials are never included. full=true adds per-endpoint cluster endpoints (node, ip, online, pulseReachable, error).",
        "method": "GET",
        "path_template": "/config/nodes",
        "params": [],
        "fields": ["id", "type", "name", "host", "status", "isCluster", "clusterName"],
        "transform": "pulse_list_nodes",
        "example": '[{"id": "pve-0", "type": "pve", "name": "pve", "host": "https://192.168.1.215:8006", "status": "connected", "isCluster": true, "clusterName": "prox-cluster"}]',
        "read_only": True,
    },
    {
        "name": "connection_health",
        "description": "Map of node/host id to whether Pulse can currently reach it. Projected to connectionHealth only — the full /api/state payload is never returned.",
        "method": "GET",
        "path_template": "/state",
        "params": [],
        "fields": ["connectionHealth"],
        "example": '{"connectionHealth": {"pve": true}}',
        "read_only": True,
    },

    # ── Write tools (gated per the integration's approval policy) ────────
    {
        "name": "acknowledge_alert",
        "description": "Acknowledge a single alert by id. Requires operator approval per the integration's gating policy.",
        "method": "POST",
        "path_template": "/alerts/acknowledge",
        "params": [
            {"name": "id", "type": "string", "description": "Alert id (from pulse_list_alerts).", "required": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "acknowledge_alerts_bulk",
        "description": "Acknowledge multiple alerts in one call. ids should be a JSON array of alert ids. Requires operator approval per the integration's gating policy.",
        "method": "POST",
        "path_template": "/alerts/bulk/acknowledge",
        "params": [
            {"name": "ids", "type": "json", "description": "JSON array of alert ids to acknowledge.", "required": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "add_node",
        "description": "Register a new node/cluster for Pulse monitoring (host, type, name, and either token or password for the API). Credentials are redacted from audit output. Requires operator approval per the integration's gating policy.",
        "method": "POST",
        "path_template": "/config/nodes",
        "params": [
            {"name": "host", "type": "string", "description": "Node host URL (e.g. https://192.168.1.215:8006).", "required": True},
            {"name": "type", "type": "string", "description": "Node type (e.g. pve).", "required": False, "default": "pve"},
            {"name": "name", "type": "string", "description": "Display name for the node.", "required": False},
            {"name": "token", "type": "string", "description": "API token (PAM token) for the node.", "required": False, "redact": True},
            {"name": "password", "type": "string", "description": "Password for the node API (alternative to token).", "required": False, "redact": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "update_node",
        "description": "Update an existing node's settings (host, name, credentials). Credentials are redacted from audit output. Requires operator approval per the integration's gating policy.",
        "method": "PUT",
        "path_template": "/config/nodes/{id}",
        "params": [
            {"name": "id", "type": "string", "description": "Node id (from pulse_list_nodes).", "required": True},
            {"name": "host", "type": "string", "description": "Node host URL.", "required": False},
            {"name": "name", "type": "string", "description": "Display name.", "required": False},
            {"name": "token", "type": "string", "description": "API token (PAM token) for the node.", "required": False, "redact": True},
            {"name": "password", "type": "string", "description": "Password for the node API (alternative to token).", "required": False, "redact": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "remove_node",
        "description": "Remove a node from Pulse monitoring. DESTRUCTIVE — requires operator approval.",
        "method": "DELETE",
        "path_template": "/config/nodes/{id}",
        "params": [
            {"name": "id", "type": "string", "description": "Node id (from pulse_list_nodes).", "required": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "test_node_connection",
        "description": "Test connectivity to a node config without persisting it. Credentials are redacted from audit output. Requires operator approval per the integration's gating policy.",
        "method": "POST",
        "path_template": "/config/nodes/test-connection",
        "params": [
            {"name": "host", "type": "string", "description": "Node host URL.", "required": True},
            {"name": "type", "type": "string", "description": "Node type.", "required": False, "default": "pve"},
            {"name": "token", "type": "string", "description": "API token.", "required": False, "redact": True},
            {"name": "password", "type": "string", "description": "Password.", "required": False, "redact": True},
        ],
        "example": '{"ok": true}',
        "read_only": False,
    },
    {
        "name": "discover",
        "description": "Discover nodes/hosts on the LAN. Requires a Pulse token with settings:write scope (otherwise the API returns missing_scope). Declared as a write because of that scope requirement — gated per the integration's approval policy.",
        "method": "GET",
        "path_template": "/discover",
        "params": [],
        "example": '{"hosts": []}',
        "read_only": False,
    },

    # ── FUTURE (v6 upgrade markers — declared, not implemented) ──────────
    {"name": "list_findings", "description": "[v6] List security/health findings. Not implemented yet — declared as a roadmap marker.", "method": "GET", "path_template": "/findings", "params": [], "example": "[]", "read_only": True, "not_implemented": True},
    {"name": "ack_finding", "description": "[v6] Acknowledge a finding. Not implemented yet.", "method": "POST", "path_template": "/findings/ack", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "snooze_finding", "description": "[v6] Snooze a finding. Not implemented yet.", "method": "POST", "path_template": "/findings/snooze", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "dismiss_finding", "description": "[v6] Dismiss a finding. Not implemented yet.", "method": "POST", "path_template": "/findings/dismiss", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "resolve_finding", "description": "[v6] Resolve a finding. Not implemented yet.", "method": "POST", "path_template": "/findings/resolve", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "plan_action", "description": "[v6] Plan a remediation action. Not implemented yet.", "method": "POST", "path_template": "/actions/plan", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "decide_action", "description": "[v6] Decide on a proposed action. Not implemented yet.", "method": "POST", "path_template": "/actions/decide", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "execute_action", "description": "[v6] Execute a remediation action. Not implemented yet.", "method": "POST", "path_template": "/actions/execute", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "get_operator_state", "description": "[v6] Get operator state. Not implemented yet.", "method": "GET", "path_template": "/operator/state", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "set_operator_state", "description": "[v6] Set operator state. Not implemented yet.", "method": "POST", "path_template": "/operator/state", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
    {"name": "clear_operator_state", "description": "[v6] Clear operator state. Not implemented yet.", "method": "DELETE", "path_template": "/operator/state", "params": [], "example": "{}", "read_only": True, "not_implemented": True},
]


def seed_pulse_tools(integration_id: int):
    """Idempotently insert/refresh the curated Pulse seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in PULSE_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                search_field=tool.get('search_field'),
                transform=tool.get('transform'),
                not_implemented=tool.get('not_implemented'),
                example=tool['example'],
                read_only=tool['read_only'],
                seeded=True,
            )
            updated += 1
        else:
            create_tool(
                integration_id,
                tool['name'],
                tool['description'],
                tool['method'],
                tool['path_template'],
                tool['params'],
                tool['example'],
                read_only=tool['read_only'],
                fields=tool.get('fields'),
                search_field=tool.get('search_field') or '',
                transform=tool.get('transform') or '',
                not_implemented=bool(tool.get('not_implemented')),
                seeded=True,
            )
            created += 1
    return created, updated
