"""Curated seed catalog for Home Assistant.

Read tools auto-run (with response projection); `call_service` is mutating and
routes through the operator approval queue. Seeded idempotently via the
Integrations UI's "Seed" action.
"""

HA_SEED_TOOLS = [
    {
        "name": "list_entities",
        "description": "List all Home Assistant entities and their current state (entity_id, state, friendly name). Use search to filter by entity_id substring, and limit to bound the result. Use this to discover entity ids.",
        "method": "GET",
        "path_template": "/states",
        "params": [],
        "fields": ["entity_id", "state", "attributes.friendly_name"],
        "search_field": "entity_id",
        "example": '[{"entity_id": "light.living_room", "state": "on", "friendly_name": "Living Room"}]',
        "read_only": True,
    },
    {
        "name": "get_entity",
        "description": "Get a single entity's full state and attributes (e.g. light.living_room).",
        "method": "GET",
        "path_template": "/states/{entity_id}",
        "params": [
            {"name": "entity_id", "type": "string", "description": "Entity id (e.g. light.living_room).", "required": True},
        ],
        "fields": ["entity_id", "state", "attributes"],
        "example": '{"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}}',
        "read_only": True,
    },
    {
        "name": "call_service",
        "description": "Call a Home Assistant service (e.g. light.turn_on, light.turn_off, climate.set_temperature, switch.toggle). Pass service data as a JSON object. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/services/{domain}/{service}",
        "params": [
            {"name": "domain", "type": "string", "description": "Service domain (e.g. light, climate, switch).", "required": True},
            {"name": "service", "type": "string", "description": "Service name (e.g. turn_on, set_temperature).", "required": True},
            {"name": "data", "type": "json", "description": "Service data as a JSON object, e.g. {\"entity_id\": \"light.living_room\", \"brightness\": 128}.", "required": False},
        ],
        "example": '[]',
        "read_only": False,
    },
]


def seed_ha_tools(integration_id: int):
    """Idempotently insert/refresh the curated Home Assistant seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in HA_SEED_TOOLS:
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
            )
            created += 1
    return created, updated
