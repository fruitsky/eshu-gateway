"""Curated seed catalog for TP-Link Omada SDN Controller.

Auth is OAuth2 client_credentials (see core/integration_proxy._fetch_oauth2_token):
POST <token_url>?grant_type=client_credentials with {omadacId, client_id,
client_secret}, then `Authorization: AccessToken=<token>` on API calls.

The integration's base_url must end in `/openapi/v1/<omadacId>` (the account id
is also required by the token exchange). Read tools auto-run (with response
projection + search/limit shaping); the client tools are mutating and route
through the operator approval queue. Seeded idempotently via the Integrations
UI's "Seed" action.
"""

OMADA_SEED_TOOLS = [
    {
        "name": "list_sites",
        "description": "List all Omada sites and their summary (site id, name, region, timezone, scenario, type). Use search to filter by site name substring and limit to bound the result. Use this to discover siteId values.",
        "method": "GET",
        "path_template": "/sites",
        "params": [
            {"name": "page", "type": "integer", "description": "Page number (1-based).", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Results per page (max 100).", "required": False, "default": 50},
        ],
        "fields": ["siteId", "name", "region", "timeZone", "scenario", "type"],
        "search_field": "name",
        "example": '[{"siteId": "640effd1b3f2ae5b912275ec", "name": "Home", "region": "Europe", "timeZone": "UTC", "scenario": "Home", "type": 0}]',
        "read_only": True,
    },
    {
        "name": "get_site",
        "description": "Get a single Omada site's details (including region, address and timezone).",
        "method": "GET",
        "path_template": "/sites/{siteId}",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
        ],
        "fields": ["siteId", "name", "region", "timeZone", "scenario", "address", "type"],
        "example": '{"siteId": "640effd1b3f2ae5b912275ec", "name": "Home", "region": "Europe", "timeZone": "UTC", "scenario": "Home", "address": "1 Main St"}',
        "read_only": True,
    },
    {
        "name": "list_site_devices",
        "description": "List the managed devices (APs, switches, gateways) on a site with status, model, IP, CPU/mem and uptime. Use search to filter by device name substring and limit to bound the result.",
        "method": "GET",
        "path_template": "/sites/{siteId}/devices",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based).", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Results per page (max 100).", "required": False, "default": 50},
        ],
        "fields": ["mac", "name", "type", "modelName", "ip", "status", "lastSeen", "cpuUtil", "memUtil", "sn", "uplinkDeviceName", "firmwareVersion", "uptime"],
        "search_field": "name",
        "example": '[{"mac": "70:B3:D5:AA:BB:CC", "name": "AP-Living", "type": "ap", "modelName": "EAP670", "ip": "192.168.1.50", "status": 1, "lastSeen": 1700000000000}]',
        "read_only": True,
    },
    {
        "name": "search_devices",
        "description": "Globally search devices by keyword across all sites you have access to (returns matches with their site, model, status and MAC). searchKey is required — provide a device name, model or MAC fragment.",
        "method": "GET",
        "path_template": "/devices",
        "params": [
            {"name": "searchKey", "type": "string", "description": "Search keyword (device name, model or MAC fragment).", "required": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based).", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Results per page (max 100).", "required": False, "default": 50},
        ],
        "fields": [],
        "example": '{"siteNames": {"640effd1b3f2ae5b912275ec": "Home"}, "devices": [{"mac": "70:B3:D5:AA:BB:CC", "name": "AP-Living", "site": "Home", "model": "EAP670", "type": "ap", "status": 1}]}',
        "read_only": True,
    },
    {
        "name": "list_site_clients",
        "description": "List the connected clients on a site (MAC, name, vendor, IP, signal, WiFi SSID/AP). Use searchKey for a server-side keyword match (MAC/name/vendor), search to filter by client name substring, and limit to bound the result. Use this to discover clientMac values. (v1 clients list is broken on v6.2 — this uses the v2 POST endpoint.)",
        "method": "POST",
        "version": "v2",
        "path_template": "/sites/{siteId}/clients",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "searchKey", "type": "string", "description": "Server-side keyword match (MAC, name, vendor…).", "required": False},
            {"name": "page", "type": "integer", "description": "Page number (1-based).", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Results per page (max 100).", "required": False, "default": 50},
        ],
        "fields": ["id", "mac", "name", "hostName", "vendor", "deviceType", "ip", "ssid", "signalLevel", "wifiMode", "apName", "healthScore"],
        "search_field": "name",
        "example": '[{"id": "abc123", "mac": "AA:BB:CC:DD:EE:FF", "name": "Phone", "vendor": "Apple", "deviceType": 1, "ip": "192.168.1.100", "ssid": "Home-5G", "signalLevel": -55}]',
        "read_only": True,
    },
    {
        "name": "get_client",
        "description": "Get a single connected client's full detail by MAC (vendor, OS, IP, wireless link, AP and channel).",
        "method": "GET",
        "path_template": "/sites/{siteId}/clients/{clientMac}",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "clientMac", "type": "string", "description": "Client MAC address (from list_site_clients).", "required": True},
        ],
        "fields": ["id", "mac", "name", "hostName", "vendor", "deviceCategory", "osName", "ip", "ssid", "signalLevel", "wifiMode", "apName", "apMac", "channel", "connectType", "vid", "networkName", "port", "switchName", "gatewayName", "uptime", "rxRate", "txRate"],
        "example": '{"id": "abc123", "mac": "AA:BB:CC:DD:EE:FF", "name": "Phone", "vendor": "Apple", "ip": "192.168.1.100", "ssid": "Home-5G", "signalLevel": -55, "apName": "AP-Living", "connectType": "wireless", "vid": 1, "networkName": "LAN"}',
        "read_only": True,
    },
    {
        "name": "list_site_alerts",
        "description": "List the alert logs for a site in a time window (module, content, time, severity). Provide timeStart and timeEnd as epoch milliseconds; use search to filter by alert content and limit to bound the result.",
        "method": "GET",
        "path_template": "/sites/{siteId}/logs/alerts",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "filters.timeStart", "type": "integer", "description": "Start of the window, epoch milliseconds.", "required": True},
            {"name": "filters.timeEnd", "type": "integer", "description": "End of the window, epoch milliseconds.", "required": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based).", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Results per page (max 100).", "required": False, "default": 50},
        ],
        "fields": ["id", "module", "content", "time", "level"],
        "search_field": "content",
        "example": '[{"id": "alert1", "module": "device", "content": "AP-Living went offline", "time": 1700000000000, "level": "error"}]',
        "read_only": True,
    },
    {
        "name": "list_site_ssids",
        "description": "List the WiFi SSIDs configured on a site (grouped by WLAN group). Use search to filter by WLAN name.",
        "method": "GET",
        "path_template": "/sites/{siteId}/wireless-network/ssids",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "type", "type": "integer", "description": "Device type: 1=AP, 2=wireless router, 3=both.", "required": False, "default": 3},
        ],
        "fields": ["wlanId", "wlanName", "ssidList"],
        "search_field": "wlanName",
        "example": '[{"wlanId": "wlan1", "wlanName": "Main", "ssidList": [{"ssid": "Home-2.4G"}, {"ssid": "Home-5G"}]}]',
        "read_only": True,
    },
    {
        "name": "block_client",
        "description": "Block a connected client by MAC so it can no longer access the network. REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/sites/{siteId}/clients/{clientMac}/block",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "clientMac", "type": "string", "description": "Client MAC address (from list_site_clients).", "required": True},
        ],
        "example": '[]',
        "read_only": False,
    },
    {
        "name": "reconnect_client",
        "description": "Force a connected client to reconnect (it will drop off and re-associate). REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/sites/{siteId}/clients/{clientMac}/reconnect",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "clientMac", "type": "string", "description": "Client MAC address (from list_site_clients).", "required": True},
        ],
        "example": '[]',
        "read_only": False,
    },
]


def seed_omada_tools(integration_id: int):
    """Idempotently insert/refresh the curated Omada seed tools for an
    integration. Existing tools with the same name are updated in place; new
    ones are created. Returns (created, updated) counts."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in OMADA_SEED_TOOLS:
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
                version=tool.get('version', 'v1'),
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
                version=tool.get('version', 'v1'),
            )
            created += 1
    return created, updated
