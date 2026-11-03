"""Seed catalog for the Eshu-owned TfL train capability.

This is a native integration: the handlers in ``core.train`` do the work and
the integration's ``base_url`` is only the upstream TfL origin. There is no key,
no generic passthrough, and no write surface in this phase.
"""
from __future__ import annotations

TRAIN_SEED_TOOLS = [
    {
        "name": "train_plan",
        "description": (
            "Plan a train journey between two UK stations. Use this for any "
            "'how do I get to X by Y' or 'what trains are there' question. Ask "
            "the user for any missing origin/destination/time before calling. "
            "Set arrive_by=true when they say 'be there by' or 'I need to be in X "
            "at T'; set arrive_by=false when they say 'leaving at' or 'after'. "
            "Returns up to 3 options with departure, arrival, duration, changes "
            "and mode, plus a short `text` field. Relay `text`; on `ok: false` "
            "relay `message` and ask the question it raises. It may return "
            "station candidates when a name is ambiguous — ask which one. Never "
            "invent a departure, arrival or platform; TfL does not provide "
            "platform numbers."
        ),
        "method": "GET",
        "path_template": "",
        "params": [
            {"name": "origin", "type": "string",
             "description": "Starting station name or NaPTAN id.", "required": True},
            {"name": "destination", "type": "string",
             "description": "Destination station name or NaPTAN id.", "required": True},
            {"name": "time", "type": "string",
             "description": "Local time as HH:MM (Europe/London).", "required": True},
            {"name": "arrive_by", "type": "boolean",
             "description": "True to arrive by `time`; false to leave after `time`.",
             "required": False, "default": True},
            {"name": "date", "type": "string",
             "description": "YYYY-MM-DD, 'today' or 'tomorrow'. Defaults to today.",
             "required": False},
            {"name": "limit", "type": "integer",
             "description": "Maximum options to return (1-5, default 3).",
             "required": False, "default": 3},
        ],
        "fields": [],
        "read_only": True,
        "transport": "http",
        "handler": "train_plan",
        "seeded": True,
        "example": (
            '{"ok": true, "origin": {"name": "Chelsfield Rail Station", '
            '"naptan": "910GCHLSFLD"}, "destination": {"name": "Petts Wood Rail Station", '
            '"naptan": "910GPETSWD"}, "date": "2026-11-03", "time": "13:30", '
            '"mode": "arrive_by", "meets_deadline": true, "options": [{"depart": '
            '"13:01", "arrive": "13:11", "duration": 10, "changes": 0, '
            '"mode": "rail", "summary": "Southeastern to Petts Wood", '
            '"disrupted": false}], "text": "Chelsfield Rail Station → Petts Wood '
            'Rail Station, arriving by 13:30:\\n1. 13:01 → 13:11 (10 min, rail, direct)"}'
        ),
    },
    {
        "name": "train_status",
        "description": (
            "Current TfL line status and disruption text for one or more lines "
            "(comma-separated; default southeastern). Use this for 'are there "
            "delays?' or before recommending a route. Relay the `text` field. "
            "Read-only."
        ),
        "method": "GET",
        "path_template": "",
        "params": [
            {"name": "lines", "type": "string",
             "description": "Comma-separated TfL line ids (default southeastern).",
             "required": False, "default": "southeastern"},
        ],
        "fields": [],
        "read_only": True,
        "transport": "http",
        "handler": "train_status",
        "seeded": True,
        "example": (
            '{"ok": true, "lines": [{"id": "southeastern", "name": '
            '"Southeastern", "severity": 10, "description": "Good Service", '
            '"reason": ""}], "text": "Southeastern: Good Service"}'
        ),
    },
]


def ensure_train_integration():
    """Create the built-in ``tfl`` integration if it is missing.

    It is a native, keyless capability, so there is nothing for the operator to
    configure. Existing rows are left alone (enable/disable and MCP mode are the
    operator's choices), and the startup reseed refreshes the catalog.
    """
    from db.integrations import create_integration, get_integration

    existing = get_integration("tfl")
    if existing:
        if (existing.get("kind") or "custom") != "trains":
            print(
                "[tfl] integration 'tfl' exists with kind "
                f"'{existing.get('kind') or 'custom'}'; not auto-seeding the "
                "train tools. Rename the integration or set kind='trains'.",
                flush=True,
            )
        return existing
    create_integration(
        "tfl",
        "https://api.tfl.gov.uk",
        "none",
        "",
        kind="trains",
        enabled=True,
        mcp_mode="both",
    )
    return get_integration("tfl")


def seed_train_tools(integration_id: int):
    """Idempotently insert/refresh the train tool catalog."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {tool["name"]: tool for tool in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in TRAIN_SEED_TOOLS:
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
                always_gate=False,
                read_only=tool["read_only"],
                seeded=True,
                handler=tool["handler"],
                example=tool["example"],
                principal_allow=[],
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
                always_gate=False,
                handler=tool["handler"],
                seeded=True,
                principal_allow=[],
            )
            created += 1
    return created, updated
