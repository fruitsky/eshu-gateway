"""Curated seed catalog for Prowlarr (/api/v1, the *arr indexer manager).

Auth is an `X-Api-Key` header — same as Sonarr/Radarr (auth_type `header` /
auth_header_name `X-Api-Key`); one integration record per Prowlarr instance
(kind `prowlarr`, name-based MCP namespace gives `prowlarr_*`).

DEFINING CONSTRAINT: indexer definitions carry real credentials in `fields[]`
(API keys, usernames, passwords per indexer). The read projections NEVER
include `fields`, the scrubber drop-list strips `fields` from any body/audit/
approval-payload, and write approval cards show name/protocol/change only —
never field values. All writes are always approval-gated. No generic
passthrough is seeded (NO_GENERIC_KINDS) so a generic read can't surface
indexer detail or a generic write reach /search or /indexer/sync.
"""

PROWLARR_ERROR_CODES = {
    '400': 'invalid_request',
    '401': 'invalid_key',
    '403': 'forbidden',
    '404': 'not_found',
    '500': 'prowlarr_unavailable',
    '502': 'prowlarr_unavailable',
    '503': 'prowlarr_unavailable',
    '504': 'prowlarr_unavailable',
}

PROWLARR_SEED_TOOLS = [
    # ── Read tools ──────────────────────────────────────────────────────
    {
        "name": "system_status",
        "description": "Prowlarr version + status (version, appName, branch, startTime). Use this to confirm the Prowlarr major version.",
        "method": "GET",
        "path_template": "/api/v1/system/status",
        "params": [],
        "transform": "prowlarr_system_status",
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '{"version": "1.29.2", "appName": "Prowlarr", "branch": "master", "startTime": "2026-01-01T00:00:00Z"}',
        "read_only": True,
    },
    {
        "name": "indexers",
        "description": "List indexers (id, name, protocol, enable, priority, indexerFeedType, sortOrder, implementation). ⚠️ Indexer definitions carry credentials in fields[] — NEVER included here, even with full=true (search by name; limit bounds the result).",
        "method": "GET",
        "path_template": "/api/v1/indexer",
        "params": [],
        "fields": ["id", "name", "protocol", "enable", "priority"],
        "search_field": "name",
        "transform": "prowlarr_indexers",
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '[{"id": 1, "name": "Nyaa", "protocol": "torrent", "enable": true, "priority": 25, "implementation": "Cardigann"}]',
        "read_only": True,
    },
    {
        "name": "indexer_stats",
        "description": "Per-indexer query statistics: success/failure counts + totalQueries — which indexer is flaky. Returns {total, indexers:[{indexerName, success, failures, totalQueries}]}.",
        "method": "GET",
        "path_template": "/api/v1/indexerstats",
        "params": [],
        "transform": "prowlarr_indexer_stats",
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '{"total": 1, "indexers": [{"indexerName": "Nyaa", "success": 100, "failures": 2, "totalQueries": 102}]}',
        "read_only": True,
    },
    {
        "name": "indexer_status",
        "description": "Indexer health/liveness: disabledTill, mostRecentFailure, escalation, attemptedQueries — why an indexer is disabled/failing. A disabled indexer is degraded, not deleted.",
        "method": "GET",
        "path_template": "/api/v1/indexerstatus",
        "params": [],
        "transform": "prowlarr_indexer_status",
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '[{"indexerId": 1, "indexerName": "Nyaa", "disabledTill": "2026-08-21T12:00:00Z", "mostRecentFailure": "timeout", "escalation": 3, "attemptedQueries": 5}]',
        "read_only": True,
    },

    # ── Write tools (always approval-gated) ─────────────────────────────
    {
        "name": "add_indexer",
        "description": "Add an indexer. `body` is the full indexer definition (GET one first for the exact shape) and LEGITIMATELY carries credentials in fields[] — the approval card shows indexer name + protocol only, never field values. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/api/v1/indexer",
        "params": [
            {"name": "body", "type": "json", "description": "Full indexer definition (includes fields[] with credentials).", "required": True},
        ],
        "always_gate": True,
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "update_indexer",
        "description": "Update an indexer by id. `body` must be the FULL indexer definition (Prowlarr validates the whole payload; partial PUTs 400) — GET the indexer first, mutate, PUT it back. Credentials in fields[] are never shown on the approval card. REQUIRES OPERATOR APPROVAL.",
        "method": "PUT",
        "path_template": "/api/v1/indexer/{id}",
        "params": [
            {"name": "id", "type": "integer", "description": "Indexer id (from the indexers tool).", "required": True},
            {"name": "body", "type": "json", "description": "Full indexer definition to PUT (includes fields[] with credentials).", "required": True},
        ],
        "always_gate": True,
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "delete_indexer",
        "description": "Delete an indexer by id. REQUIRES OPERATOR APPROVAL.",
        "method": "DELETE",
        "path_template": "/api/v1/indexer/{id}",
        "params": [
            {"name": "id", "type": "integer", "description": "Indexer id (from the indexers tool).", "required": True},
        ],
        "always_gate": True,
        "error_codes": PROWLARR_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "sync_indexers",
        "description": "AppIndexerSync — pushes the current indexer config to Sonarr AND Radarr (cross-app state change; highest blast radius in the *arr family). REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/api/v1/indexer/sync",
        "params": [],
        "always_gate": True,
        "error_codes": PROWLARR_ERROR_CODES,
        "response_hint": "Indexers pushed to Sonarr AND Radarr. Verify each app sees the updated indexer list.",
        "example": '{}',
        "read_only": False,
    },
]


def seed_prowlarr_tools(integration_id: int):
    """Idempotently insert/refresh the curated Prowlarr seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in PROWLARR_SEED_TOOLS:
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
                error_codes=tool.get('error_codes'),
                always_gate=tool.get('always_gate'),
                response_hint=tool.get('response_hint'),
                example=tool['example'],
                read_only=tool['read_only'],
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
                error_codes=tool.get('error_codes') or None,
                always_gate=bool(tool.get('always_gate')),
                response_hint=tool.get('response_hint') or '',
            )
            created += 1
    return created, updated
