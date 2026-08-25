"""Curated seed catalog for Pi-hole v5 (/admin/api.php).

Auth is a query-param token (?auth=<token>) — the integration uses the
`query_token` auth type and its base_url ends at `/admin` (tools append
`api.php?...`). One integration record per Pi-hole instance: create one record
per box (e.g. pihole2, pihole3) and the MCP surface namespaces each tool set by
the integration name (pihole2_summary, pihole3_summary, ...).

Read tools are compact-by-default (transforms parse the comma-formatted string
numbers and map the v5 `[]`-on-bad-token fingerprint to invalid_token). The two
write tools are always approval-gated because toggling blocking is LAN-wide.
No generic passthrough is seeded (NO_GENERIC_KINDS).
"""

PIHOLE_ERROR_CODES = {
    '401': 'invalid_token',
    '403': 'forbidden',
    '404': 'not_found',
    '500': 'pihole_unavailable',
    '502': 'pihole_unavailable',
    '503': 'pihole_unavailable',
    '504': 'pihole_unavailable',
}

PIHOLE_SEED_TOOLS = [
    # ── Read tools ──────────────────────────────────────────────────────
    {
        "name": "summary",
        "description": "Summary for this Pi-hole instance: domains blocked, queries today, ads blocked + percentage, queries forwarded/cached, clients seen, blocking status and privacy level. Comma-formatted API numbers are parsed to integers. full=true adds gravity-last-updated, all-types/unique-domains counts and the reply_* breakdown.",
        "method": "GET",
        "path_template": "api.php?summary",
        "params": [],
        "fields": ["domainsBlocked", "queriesToday", "adsBlockedToday", "adsPct", "status"],
        "transform": "pihole_summary",
        "error_codes": PIHOLE_ERROR_CODES,
        "example": '{"domainsBlocked": 102364, "queriesToday": 355179, "adsBlockedToday": 1693, "adsPct": 0.5, "status": "enabled"}',
        "read_only": True,
    },
    {
        "name": "status",
        "description": "Blocking status for this Pi-hole instance (enabled/disabled). When disabled, the status text may include remaining-time info — passed through as-is.",
        "method": "GET",
        "path_template": "api.php?status",
        "params": [],
        "transform": "pihole_status",
        "error_codes": PIHOLE_ERROR_CODES,
        "example": '{"status": "enabled"}',
        "read_only": True,
    },
    {
        "name": "api_version",
        "description": "Pi-hole API version int for this instance (e.g. 3). This is the admin/api.php API version — NOT the core/web/FTL build numbers.",
        "method": "GET",
        "path_template": "api.php?version",
        "params": [],
        "transform": "pihole_api_version",
        "error_codes": PIHOLE_ERROR_CODES,
        "example": '{"apiVersion": 3}',
        "read_only": True,
    },

    # ── Write tools (always approval-gated — LAN-wide impact) ───────────
    {
        "name": "enable_blocking",
        "description": "Re-enable DNS blocking on THIS Pi-hole instance. Global — affects the WHOLE LAN's DNS for this box. REQUIRES OPERATOR APPROVAL.",
        "method": "GET",
        "path_template": "api.php?enable",
        "params": [],
        "always_gate": True,
        "error_codes": PIHOLE_ERROR_CODES,
        "response_hint": "Blocking re-enabled. Confirm via pihole_status or pihole_summary.",
        "example": '{"status": "enabled"}',
        "read_only": False,
    },
    {
        "name": "disable_blocking",
        "description": "Disable DNS blocking on THIS Pi-hole instance, optionally for a number of seconds (timed disable self-restores, e.g. durationSeconds=300 = 5 min). Global — affects the WHOLE LAN's DNS for this box. REQUIRES OPERATOR APPROVAL.",
        "method": "GET",
        "path_template": "api.php?disable",
        "path_variants": {"durationSeconds": "api.php?disable={durationSeconds}"},
        "params": [
            {"name": "durationSeconds", "type": "integer", "description": "Disable for this many seconds (timed disable auto-restores). Omit for an indefinite disable.", "required": False},
        ],
        "always_gate": True,
        "error_codes": PIHOLE_ERROR_CODES,
        "response_hint": "Blocking disabled. Re-enable via pihole_enable_blocking, or it will self-restore when a timed disable elapses.",
        "example": '{"status": "disabled"}',
        "read_only": False,
    },
]


def seed_pihole_tools(integration_id: int):
    """Idempotently insert/refresh the curated Pi-hole seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in PIHOLE_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                transform=tool.get('transform'),
                error_codes=tool.get('error_codes'),
                always_gate=tool.get('always_gate'),
                path_variants=tool.get('path_variants'),
                response_hint=tool.get('response_hint'),
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
                transform=tool.get('transform') or '',
                error_codes=tool.get('error_codes') or None,
                always_gate=bool(tool.get('always_gate')),
                path_variants=tool.get('path_variants') or None,
                response_hint=tool.get('response_hint') or '',
                seeded=True,
            )
            created += 1
    return created, updated
