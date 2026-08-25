"""Curated seed catalogs for Sonarr and Radarr (the *arr apps, /api/v3).

Auth is an `X-Api-Key` header (the legacy ?apikey= query param is gone in
v4/v5) — integrations use auth_type `header` / auth_header_name `X-Api-Key`.
One integration record per app (sonarr, radarr); the MCP surface namespaces
each tool set by the integration name (sonarr_series, radarr_movies, ...).

Both apps share one API shape, so the catalog is built from a single
parameterized definition that differs only where the API does (series vs
movie endpoints/fields, and the search-on-add flag name).

GUARDRAIL (hard): every write that can trigger torrent searches or file
deletions defaults OFF and stays visible in the approval card —
searchForMissingEpisodes / searchOnAdd default false (server-merged into the
body), deleteFiles / removeFromClient / blocklist default false. All writes
are always approval-gated.
"""

ARR_ERROR_CODES = {
    '400': 'invalid_request',
    '401': 'invalid_key',
    '403': 'forbidden',
    '404': 'not_found',
    '500': 'arr_unavailable',
    '502': 'arr_unavailable',
    '503': 'arr_unavailable',
    '504': 'arr_unavailable',
}


def _build_catalog(kind: str) -> list:
    sonarr = kind == 'sonarr'
    series_path = '/api/v3/series' if sonarr else '/api/v3/movie'
    series_tool = 'series' if sonarr else 'movies'
    search_flag = 'searchForMissingEpisodes' if sonarr else 'searchOnAdd'
    add_name = 'add_series' if sonarr else 'add_movie'
    update_name = 'update_series' if sonarr else 'update_movie'
    delete_name = 'delete_series' if sonarr else 'delete_movie'
    singular = 'series' if sonarr else 'movie'
    plural = 'series' if sonarr else 'movies'

    # ── Read tools ──────────────────────────────────────────────────────
    reads = [
        {
            "name": "system_status",
            "description": "Version + status of this *arr app (appName, version, branch, isDocker, startTime). Use this to confirm the Sonarr/Radarr major version.",
            "method": "GET",
            "path_template": "/api/v3/system/status",
            "params": [],
            "transform": "arr_system_status",
            "error_codes": ARR_ERROR_CODES,
            "example": '{"appName": "Sonarr", "version": "4.0.16.2944", "branch": "main", "isDocker": true}',
            "read_only": True,
        },
        {
            "name": series_tool,
            "description": f"List {plural} (id, title, year, status, monitored, qualityProfileId, language, path, tags, statistics). Raw payloads are heavy — projected compact. Use search (title substring) and limit; full=true adds overview/added.",
            "method": "GET",
            "path_template": series_path,
            "params": [],
            "fields": ["id", "title", "year", "status", "monitored", "qualityProfileId"],
            "search_field": "title",
            "transform": "arr_series" if sonarr else "arr_movies",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 5, "title": "Bluey", "year": 2018, "status": "continuing", "monitored": true}]',
            "read_only": True,
        },
        {
            "name": "queue",
            "description": "Download queue (paginated). total + records with id, title, status, trackedDownloadStatus, errorMessage, sizeleft, timeleft — the stuck-download diagnosis.",
            "method": "GET",
            "path_template": "/api/v3/queue",
            "params": [
                {"name": "page", "type": "integer", "description": "Page (1-based).", "required": False, "default": 1},
                {"name": "pageSize", "type": "integer", "description": "Page size (max 100).", "required": False, "default": 20},
            ],
            "transform": "arr_queue",
            "error_codes": ARR_ERROR_CODES,
            "example": '{"total": 3, "records": [{"id": 10, "title": "Show S01E01", "status": "downloadClientUnavailable", "errorMessage": "connection refused"}]}',
            "read_only": True,
        },
        {
            "name": "history",
            "description": "Recent history events (paginated). total + records with id, eventType (grabbed/imported/deleted/failed), title, date, quality, indexer, language.",
            "method": "GET",
            "path_template": "/api/v3/history",
            "params": [
                {"name": "page", "type": "integer", "description": "Page (1-based).", "required": False, "default": 1},
                {"name": "pageSize", "type": "integer", "description": "Page size (max 100).", "required": False, "default": 20},
            ],
            "transform": "arr_history",
            "error_codes": ARR_ERROR_CODES,
            "example": '{"total": 1, "records": [{"id": 1, "eventType": "grabbed", "title": "Show S01E01", "quality": "HDTV-720p", "indexer": "Nyaa"}]}',
            "read_only": True,
        },
        {
            "name": "quality_profiles",
            "description": "Quality profiles (id, name, cutoff, items with allowed flags). Powers the language/dub profile workflows.",
            "method": "GET",
            "path_template": "/api/v3/qualityprofile",
            "params": [],
            "transform": "arr_quality_profiles",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 1, "name": "HD-1080p", "cutoff": 3, "items": [{"name": "HDTV-720p", "allowed": true}]}]',
            "read_only": True,
        },
        {
            "name": "custom_formats",
            "description": "Custom formats (id, name, includeCustomFormatWhenRenaming, specifications: implementation + negate). Use search by name.",
            "method": "GET",
            "path_template": "/api/v3/customformat",
            "params": [],
            "search_field": "name",
            "transform": "arr_custom_formats",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 3, "name": "PT-PT Dub", "includeCustomFormatWhenRenaming": false, "specifications": [{"implementation": "LanguageSpecification", "negate": false}]}]',
            "read_only": True,
        },
        {
            "name": "languages",
            "description": "Language id -> name map (id, name). PT-PT = 18 and PT-BR = 33 in Sonarr v4, but read the live list to be sure. Radarr exposes movie languages.",
            "method": "GET",
            "path_template": "/api/v3/language",
            "params": [],
            "transform": "arr_languages",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 1, "name": "English"}, {"id": 18, "name": "Portuguese (PT)"}]',
            "read_only": True,
        },
        {
            "name": "rootfolders",
            "description": "Root folders (id, path, accessible, freeSpace) — quick disk-capacity check for the *arr media paths.",
            "method": "GET",
            "path_template": "/api/v3/rootfolder",
            "params": [],
            "transform": "arr_rootfolders",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 1, "path": "/media/series", "accessible": true, "freeSpace": 1099511627776}]',
            "read_only": True,
        },
        {
            "name": "command_status",
            "description": "Poll a previously-run command by id (from a command write). Returns id, name, status (queued/started/completed/failed), started, ended, duration. Command POSTs are fire-and-forget — poll this to confirm completion.",
            "method": "GET",
            "path_template": "/api/v3/command/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": "Command id (returned by the command write tool).", "required": True},
            ],
            "transform": "arr_command_status",
            "error_codes": ARR_ERROR_CODES,
            "example": '{"id": 42, "name": "RefreshSeries", "status": "completed"}',
            "read_only": True,
        },
    ]

    # ── Write tools (always approval-gated) ─────────────────────────────
    writes = [
        {
            "name": add_name,
            "description": f"Add a {singular} to this *arr app. `body` is the full {singular} object (GET one first for the exact shape). ⚠️ GUARDRAIL: {search_flag} defaults FALSE and is server-merged — a search triggers torrent grabs; only pass true deliberately. REQUIRES OPERATOR APPROVAL.",
            "method": "POST",
            "path_template": series_path,
            "params": [
                {"name": "body", "type": "json", "description": f"Full {singular} object to add.", "required": True},
                {"name": search_flag, "type": "boolean", "description": "Search for missing releases on add (default false — a true value triggers torrent searches).", "required": False, "default": False},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{}',
            "read_only": False,
        },
        {
            "name": update_name,
            "description": f"Update a {singular} by id. `body` must be the FULL {singular} object (v4 validates the whole payload; partial PUTs 400) — GET the {singular} first, mutate one field, PUT it back. REQUIRES OPERATOR APPROVAL.",
            "method": "PUT",
            "path_template": series_path + "/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": f"{singular.capitalize()} id (from the list tool).", "required": True},
                {"name": "body", "type": "json", "description": "Full object to PUT (GET-current -> mutate -> PUT).", "required": True},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{}',
            "read_only": False,
        },
        {
            "name": "command",
            "description": "Run a named *arr command (POST /api/v3/command) — e.g. RefreshSeries, RescanSeries, EpisodeSearch, MovieSearch, RSS Sync. `name` is required; `data` is an optional JSON object of the command's params (e.g. seriesId, episodeIds). ⚠️ EpisodeSearch/MovieSearch trigger real torrent searches — be deliberate. Fire-and-forget: poll command_status for completion. REQUIRES OPERATOR APPROVAL.",
            "method": "POST",
            "path_template": "/api/v3/command",
            "params": [
                {"name": "name", "type": "string", "description": "Command name (RefreshSeries, RescanSeries, EpisodeSearch, MovieSearch, RSS Sync, ...).", "required": True},
                {"name": "data", "type": "json", "description": "Optional command parameters (JSON object).", "required": False},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{"name": "RefreshSeries", "seriesId": 5}',
            "read_only": False,
        },
        {
            "name": "remove_from_queue",
            "description": "Remove a queue item by id. removeFromClient=true deletes the download from the download client (qBittorrent); blocklist=true adds a blocklist entry. Both default false and are shown in the approval card. REQUIRES OPERATOR APPROVAL.",
            "method": "DELETE",
            "path_template": "/api/v3/queue/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": "Queue item id (from the queue tool).", "required": True},
                {"name": "removeFromClient", "type": "boolean", "description": "Delete the download from the download client (default false).", "required": False, "default": False, "in_query": True},
                {"name": "blocklist", "type": "boolean", "description": "Add the item to the blocklist (default false).", "required": False, "default": False, "in_query": True},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{}',
            "read_only": False,
        },
        {
            "name": delete_name,
            "description": f"Delete a {singular} by id. ⚠️ deleteFiles defaults FALSE (removes only the arr entry). deleteFiles=true DELETES MEDIA FROM DISK — explicit only. REQUIRES OPERATOR APPROVAL.",
            "method": "DELETE",
            "path_template": series_path + "/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": f"{singular.capitalize()} id (from the list tool).", "required": True},
                {"name": "deleteFiles", "type": "boolean", "description": "Also delete the media files from disk (default false — explicit only).", "required": False, "default": False, "in_query": True},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{}',
            "read_only": False,
        },
    ]

    return reads + writes


SONARR_SEED_TOOLS = _build_catalog('sonarr')
RADARR_SEED_TOOLS = _build_catalog('radarr')

_CATALOGS = {'sonarr': SONARR_SEED_TOOLS, 'radarr': RADARR_SEED_TOOLS}


def _seed_arr(integration_id: int, kind: str):
    """Idempotently insert/refresh the curated *arr seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in _CATALOGS[kind]:
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
                error_codes=tool.get('error_codes') or None,
                always_gate=bool(tool.get('always_gate')),
                seeded=True,
            )
            created += 1
    return created, updated


def seed_sonarr_tools(integration_id: int):
    return _seed_arr(integration_id, 'sonarr')


def seed_radarr_tools(integration_id: int):
    return _seed_arr(integration_id, 'radarr')
