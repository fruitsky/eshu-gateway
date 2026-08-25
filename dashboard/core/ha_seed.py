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
    {
        "name": "list_services",
        "description": "List every Home Assistant service with its field schema (names, types, required/optional) — learn the exact fields a service accepts before calling it. Returns the full schema map.",
        "method": "GET",
        "path_template": "/services",
        "params": [],
        "fields": [],
        "example": '{"light": {"turn_on": {"name": "Turn On", "fields": {"entity_id": {"selector": {"entity": {}}}}}}}',
        "read_only": True,
    },
    {
        "name": "get_config",
        "description": "Get the Home Assistant core configuration (location, units, timezone, version).",
        "method": "GET",
        "path_template": "/config",
        "params": [],
        "fields": ["location_name", "latitude", "longitude", "elevation", "unit_system", "time_zone", "version"],
        "example": '{"location_name": "Home", "latitude": 51.5, "longitude": -0.12, "elevation": 20, "unit_system": {"length": "km"}, "time_zone": "Europe/London", "version": "2026.7.3"}',
        "read_only": True,
    },
    {
        "name": "get_history",
        "description": "Get the recorded state history for an entity (or all) from a start time (ISO8601). filter_entity_id narrows to one entity; end_time bounds the window. Returns the raw history (list of per-entity state lists).",
        "method": "GET",
        "path_template": "/history/period/{start}",
        "params": [
            {"name": "start", "type": "string", "description": "Start time, ISO8601 (e.g. 2026-08-18T00:00:00).", "required": True},
            {"name": "filter_entity_id", "type": "string", "description": "Restrict to one entity (e.g. sensor.outside_temp).", "required": False},
            {"name": "end_time", "type": "string", "description": "End time, ISO8601 (optional).", "required": False},
        ],
        "fields": [],
        "example": '[[{"entity_id": "sensor.x", "state": "20", "last_changed": "2026-08-18T00:00:00"}]]',
        "read_only": True,
    },
    {
        "name": "list_entity_registry",
        "description": "List the Home Assistant entity registry — ALL registered entities, including disabled/hidden ones /api/states never shows. Use device_id to get every entity of one device, search to filter by entity_id substring, limit to bound. disabled_by (null/'integration'/'user') distinguishes a disabled entity from one never created.",
        "method": "GET",
        "path_template": "config/entity_registry/list",
        "params": [],
        "fields": ["entity_id", "name", "platform", "disabled_by", "device_id", "config_entry_id", "area_id"],
        "search_field": "entity_id",
        "filter_fields": ["device_id"],
        "transport": "ws",
        "example": '[{"entity_id": "sensor.smoke_rssi", "name": "Smoke RSSI", "platform": "mqtt", "disabled_by": "integration", "device_id": "dev123", "config_entry_id": "ce1", "area_id": "a1"}]',
        "read_only": True,
    },
    {
        "name": "list_device_registry",
        "description": "List the Home Assistant device registry — every device with manufacturer, model, identifiers, connections, and via_device_id (what it joins through). Use search to filter by device name substring, limit to bound.",
        "method": "GET",
        "path_template": "config/device_registry/list",
        "params": [],
        "fields": ["id", "name", "name_by_user", "manufacturer", "model", "identifiers", "connections", "via_device_id", "entry_type", "area_id"],
        "search_field": "name",
        "transport": "ws",
        "example": '[{"id": "dev123", "name": "Smoke Detector", "manufacturer": "Tuya", "model": "_TZE284_gyzlwu5q TS0601", "identifiers": [["zigbee", "a4:c1:38:53:2b:6a:d6:5f"]], "via_device_id": "dev-coord"}]',
        "read_only": True,
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
                filter_fields=tool.get('filter_fields'),
                transport=tool.get('transport', 'http'),
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
                seeded=True,
            )
            created += 1
    return created, updated
