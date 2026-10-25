"""Curated seed catalog for Seerr (Overseerr/Jellyseerr successor).

Seerr is the request layer in front of Sonarr/Radarr. It is *not* a
replacement for the *arr read tools: Seerr exposes season-level availability,
while Sonarr/Radarr expose per-episode `hasFile`/monitored and the
`wanted/missing` view.

Auth is the Seerr main API key via the `X-Api-Key` header. The API key is
admin-level, so `request_create` runs immediately and Seerr auto-approves the
request (the operator accepted this). The tool only exposes safe request
fields — no `is4k`, `userId`, `ignoreQuota`, `rootFolder`, `profileId` or
`serverId`.
"""

SEERR_ERROR_CODES = {
    '400': 'invalid_request',
    '401': 'invalid_key',
    '403': 'forbidden',
    '404': 'not_found',
    '409': 'conflict',
    '500': 'seerr_unavailable',
    '502': 'seerr_unavailable',
    '503': 'seerr_unavailable',
    '504': 'seerr_unavailable',
}

SEERR_SEED_TOOLS = [
    {
        "name": "search",
        "description": "Search Seerr/TMDB for movies and TV shows. Returns id, mediaType, title/name, year, overview and mediaInfo.status (1 unknown, 2 pending, 3 processing, 4 partially available, 5 available, 6 deleted). Use the returned id as mediaId for request_create.",
        "method": "GET",
        "path_template": "/search",
        "params": [
            {"name": "query", "type": "string", "description": "Title to search for.", "required": True},
            {"name": "page", "type": "integer", "description": "Page (1-based).", "required": False, "default": 1},
            {"name": "language", "type": "string", "description": "Optional TMDB language (e.g. en).", "required": False},
        ],
        "transform": "seerr_search",
        "error_codes": SEERR_ERROR_CODES,
        "example": '{"results": [{"id": 10378, "mediaType": "movie", "title": "Big Buck Bunny", "year": "2008", "mediaStatus": 5}]}',
        "read_only": True,
    },
    {
        "name": "movie",
        "description": "Get full Seerr details for one movie, including mediaInfo.status (availability/request state), overview and runtime.",
        "method": "GET",
        "path_template": "/movie/{movieId}",
        "params": [
            {"name": "movieId", "type": "integer", "description": "TMDB movie id (from seerr_search).", "required": True},
            {"name": "language", "type": "string", "description": "Optional TMDB language (e.g. en).", "required": False},
        ],
        "transform": "seerr_movie",
        "error_codes": SEERR_ERROR_CODES,
        "example": '{"id": 10378, "title": "Big Buck Bunny", "releaseDate": "2008-04-10", "mediaStatus": 5}',
        "read_only": True,
    },
    {
        "name": "tv",
        "description": "Get full Seerr details for one TV show, including mediaInfo.status and per-season status (1 unknown, 2 pending, 3 processing, 4 partially available, 5 available, 6 deleted).",
        "method": "GET",
        "path_template": "/tv/{tvId}",
        "params": [
            {"name": "tvId", "type": "integer", "description": "TMDB TV id (from seerr_search).", "required": True},
            {"name": "language", "type": "string", "description": "Optional TMDB language (e.g. en).", "required": False},
        ],
        "transform": "seerr_tv",
        "error_codes": SEERR_ERROR_CODES,
        "example": '{"id": 33050, "name": "Pioneer One", "mediaStatus": 4, "seasons": [{"seasonNumber": 1, "status": 5}]}',
        "read_only": True,
    },
    {
        "name": "requests",
        "description": "List Seerr requests (newest first). Returns id, status (1 pending, 2 approved, 3 declined), mediaType, tmdbId, mediaStatus, requestedBy and createdAt. Use this to report on a request created earlier.",
        "method": "GET",
        "path_template": "/request",
        "params": [
            {"name": "take", "type": "integer", "description": "Page size.", "required": False, "default": 20},
            {"name": "skip", "type": "integer", "description": "Rows to skip.", "required": False, "default": 0},
            {"name": "filter", "type": "string", "description": "all, open or resolved (default all).", "required": False, "default": "all"},
            {"name": "sort", "type": "string", "description": "added or modified (default added).", "required": False, "default": "added"},
        ],
        "transform": "seerr_requests",
        "error_codes": SEERR_ERROR_CODES,
        "example": '{"results": [{"id": 1, "status": 2, "mediaType": "movie", "tmdbId": 10378}]}',
        "read_only": True,
    },
    {
        "name": "request_create",
        "description": "Create a Seerr request for a movie or TV show. For a movie pass mediaType='movie' and the TMDB id. For a TV show pass mediaType='tv' and the TMDB id; omit seasons to request all seasons, or pass a list of season numbers (e.g. [1,2]). Seerr applies its defaults and (with the main API key) auto-approves the request.",
        "method": "POST",
        "path_template": "/request",
        "params": [
            {"name": "mediaType", "type": "string", "description": "'movie' or 'tv'.", "required": True},
            {"name": "mediaId", "type": "integer", "description": "TMDB id from seerr_search.", "required": True},
            {"name": "seasons", "type": "array", "description": "TV only: season numbers, e.g. [1,2]. Omit to request all seasons.", "required": False},
        ],
        "handler": "seerr_request_create",
        "error_codes": SEERR_ERROR_CODES,
        "example": '{"id": 1, "status": 2, "media": {"mediaType": "movie", "tmdbId": 10378}}',
        "read_only": False,
        "always_gate": False,
    },
]


def seed_seerr_tools(integration_id: int):
    """Idempotently insert/refresh the curated Seerr tools for an integration."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in SEERR_SEED_TOOLS:
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
                filter_fields=tool.get('filter_fields'),
                transport=tool.get('transport', 'http'),
                version=tool.get('version', 'v1'),
                strip_envelope=tool.get('strip_envelope'),
                transform=tool.get('transform'),
                handler=tool.get('handler'),
                always_gate=bool(tool.get('always_gate')),
                error_codes=tool.get('error_codes'),
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
                search_field=tool.get('search_field') or '',
                filter_fields=tool.get('filter_fields'),
                transport=tool.get('transport', 'http'),
                version=tool.get('version', 'v1'),
                strip_envelope=tool.get('strip_envelope'),
                transform=tool.get('transform') or '',
                handler=tool.get('handler') or '',
                always_gate=bool(tool.get('always_gate')),
                error_codes=tool.get('error_codes'),
                path_variants=tool.get('path_variants'),
                response_hint=tool.get('response_hint'),
                seeded=True,
            )
            created += 1
    return created, updated
