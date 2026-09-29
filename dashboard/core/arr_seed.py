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

    # History event ids are SINGULAR on the wire: Sonarr v4 uses `episodeId`
    # (int), Radarr v6 uses `movieId` (int). (The command tool uses the plural
    # `episodeIds` array — that is a different payload; do not conflate them.)
    history_desc = (
        "Recent history events (paginated). total + records with id, eventType "
        "(grabbed/imported/deleted/failed), "
        + ("seriesId, episodeId, sourceTitle, title, date, quality, indexer, "
           "language. grabbed/imported rows carry sourceTitle (the release "
           "title) and episodeId — use these to prove WHICH episode an event "
           "was for and what release it actually was. full=true adds the raw "
           "data blob."
           if sonarr else
           "movieId, sourceTitle, title, date, quality, indexer, language. "
           "grabbed/imported rows carry sourceTitle (the release title) and "
           "movieId — use these to prove WHICH movie an event was for and what "
           "release it actually was. full=true adds the raw data blob.")
    )
    history_example = (
        '{"total": 1, "records": [{"id": 1, "eventType": "grabbed", "seriesId": 88, '
        '"episodeId": 3746, "sourceTitle": "Show S01E10 ...", "quality": "WEBDL-1080p", '
        '"indexer": "TorrentDay"}]}'
        if sonarr else
        '{"total": 1, "records": [{"id": 1, "eventType": "grabbed", "movieId": 86, '
        '"sourceTitle": "Movie 2026 ...", "quality": "Bluray-1080p", '
        '"indexer": "TorrentDay"}]}'
    )

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
            "description": history_desc,
            "method": "GET",
            "path_template": "/api/v3/history",
            "params": [
                {"name": "page", "type": "integer", "description": "Page (1-based).", "required": False, "default": 1},
                {"name": "pageSize", "type": "integer", "description": "Page size (max 100).", "required": False, "default": 20},
                {"name": "seriesId" if sonarr else "movieId", "type": "integer",
                 "description": ("Filter to one series." if sonarr else "Filter to one movie."), "required": False},
                {"name": "full", "type": "boolean", "description": "Include the raw per-record data blob.", "required": False, "local": True},
            ],
            "transform": "arr_history",
            "error_codes": ARR_ERROR_CODES,
            "example": history_example,
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
            "description": "Custom formats (id, name, includeCustomFormatWhenRenaming, specifications: implementation + negate). Use search by name. full=true adds each specification's `fields` (the regex values — an array of {name, value} in v4).",
            "method": "GET",
            "path_template": "/api/v3/customformat",
            "params": [
                {"name": "full", "type": "boolean", "description": "Include specification fields (regex values).", "required": False, "local": True},
            ],
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

    # Per-episode / per-movie enriched reads (the incident's missing surface).
    if sonarr:
        reads.append({
            "name": "episodes",
            "description": "List episodes for a series (id, seasonNumber, episodeNumber, title, episodeFileId, hasFile, monitored, airDate). `search` filters on title, `limit` caps the list.",
            "method": "GET",
            "path_template": "/api/v3/episode",
            "params": [
                {"name": "seriesId", "type": "integer", "description": "Series id (from the series tool).", "required": True},
                {"name": "seasonNumber", "type": "integer", "description": "Optional season filter.", "required": False},
            ],
            "search_field": "title",
            "transform": "arr_episodes",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 3746, "seasonNumber": 1, "episodeNumber": 10, "title": "Outside", "hasFile": true, "episodeFileId": 1551}]',
            "read_only": True,
        })
        reads.append({
            "name": "episode_files",
            "description": "Episode files for a series with per-file media info — sceneName, releaseGroup, languages, quality, customFormats + customFormatScore, and mediaInfo (audioCodec, audioStreamCount, subtitles, resolution, runTime). This is the file that distinguishes an Audio-Description rip from a normal one. `search` filters sceneName; `full` adds size/path/dateAdded.",
            "method": "GET",
            "path_template": "/api/v3/episodefile",
            "params": [
                {"name": "seriesId", "type": "integer", "description": "Series id (from the series tool).", "required": True},
            ],
            "fields": ["id", "sceneName"],
            "search_field": "sceneName",
            "transform": "arr_episode_files",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 1551, "sceneName": "Silo S01E10 ... Audio Description ...", "releaseGroup": "Kitsune", "customFormatScore": 0, "mediaInfo": {"audioStreamCount": 1, "subtitles": ""}}]',
            "read_only": True,
        })
    else:
        reads.append({
            "name": "movie_files",
            "description": "Movie files for a movie with per-file media info — sceneName, releaseGroup, languages, quality, customFormats + customFormatScore, and mediaInfo (audioCodec, audioStreamCount, subtitles, resolution, runTime). `search` filters sceneName; `full` adds size/path/dateAdded.",
            "method": "GET",
            "path_template": "/api/v3/moviefile",
            "params": [
                {"name": "movieId", "type": "integer", "description": "Movie id (from the movies tool).", "required": True},
            ],
            "fields": ["id", "sceneName"],
            "search_field": "sceneName",
            "transform": "arr_movie_files",
            "error_codes": ARR_ERROR_CODES,
            "example": '[{"id": 7, "sceneName": "Jaws.1975.1080p...", "releaseGroup": "NTb", "customFormatScore": 0, "mediaInfo": {"audioStreamCount": 1}}]',
            "read_only": True,
        })
    reads.append({
        "name": "release_search",
        "description": "⚠️ SLOW — triggers live indexer queries and can take up to ~60s. Search indexers for releases for ONE episode/movie. Returns per release: title, size, quality, indexer, seeders, customFormatScore, rejected, and rejections. The rejections are the definitive \"why didn't it grab/upgrade\" evidence (e.g. \"Existing file on disk has a equal or higher Custom Format score\"). READ-ONLY: does NOT grab. Do not call in a tight loop.",
        "method": "GET",
        "path_template": "/api/v3/release",
        "params": [
            {"name": "episodeId" if sonarr else "movieId", "type": "integer",
             "description": ("Episode id to search for." if sonarr else "Movie id to search for."), "required": True},
        ],
        "search_field": "title",
        "transform": "arr_release_search",
        "error_codes": ARR_ERROR_CODES,
        "timeout": 120,
        "example": '[{"title": "Show S01E10 1080p WEBDL-NTb", "size": 3660000000, "quality": "WEBDL-1080p", "indexer": "TorrentDay", "seeders": 41, "customFormatScore": 0, "rejected": false, "rejections": []}]',
        "read_only": True,
    })

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
        {
            "name": "custom_format_create",
            "description": "Create a Custom Format. `specifications` is a list of spec objects ({implementation, negate?, required?, fields}). `fields` may be given as the older flat {name: value} dict or the v4 array form — the server NORMALISES it to the v4 array shape ([{order, name, value}]) that Sonarr v4 / Radarr v5 require (a flat dict makes them 400). Typical use: create the AD/blocked CF here, then score it at -10000 via quality_profile_update. REQUIRES OPERATOR APPROVAL.",
            "method": "POST",
            "path_template": "/api/v3/customformat",
            "params": [
                {"name": "name", "type": "string", "description": "Custom format name.", "required": True},
                {"name": "includeCustomFormatWhenRenaming", "type": "boolean", "description": "Include the CF in rename tokens (default false).", "required": False, "default": False},
                {"name": "specifications", "type": "array", "description": "List of specification objects: implementation, negate, required, fields (dict or list).", "required": True},
            ],
            "handler": "arr_custom_format_create",
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{"name": "Audio Description", "specifications": [{"implementation": "ReleaseTitleSpecification", "fields": {"value": "\\\\bAudio Description\\\\b"}}]}',
            "read_only": False,
        },
        {
            "name": "custom_format_update",
            "description": "Update a Custom Format by id. `body` must be the FULL CF object (GET it via the read passthrough, mutate, PUT) — partial PUTs 400. REQUIRES OPERATOR APPROVAL.",
            "method": "PUT",
            "path_template": "/api/v3/customformat/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": "Custom format id (from the custom_formats list).", "required": True},
                {"name": "body", "type": "json", "description": "Full CF object to PUT (GET-current -> mutate -> PUT).", "required": True},
            ],
            "always_gate": True,
            "error_codes": ARR_ERROR_CODES,
            "example": '{}',
            "read_only": False,
        },
        {
            "name": "quality_profile_update",
            "description": "Update a quality profile by id. `body` must be the FULL profile object (GET one via the read passthrough first, mutate, PUT) — partial PUTs 400. Set formatItems[].score to -10000 to block a Custom Format (releases matching it then score below minFormatScore and are never grabbed; an on-disk file re-scored to -10000 makes normal releases a strict upgrade). REQUIRES OPERATOR APPROVAL.",
            "method": "PUT",
            "path_template": "/api/v3/qualityprofile/{id}",
            "params": [
                {"name": "id", "type": "integer", "description": "Quality profile id.", "required": True},
                {"name": "body", "type": "json", "description": "Full quality profile object to PUT (GET-current -> mutate -> PUT).", "required": True},
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
                handler=tool.get('handler') or '',
                example=tool['example'],
                read_only=tool['read_only'],
                timeout=tool.get('timeout') or 0,
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
                handler=tool.get('handler') or '',
                timeout=tool.get('timeout') or 0,
                seeded=True,
            )
            created += 1
    return created, updated


def seed_sonarr_tools(integration_id: int):
    return _seed_arr(integration_id, 'sonarr')


def seed_radarr_tools(integration_id: int):
    return _seed_arr(integration_id, 'radarr')
