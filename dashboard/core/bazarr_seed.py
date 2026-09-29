"""Curated seed catalog for Bazarr (subtitle management, /api at the origin).

Auth is an `X-API-KEY` header (Bazarr's `auth_method: apikey`, 32-hex key from
/config/config/config.yaml) — same `header` auth_type as Sonarr/Radarr, only the
header name differs. One integration record per Bazarr instance (kind `bazarr`,
name-based MCP namespace gives `bazarr_*`).

ROUTES ARE VERIFIED AGAINST THE UPSTREAM SOURCE (v1.5.5), not guessed — the
probe spec's shapes were wrong:
  - `GET /api/series`         → envelope {data:[...], total:N}
  - `GET /api/episodes`       → requires `seriesid[]` and/or `episodeid[]`
                                (arrays; a bare call 404s)
  - `GET /api/episodes/history`
  - `GET /api/system/status`
  - `PATCH /api/series` body  {action, seriesid} where action="search-missing"
                                runs `series_download_subtitles(seriesid)` — the
                                subtitle-download trigger (there is NO
                                `POST /api/subtitles`; that route is PATCH-only
                                and syncs an existing subtitle file).

The generic floor is enabled for Bazarr (read + write); the generic write is
`always_gate` (see core.generic_tools.ALWAYS_GATE_GENERIC_WRITE_KINDS) because
any write can trigger downloads/searches. Reads are un-gated; writes are
approval-gated.
"""

BAZARR_ERROR_CODES = {
    '400': 'invalid_request',
    '401': 'invalid_key',
    '403': 'forbidden',
    '404': 'not_found',
    '500': 'bazarr_unavailable',
    '502': 'bazarr_unavailable',
    '503': 'bazarr_unavailable',
    '504': 'bazarr_unavailable',
}

BAZARR_SEED_TOOLS = [
    # ── Read tools ──────────────────────────────────────────────────────
    {
        "name": "system_status",
        "description": "Bazarr version/status. Use this to confirm the Bazarr build.",
        "method": "GET",
        "path_template": "/api/system/status",
        "params": [],
        "transform": "bazarr_status",
        "error_codes": BAZARR_ERROR_CODES,
        "example": '{"data": {"version": "1.5.5", "branch": "master"}}',
        "read_only": True,
    },
    {
        "name": "series",
        "description": "List series Bazarr tracks (sonarrSeriesId, title, profileId, path, episodeFileCount, episodeMissingCount). API returns an envelope {data, total} — unwrapped here. `search` filters on title, `limit` caps the list.",
        "method": "GET",
        "path_template": "/api/series",
        "params": [],
        "search_field": "title",
        "transform": "bazarr_series",
        "error_codes": BAZARR_ERROR_CODES,
        "example": '[{"sonarrSeriesId": 88, "title": "Silo", "profileId": 1, "episodeMissingCount": 1}]',
        "read_only": True,
    },
    {
        "name": "episodes",
        "description": "Episodes metadata for a series (sonarrSeriesId, sonarrEpisodeId, season, episode, title, monitored, path, missing_subtitles, subtitles, audio_language). Bazarr requires seriesid[] and/or episodeid[] — seriesId is required here, episodeId narrows to one episode.",
        "method": "GET",
        "path_template": "/api/episodes",
        "params": [
            {"name": "seriesId", "type": "integer", "query_key": "seriesid[]",
             "description": "Sonarr series id (Bazarr keys on sonarrSeriesId).", "required": True},
            {"name": "episodeId", "type": "integer", "query_key": "episodeid[]",
             "description": "Optional Sonarr episode id to narrow to one episode.", "required": False},
        ],
        "transform": "bazarr_episodes",
        "error_codes": BAZARR_ERROR_CODES,
        "example": '[{"sonarrSeriesId": 88, "sonarrEpisodeId": 3746, "season": 1, "episode": 10, "monitored": true, "missing_subtitles": []}]',
        "read_only": True,
    },
    {
        "name": "history",
        "description": "Recent episode subtitle history {total, records} (which language/provider was downloaded for which episode). Optionally narrow to one episodeId; `limit` caps the list.",
        "method": "GET",
        "path_template": "/api/episodes/history",
        "params": [
            {"name": "episodeId", "type": "integer", "query_key": "episodeid",
             "description": "Optional Sonarr episode id filter.", "required": False},
            {"name": "limit", "type": "integer", "description": "Max records (default 50).", "required": False, "default": 50, "local": True},
        ],
        "transform": "bazarr_history",
        "error_codes": BAZARR_ERROR_CODES,
        "example": '{"total": 1, "records": [{"sonarrEpisodeId": 3746, "language": "en"}]}',
        "read_only": True,
    },

    # ── Write tools (always approval-gated) ─────────────────────────────
    {
        "name": "search_subtitles",
        "description": "Trigger Bazarr to search for and download missing subtitles for a series (PATCH /api/series with action=search-missing, which calls series_download_subtitles on the server). Same approval weight as a torrent grab. REQUIRES OPERATOR APPROVAL.",
        "method": "PATCH",
        "path_template": "/api/series",
        "params": [
            {"name": "seriesId", "type": "integer", "query_key": "seriesid",
             "description": "Sonarr series id to search subtitles for.", "required": True},
            {"name": "action", "type": "string",
             "description": "Bazarr series action (default search-missing; other options: scan-disk, search-wanted, sync).", "required": False, "default": "search-missing"},
        ],
        "always_gate": True,
        "error_codes": BAZARR_ERROR_CODES,
        "response_hint": "Subtitle search queued for the series. Verify downloads via the history tool (records) or the episode's missing_subtitles/subtitles.",
        "example": '{"seriesId": 88}',
        "read_only": False,
    },
]


def seed_bazarr_tools(integration_id: int):
    """Idempotently insert/refresh the curated Bazarr seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in BAZARR_SEED_TOOLS:
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
                response_hint=tool.get('response_hint') or '',
                timeout=tool.get('timeout') or 0,
                seeded=True,
            )
            created += 1
    return created, updated
