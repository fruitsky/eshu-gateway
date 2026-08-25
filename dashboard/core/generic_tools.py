"""Generic passthrough tools — the "no blocks" floor.

Every integration gets a generic HTTP read/write pair so an agent can reach any
endpoint the upstream API offers, even ones not hand-curated in a seed catalog.
HA additionally gets generic WebSocket read/write tools. These are seeded via
`core.seeds` like any other tool (idempotent, preserves enabled state), so they
show up in the Tools UI, are audited, and route through the gating policy.
"""


def generic_tools_for(kind: str) -> list:
    read_desc = (
        "Call any read endpoint on this integration. `path` is relative to the "
        "integration's base URL (e.g. /states); `params` is an optional JSON "
        "object of query parameters. `method` is GET (default) or HEAD — HEAD "
        "returns headers-only metadata ({status, content_length, content_type, "
        "url}) with no body, e.g. to check a media file's size. Credentials are "
        "injected by Eshu; every call is audited."
    )
    write_desc = (
        "Call any mutating endpoint on this integration. `method` is "
        "POST/PUT/PATCH/DELETE, `path` is relative to the base URL, `params` is "
        "an optional query-parameter object, `data` is an optional JSON body. "
        "Credentials are injected by Eshu; every call is audited, and the call "
        "is gated per this integration's approval policy."
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
                {"name": "data", "type": "json", "description": "Optional request body (JSON object).", "required": False},
            ],
            "fields": [],
            "read_only": False,
            "transport": "http",
            "generic": True,
        },
    ]
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
                seeded=True,
            )
            created += 1
    return created, updated
