"""Seed catalog for the Eshu-owned commute capability.

Profiles live in Eshu's SQLite DB. Writes are validated, audited, and executed
immediately; they are scoped to the household agent at the tool level, not
gated through the approval queue. The scheduler path (`/api/commute/due`) is a
REST endpoint, not an MCP tool, so the agent cannot mark a slot sent.
"""
from __future__ import annotations

COMMUTE_SEED_TOOLS = [
    {
        "name": "check",
        "description": (
            "Live commute check for one person. Read-only. Use for 'how are my "
            "trains?', 'will I make it?', or a check against their usual train "
            "and arrive-by time. Ask which person (Gui or Megan) if it is not "
            "clear. Returns a short `text` field; relay it. This is the profile "
            "check, not an ad-hoc journey query — use train_plan for a "
            "different origin/destination/time."
        ),
        "method": "GET",
        "path_template": "",
        "params": [
            {"name": "person", "type": "string",
             "description": "Profile slug: gui or megan.", "required": True},
            {"name": "at", "type": "string",
             "description": "Optional HH:MM override for testing/backfill.",
             "required": False},
        ],
        "fields": [],
        "read_only": True,
        "transport": "http",
        "handler": "commute_check",
        "seeded": True,
        "principal_allow": [],
        "example": (
            '{"ok": true, "person": "gui", "date": "2026-11-03", "level": "normal", '
            '"send": true, "text": "🚆 Morning Gui — all good.\\n06:16 Chelsfield Rail '
            'Station → 06:42 Cannon Street (26 min, direct).\\n8 min spare against '
            'your 06:50."}'
        ),
    },
    {
        "name": "profile_get",
        "description": (
            "Read one person's commute profile: stations, usual train, "
            "arrive-by, alert times, days, overrides and pause. Read-only. Use "
            "before changing anything so the user can see what is changing; "
            "relay the `text` field."
        ),
        "method": "GET",
        "path_template": "",
        "params": [
            {"name": "person", "type": "string",
             "description": "Profile slug: gui or megan.", "required": True},
        ],
        "fields": [],
        "read_only": True,
        "transport": "http",
        "handler": "commute_profile_get",
        "seeded": True,
        "principal_allow": [],
        "example": (
            '{"ok": true, "person": "gui", "text": "Gui (gui)\\n  Chelsfield Rail '
            'Station → Cannon Street\\n  usual train 06:16 ...", "profile": {}}'
        ),
    },
    {
        "name": "profile_set",
        "description": (
            "Change a person's permanent commute settings. Applies immediately "
            "— no approval step — then returns the stored profile; repeat that "
            "read-back. Use only after commute_profile_get and after resolving "
            "which person (Gui or Megan) if ambiguous. Patch fields: days, "
            "arrive_by, usual_train, never_before, notify_times (list of HH:MM), "
            "warn_minutes, lines, alerts_only, display_name, from, to. For a "
            "single date use commute_override, not this. Never change days for a "
            "one-off absence."
        ),
        "method": "POST",
        "path_template": "/profile",
        "params": [
            {"name": "person", "type": "string",
             "description": "Profile slug: gui or megan.", "required": True},
            {"name": "patch", "type": "json",
             "description": "Object of fields to change.", "required": True},
        ],
        "fields": [],
        "read_only": False,
        "always_gate": False,
        "transport": "http",
        "handler": "commute_profile_set",
        "seeded": True,
        "principal_allow": ["rowana-household"],
        "example": (
            '{"ok": true, "person": "megan", "text": "Megan (megan)\\n  ...", '
            '"profile": {"notify_times": ["05:30", "06:00"]}}'
        ),
    },
    {
        "name": "override",
        "description": (
            "Set a one-off change for one date: action='skip' (no alert that "
            "day) or 'office' (alert even if it is not a normal commute day). "
            "Optionally set arrive_by for that date. Never edits the weekly "
            "days list. Applies immediately and returns the stored read-back."
        ),
        "method": "POST",
        "path_template": "/override",
        "params": [
            {"name": "person", "type": "string",
             "description": "Profile slug: gui or megan.", "required": True},
            {"name": "date", "type": "string",
             "description": "YYYY-MM-DD.", "required": True},
            {"name": "action", "type": "string",
             "description": "'skip' or 'office'.", "required": True},
            {"name": "arrive_by", "type": "string",
             "description": "Optional HH:MM deadline for an office day.",
             "required": False},
        ],
        "fields": [],
        "read_only": False,
        "always_gate": False,
        "transport": "http",
        "handler": "commute_override",
        "seeded": True,
        "principal_allow": ["rowana-household"],
        "example": (
            '{"ok": true, "person": "gui", "text": "Gui (gui)\\n  ...", '
            '"profile": {"overrides": {"2026-11-04": "skip"}}}'
        ),
    },
    {
        "name": "pause",
        "description": (
            "Pause commute alerts until a date (inclusive), or clear a pause. "
            "Applies immediately and returns the stored read-back. Confirm with "
            "the user before pausing all alerts; an accidental pause means "
            "silent mornings."
        ),
        "method": "POST",
        "path_template": "/pause",
        "params": [
            {"name": "person", "type": "string",
             "description": "Profile slug: gui or megan.", "required": True},
            {"name": "until", "type": "string",
             "description": "YYYY-MM-DD, or 'clear'.", "required": True},
        ],
        "fields": [],
        "read_only": False,
        "always_gate": False,
        "transport": "http",
        "handler": "commute_pause",
        "seeded": True,
        "principal_allow": ["rowana-household"],
        "example": (
            '{"ok": true, "person": "megan", "text": "Megan (megan)\\n  ...", '
            '"profile": {"paused_until": "2026-11-07"}}'
        ),
    },
]


def ensure_commute_integration():
    """Create the built-in ``commute`` integration if it is missing."""
    from db.integrations import create_integration, get_integration

    existing = get_integration("commute")
    if existing:
        if (existing.get("kind") or "custom") != "commute":
            print(
                "[commute] integration 'commute' exists with kind "
                f"'{existing.get('kind') or 'custom'}'; not auto-seeding the "
                "commute tools. Rename the integration or set kind='commute'.",
                flush=True,
            )
        return existing
    create_integration(
        "commute",
        "https://api.tfl.gov.uk",
        "none",
        "",
        kind="commute",
        enabled=True,
        mcp_mode="both",
    )
    return get_integration("commute")


def seed_commute_tools(integration_id: int):
    from db.integrations import create_tool, get_tools, update_tool

    existing = {tool["name"]: tool for tool in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in COMMUTE_SEED_TOOLS:
        if tool["name"] in existing:
            update_tool(
                existing[tool["name"]]["id"],
                name=tool["name"],
                description=tool["description"],
                method=tool["method"],
                path_template=tool["path_template"],
                params=tool["params"],
                fields=tool["fields"],
                search_field="",
                filter_fields=[],
                transport=tool["transport"],
                generic=False,
                always_gate=tool.get("always_gate", False),
                read_only=tool["read_only"],
                seeded=True,
                handler=tool["handler"],
                example=tool["example"],
                principal_allow=tool.get("principal_allow") or [],
            )
            updated += 1
        else:
            create_tool(
                integration_id,
                tool["name"],
                tool["description"],
                tool["method"],
                tool["path_template"],
                tool["params"],
                tool["example"],
                read_only=tool["read_only"],
                fields=tool["fields"],
                transport=tool["transport"],
                generic=False,
                always_gate=tool.get("always_gate", False),
                handler=tool["handler"],
                seeded=True,
                principal_allow=tool.get("principal_allow") or [],
            )
            created += 1
    return created, updated
