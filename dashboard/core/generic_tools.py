"""Generic passthrough tools — the "no blocks" floor.

Every integration gets a generic HTTP read/write pair so an agent can reach any
endpoint the upstream API offers, even ones not hand-curated in a seed catalog.
HA additionally gets generic WebSocket read/write tools. These are seeded via
`core.seeds` like any other tool (idempotent, preserves enabled state), so they
show up in the Tools UI, are audited, and route through the gating policy.
"""


# Kind -> API prefix the server injects for generic calls, so the agent writes
# paths relative to the API root (/episodefile) rather than the origin. Kept in
# sync with core.integration_proxy._KIND_API_PREFIX.
_API_PREFIX = {
    'sonarr': '/api/v3',
    'radarr': '/api/v3',
    'prowlarr': '/api/v1',
    'bazarr': '/api',
}

# Kinds that get ONLY the generic read passthrough. Their writes stay fully
# curated because an un-curated *arr write can trigger torrent searches or
# delete media from disk. (The generic `write` is still gated, but it bypasses
# the curated guardrails — default-off search flags, full-object PUTs — so it
# is not seeded for these kinds.)
READ_ONLY_GENERIC_KINDS = {'sonarr', 'radarr', 'prowlarr', 'jellyfin'}

# Kinds whose generic `write` must ALWAYS be approval-gated regardless of the
# integration's gate_mode. Bazarr: any write can trigger subtitle
# downloads/searches. Home Assistant: the generic write is an escape hatch to
# every admin endpoint (config/registry/restart), so it must never auto-run.
ALWAYS_GATE_GENERIC_WRITE_KINDS = {'bazarr', 'ha'}


def generic_tools_for(kind: str) -> list:
    prefix = _API_PREFIX.get(kind or '')
    read_desc = (
        "Call any read endpoint on this integration. `path` is relative to the "
        "integration's base URL (e.g. /states); `params` is an optional JSON "
        "object of query parameters. `method` is GET (default) or HEAD — HEAD "
        "returns headers-only metadata ({status, content_length, content_type, "
        "url}) with no body, e.g. to check a media file's size. Credentials are "
        "injected by Eshu; every call is audited."
    )
    if prefix:
        read_desc += (
            f" This integration's API lives under {prefix}: pass paths relative "
            f"to that root (e.g. /episodefile), not the full {prefix} path.")
    if kind == 'omada':
        read_desc += (" Omada list endpoints require page/pageSize — defaults "
                      "(page=1, pageSize=50) are injected when omitted, so list "
                      "reads work without passing them.")
    write_desc = (
        "Call any mutating endpoint on this integration. `method` is "
        "POST/PUT/PATCH/DELETE, `path` is relative to the base URL, `params` is "
        "an optional query-parameter object, `data` is an optional body object "
        "(sent as JSON, or form-encoded for integrations that require it, e.g. "
        "Bazarr /system/*). Credentials are injected by Eshu; every call is "
        "audited, and the call is gated per this integration's approval policy. "
        "⚠️ A 2xx with an EMPTY response body is NOT proof the change was "
        "applied — read the target value back. Pass `verify_path` to have the "
        "server GET that path after a successful write and return it."
    )
    tools = [
        {
            "name": "read",
            "description": read_desc,
            "method": "GET",
            "path_template": "",
            "params": [
                {"name": "method", "type": "string", "description": "GET or HEAD (default GET). HEAD returns headers-only metadata with no body.", "required": False, "default": "GET"},
                {"name": "path", "type": "string", "description": "Endpoint path relative to the base URL (or to the host origin when root=true).", "required": True},
                {"name": "params", "type": "json", "description": "Optional query parameters (JSON object).", "required": False},
                {"name": "root", "type": "boolean", "description": "Resolve path against the host origin (scheme://host:port) instead of the integration's base URL — for resources served at the origin root (e.g. Home Assistant media at /media/local/<file>). Traversal is still blocked.", "required": False},
            ],
            "fields": [],
            "read_only": True,
            "transport": "http",
            "generic": True,
        },
        {
            "name": "write",
            "description": write_desc,
            "method": "POST",
            "path_template": "",
            "params": [
                {"name": "method", "type": "string", "description": "HTTP method: POST, PUT, PATCH or DELETE.", "required": True, "default": "POST"},
                {"name": "path", "type": "string", "description": "Endpoint path relative to the base URL.", "required": True},
                {"name": "params", "type": "json", "description": "Optional query parameters (JSON object).", "required": False},
                {"name": "data", "type": "json", "description": "Optional request body (JSON object; form-encoded when the integration requires it).", "required": False},
                {"name": "verify_path", "type": "string", "description": "Optional path relative to the base URL that the server GETs after a successful write and returns as `verify` — the evidence the change stuck.", "required": False},
            ],
            "fields": [],
            "read_only": False,
            "transport": "http",
            "generic": True,
            "always_gate": kind in ALWAYS_GATE_GENERIC_WRITE_KINDS,
        },
    ]
    if kind in READ_ONLY_GENERIC_KINDS:
        tools = [t for t in tools if t['name'] == 'read']
    if kind == 'ha':
        tools.extend([
            {
                "name": "ws_read",
                "description": "Call any read Home Assistant WebSocket command (e.g. config/entity_registry/list). `payload` is an optional JSON object of the command's fields. Audited.",
                "method": "GET",
                "path_template": "",
                "params": [
                    {"name": "command", "type": "string", "description": "HA WebSocket command type.", "required": True},
                    {"name": "payload", "type": "json", "description": "Optional command payload (JSON object).", "required": False},
                ],
                "fields": [],
                "read_only": True,
                "transport": "ws",
                "generic": True,
            },
            {
                "name": "ws_write",
                "description": "Call any mutating Home Assistant WebSocket command (e.g. config/device_registry/remove, call_service). `payload` is an optional JSON object of the command's fields. Gated per the integration's approval policy.",
                "method": "POST",
                "path_template": "",
                "params": [
                    {"name": "command", "type": "string", "description": "HA WebSocket command type.", "required": True},
                    {"name": "payload", "type": "json", "description": "Optional command payload (JSON object).", "required": False},
                ],
                "fields": [],
                "read_only": False,
                "transport": "ws",
                "generic": True,
                "always_gate": True,
            },
        ])
    return tools


def seed_generic_tools(integration_id: int, kind: str):
    """Idempotently insert/refresh the generic tools for an integration.
    Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in generic_tools_for(kind):
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                search_field='',
                filter_fields=None,
                transport=tool['transport'],
                generic=True,
                always_gate=tool.get('always_gate'),
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
                '',
                read_only=tool['read_only'],
                fields=tool.get('fields'),
                transport=tool['transport'],
                generic=True,
                always_gate=bool(tool.get('always_gate')),
                seeded=True,
            )
            created += 1
    return created, updated
