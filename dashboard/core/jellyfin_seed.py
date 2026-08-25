"""Curated seed catalog for Jellyfin (10.11.11).

Auth is a simple `X-Emby-Token: <api-key>` header (the UI profile pre-fills it).
The API key is admin-level and cannot be scoped down, so this catalog is
deliberately FULLY curated — no generic read/write passthrough is seeded
(`core.seeds.NO_GENERIC_KINDS`), and every write tool is `always_gate` (it
requires operator approval even under the `destructive` gate mode).

Responses are compact-by-default via transforms in `core.transforms` (the whole
API is PascalCase; transforms map to camelCase and unwrap the odd shapes like
`NowPlayingQueueFullItems[0]`). Errors use stable codes via each tool's
`error_codes` map (invalid_key / forbidden / not_found / method_not_allowed /
jellyfin_unavailable).
"""

JELLYFIN_ERROR_CODES = {
    '401': 'invalid_key',
    '403': 'forbidden',
    '404': 'not_found',
    '405': 'method_not_allowed',
    '500': 'jellyfin_unavailable',
    '502': 'jellyfin_unavailable',
    '503': 'jellyfin_unavailable',
    '504': 'jellyfin_unavailable',
}

JELLYFIN_SEED_TOOLS = [
    # ── Read tools ──────────────────────────────────────────────────────
    {
        "name": "system_info",
        "description": "Jellyfin server info (version, server name, OS, arch, cache/log/transcode/web paths, id). The raw /System/Info is ~35 KB — projected to a compact shape. full=true adds version name, OS display name and pending-restart flags.",
        "method": "GET",
        "path_template": "/System/Info",
        "params": [],
        "fields": ["version", "serverName", "os", "arch", "cachePath", "logPath", "transcodePath", "webPath", "id"],
        "transform": "jellyfin_system_info",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{"version": "10.11.11", "serverName": "jellyfin", "os": "Linux", "arch": "X64", "cachePath": "/var/lib/jellyfin/cache", "transcodePath": "/mnt/jellyfincache/transcodes", "id": "5dc36f32399742309bc253761900d6f6"}',
        "read_only": True,
    },
    {
        "name": "libraries",
        "description": "List media libraries (Name, CollectionType, Locations, ItemId). Note /Library/VirtualFolders returns a plain array. Use search (name substring) and limit; the itemId feeds jellyfin_scan_library for a per-library scan.",
        "method": "GET",
        "path_template": "/Library/VirtualFolders",
        "params": [],
        "fields": ["name", "type", "locations", "itemId"],
        "search_field": "Name",
        "transform": "jellyfin_libraries",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"name": "Movies", "type": "movies", "locations": ["/mnt/nasdownloads/movies"], "itemId": "e3d0a"}]',
        "read_only": True,
    },
    {
        "name": "item_counts",
        "description": "Item counts (movies, series, episodes, albums, etc.) — small passthrough of /Items/Counts. Keys stay PascalCase (MovieCount, SeriesCount, EpisodeCount).",
        "method": "GET",
        "path_template": "/Items/Counts",
        "params": [],
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{"MovieCount": 36, "SeriesCount": 51, "EpisodeCount": 816}',
        "read_only": True,
    },
    {
        "name": "sessions",
        "description": "List active sessions (device, client, user, isActive, lastActivity) with compact now-playing, play state and transcode info. activeOnly=true filters to active sessions; search matches device or user name.",
        "method": "GET",
        "path_template": "/Sessions",
        "params": [
            {"name": "activeOnly", "type": "boolean", "description": "Only return active sessions.", "required": False, "local": True},
        ],
        "search_field": "DeviceName",
        "transform": "jellyfin_sessions",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"deviceName": "Firefox", "client": "Jellyfin Web", "userName": "jellyfin", "isActive": true, "nowPlaying": {"name": "Movie.mkv", "type": "Movie"}}]',
        "read_only": True,
    },
    {
        "name": "scheduled_tasks",
        "description": "List scheduled tasks with id, name, state, category and last run result (status/progress/end). The id feeds jellyfin_start_task / jellyfin_stop_task. Use search (name) and optional category filter.",
        "method": "GET",
        "path_template": "/ScheduledTasks",
        "params": [
            {"name": "category", "type": "string", "description": "Filter by task category (exact).", "required": False, "local": True},
        ],
        "search_field": "Name",
        "transform": "jellyfin_scheduled_tasks",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"id": "62f4a", "name": "Clean Transcode Directory", "state": "Idle", "category": "Maintenance", "lastStatus": "Completed"}]',
        "read_only": True,
    },
    {
        "name": "plugins",
        "description": "List plugins (name, version, status). Status flags matter for upgrades: Restart = needs a restart, Superseded = old version present, NotSupported = incompatible.",
        "method": "GET",
        "path_template": "/Plugins",
        "params": [],
        "search_field": "Name",
        "transform": "jellyfin_plugins",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"name": "Subtitle Extract", "version": "7.0.0.0", "status": "Active"}]',
        "read_only": True,
    },
    {
        "name": "activity_log",
        "description": "Activity log entries (name, type, date, severity). startIndex/limit page through the API; search filters client-side on entry name. Returns {total, entries}.",
        "method": "GET",
        "path_template": "/System/ActivityLog/Entries",
        "params": [
            {"name": "startIndex", "type": "integer", "description": "Pagination start index.", "required": False, "default": 0},
            {"name": "limit", "type": "integer", "description": "Page size (default 20).", "required": False, "default": 20},
            {"name": "search", "type": "string", "description": "Substring filter on entry name.", "required": False, "local": True},
        ],
        "transform": "jellyfin_activity_log",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{"total": 9419, "entries": [{"name": "Login", "type": "UserAuthenticated", "date": "2026-08-20T20:00:00Z", "severity": "Info"}]}',
        "read_only": True,
    },
    {
        "name": "logs",
        "description": "List available log files (name, size). Use jellyfin_get_log to read one. Search by name substring.",
        "method": "GET",
        "path_template": "/System/Logs",
        "params": [],
        "search_field": "Name",
        "transform": "jellyfin_logs",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"name": "FFmpeg.Transcode", "size": 482355}]',
        "read_only": True,
    },
    {
        "name": "get_log",
        "description": "Read the tail of a log file. name comes from jellyfin_logs (e.g. jellyfin20260820.log); the working route is GET /System/Logs/Log?name=<name> (the /System/Logs/{name} path form 404s). Returns {name, lines, content}; tailLines (default 200) and a ~100 KB cap keep the response bounded. 403 = no permission, 404 = unknown log name.",
        "method": "GET",
        "path_template": "/System/Logs/Log",
        "params": [
            {"name": "name", "type": "string", "description": "Log file name (from jellyfin_logs).", "required": True},
            {"name": "tailLines", "type": "integer", "description": "Last N lines to return (default 200).", "required": False, "default": 200, "local": True},
        ],
        "transform": "jellyfin_get_log",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{"name": "FFmpeg.Transcode", "lines": 200, "content": "..."}',
        "read_only": True,
    },
    {
        "name": "users",
        "description": "List users (id, name, isAdmin).",
        "method": "GET",
        "path_template": "/Users",
        "params": [],
        "search_field": "Name",
        "transform": "jellyfin_users",
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '[{"id": "07978d5c70ef493cbd24d62aafb4848f", "name": "jellyfin", "isAdmin": true}]',
        "read_only": True,
    },

    # ── Write tools (always approval-gated) ─────────────────────────────
    {
        "name": "scan_library",
        "description": "Trigger a library scan. Defaults to a plain full refresh (replaceAllMetadata=false, replaceAllImages=false). Pass itemId to scan one library (from jellyfin_libraries). REQUIRES OPERATOR APPROVAL. Warning: replaceAllMetadata=true re-extracts metadata AND subtitles over NFS (the homelab's I/O storm) — only opt in deliberately.",
        "method": "POST",
        "path_template": "/Library/Refresh",
        "path_variants": {"itemId": "/Items/{itemId}/Refresh"},
        "response_hint": "Scan triggered. An itemId-scoped refresh is SILENT — it may not appear in scheduled tasks or the activity log; verify via jellyfin_scheduled_tasks, jellyfin_activity_log, or item counts.",
        "params": [
            {"name": "itemId", "type": "string", "description": "Optional library ItemId to scan (from jellyfin_libraries); omitted = full library scan.", "required": False},
            {"name": "replaceAllMetadata", "type": "boolean", "description": "Re-extract all metadata (I/O storm — default false).", "required": False, "default": False, "in_query": True},
            {"name": "replaceAllImages", "type": "boolean", "description": "Re-extract all images (default false).", "required": False, "default": False, "in_query": True},
        ],
        "always_gate": True,
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "restart",
        "description": "Restart the Jellyfin server. KILLS ACTIVE SESSIONS. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/System/Restart",
        "params": [],
        "always_gate": True,
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "start_task",
        "description": "Start a scheduled task by id (from jellyfin_scheduled_tasks). REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/ScheduledTasks/Running/{taskId}",
        "params": [
            {"name": "taskId", "type": "string", "description": "Task id (from jellyfin_scheduled_tasks).", "required": True},
        ],
        "always_gate": True,
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "stop_task",
        "description": "Stop a running scheduled task by id (from jellyfin_scheduled_tasks). REQUIRES OPERATOR APPROVAL.",
        "method": "DELETE",
        "path_template": "/ScheduledTasks/Running/{taskId}",
        "params": [
            {"name": "taskId", "type": "string", "description": "Task id (from jellyfin_scheduled_tasks).", "required": True},
        ],
        "always_gate": True,
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
    {
        "name": "stop_transcodes",
        "description": "Kill ALL active transcode streams (DELETE /Videos/ActiveEncodings). DESTRUCTIVE — interrupts every active stream. REQUIRES OPERATOR APPROVAL.",
        "method": "DELETE",
        "path_template": "/Videos/ActiveEncodings",
        "params": [],
        "always_gate": True,
        "error_codes": JELLYFIN_ERROR_CODES,
        "example": '{}',
        "read_only": False,
    },
]


def seed_jellyfin_tools(integration_id: int):
    """Idempotently insert/refresh the curated Jellyfin seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in JELLYFIN_SEED_TOOLS:
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
                path_variants=tool.get('path_variants'),
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
                path_variants=tool.get('path_variants') or None,
                response_hint=tool.get('response_hint') or '',
            )
            created += 1
    return created, updated
