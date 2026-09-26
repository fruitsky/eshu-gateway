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
        "totals": True,
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
        "strip_envelope": True,
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
        "fields": ["id", "mac", "name", "hostName", "vendor", "deviceType", "ip", "ssid", "signalLevel", "wifiMode", "apName", "healthScore", "trafficDown", "trafficUp", "activity"],
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
        "fields": ["id", "mac", "name", "hostName", "vendor", "deviceType", "deviceCategory", "osName", "model", "systemName", "ip", "wireless", "active", "lastSeen", "blocked", "guest", "connectType", "connectDevType", "ipSetting", "ssid", "signalLevel", "wifiMode", "apName", "apMac", "channel", "vid", "networkName", "port", "switchName", "gatewayName", "uptime", "rxRate", "txRate", "trafficDown", "trafficUp", "activity"],
        "example": '{"id": "abc123", "mac": "AA:BB:CC:DD:EE:FF", "name": "Phone", "vendor": "Apple", "ip": "192.168.1.100", "ssid": "Home-5G", "signalLevel": -55, "apName": "AP-Living", "connectType": "wireless", "vid": 1, "networkName": "LAN"}',
        "read_only": True,
    },
    {
        "name": "list_known_clients",
        "description": "List ALL clients the controller knows — online AND offline/blocked (v2 client query with scope=0), with presence facts (active, lastSeen) and address facts (ip, VLAN/network). Use this instead of list_site_clients when a device may be disconnected: the associated-clients list cannot enumerate offline clients. search matches name, hostName, MAC, vendor and device type (case-insensitive); active/wireless narrow to online/offline and WiFi/wired. Rows are sorted active first, then lastSeen desc, and carry totalRows/returned/truncated so a filtered or paged view is never mistaken for the whole set. Fixed-address flag (useFixedAddr) lives in get_client and list_dhcp_reservations. Read-only (un-gated).",
        "method": "POST",
        "version": "v2",
        "path_template": "/sites/{siteId}/clients",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "search", "type": "string", "description": "Substring match on name, hostName, MAC, vendor or device type.", "required": False, "local": True},
            {"name": "active", "type": "boolean", "description": "true=online only, false=offline only, omit for both.", "required": False, "local": True},
            {"name": "wireless", "type": "boolean", "description": "true=WiFi only, false=wired only, omit for both.", "required": False, "local": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based) over the filtered set.", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Rows per page (default 50, max 500).", "required": False, "default": 50},
            {"name": "scope", "type": "integer", "description": "Client scope passed to the API. Leave at the default 0 (all: online + offline + blocked).", "required": False, "default": 0},
        ],
        "transform": "omada_list_known_clients",
        "example": '{"totalRows": 92, "matched": 92, "returned": 2, "truncated": true, "rows": [{"mac": "6E-39-40-84-35-5F", "name": "Ellen\'s iPad", "active": false, "lastSeen": 1771768986768, "ip": "192.168.20.3", "wireless": true, "ssid": "torquoise", "apName": "House-AP-EAP225 v5"}]}',
        "read_only": True,
    },
    {
        "name": "list_networks",
        "description": "List the site's LAN networks, resolving network id ↔ name ↔ VLAN id ↔ subnet/gateway (plus DHCP enable, gateway and domain). Use this to name the network behind a client's netId or IP — e.g. which network owns 192.168.20.0/24 and its VLAN. Returns totalRows/returned/truncated. Read-only (un-gated).",
        "method": "GET",
        "path_template": "/sites/{siteId}/lan-networks",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
        ],
        "transform": "omada_list_networks",
        "example": '{"totalRows": 7, "returned": 7, "truncated": false, "rows": [{"id": "64285b28c2a55c6ded3026a4", "name": "20-Guest_VLAN", "vid": 20, "subnet": "192.168.20.254/24", "purpose": 1, "dhcpEnabled": true, "gateway": "192.168.20.254"}]}',
        "read_only": True,
    },
    {
        "name": "list_acls",
        "description": "Read the site's gateway and/or switch ACLs as structured rows in evaluation order (first-match-wins), resolving source/destination type 0=network and 1=IP group (2=MAC group, best-effort) to names while keeping the raw ids. Each layer also returns a normalized block (stable per-rule hashes + ordered id list + list hash) so an ACL reorder can be diffed. Use this before/after any acl_reorder. Read-only (un-gated); ACL writes stay in the gated write/acl_reorder tools.",
        "method": "GET",
        "path_template": "/sites/{siteId}/acls/osg-acls",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "layer", "type": "string", "description": "gateway, switch or both (default both).", "required": False, "local": True},
        ],
        "transform": "omada_list_acls",
        "example": '{"totalRows": 45, "returned": 45, "truncated": false, "layers": {"gateway": [{"index": 1, "id": "6a86cc3477bfbd044e5f5db8", "name": "Allow_Kindle2Main", "action": "allow", "srcType": "ip-group", "src": ["Kindle"], "dstType": "network", "dst": ["1-Main_LAN(Default)"], "protocols": [6, 17]}]}, "normalized": {"gateway": {"order": ["6a86cc3477bfbd044e5f5db8"], "hash": "…"}}}',
        "read_only": True,
    },
    {
        "name": "list_dhcp_reservations",
        "description": "List the controller's DHCP user/binding table: MAC ↔ IP ↔ network ↔ name, including offline fixed-address entries (this is where a fixed-address client such as DESKTOP-FOCJDJ4 → 192.168.20.1 lives). search matches MAC, IP, name and network name — so an IP or MAC lookup works, not only hostname. Rows carry type (0=infrastructure AP/switch, 1=client), showingType, server and netName. Returns totalRows/returned/truncated. Read-only (un-gated).",
        "method": "GET",
        "path_template": "/sites/{siteId}/setting/service/dhcp",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "search", "type": "string", "description": "Substring match on MAC, IP, name, network or server.", "required": False, "local": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based) over the matched set.", "required": False, "default": 1, "local": True},
            {"name": "pageSize", "type": "integer", "description": "Rows per page (default 50, max 500).", "required": False, "default": 50, "local": True},
        ],
        "transform": "omada_list_dhcp_reservations",
        "example": '{"totalRows": 116, "matched": 1, "returned": 1, "truncated": false, "rows": [{"mac": "4C-D5-77-7B-13-7D", "ip": "192.168.20.1", "name": "DESKTOP-FOCJDJ4", "netName": "20-Guest_VLAN", "serverName": "Router-ER605 v2.0", "type": 1, "showingType": "Computer"}]}',
        "read_only": True,
    },
    {
        "name": "get_device",
        "description": "Get one managed device's detail by MAC (name, type, model, IP, firmware, status, uptime, CPU/mem, uplink). full=true adds per-type extras: AP radios/channels or gateway WAN status. Read-only (un-gated).",
        "method": "GET",
        "path_template": "/sites/{siteId}/devices/all",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "deviceMac", "type": "string", "description": "Device MAC (from list_site_devices).", "required": True},
            {"name": "full", "type": "boolean", "description": "Include per-type extras (AP radios, gateway WAN status).", "required": False, "local": True},
        ],
        "transform": "omada_get_device",
        "example": '{"mac": "9C-A2-F4-40-14-86", "name": "Router-ER605 v2.0", "type": "gateway", "model": "ER605 v2.0", "ip": "192.168.1.1", "firmwareVersion": "2.3.2 Build 20251029 Rel.12727", "status": 1, "uptime": "16day(s) 17h 46m 16s", "cpuUtil": 3, "memUtil": 41}',
        "read_only": True,
    },
    {
        "name": "list_client_events",
        "description": "List site event-log entries (connect/disconnect and similar) in a time window, optionally narrowed to one client MAC. Omada has no server-side client filter, so clientMac is matched against the event content — and the log is window-limited, so if nothing matches, widen timeStart/timeEnd. timeStart/timeEnd are epoch milliseconds. The scan is capped (~5000 events), so a busy window reports scanned < totalRows and truncated=true; narrow the window to see everything. Returns totalRows/scanned/matched/returned/truncated plus the window and a retention note. Read-only (un-gated).",
        "method": "GET",
        "path_template": "/sites/{siteId}/logs/events",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "timeStart", "type": "integer", "query_key": "filters.timeStart", "description": "Start of the window, epoch milliseconds.", "required": True},
            {"name": "timeEnd", "type": "integer", "query_key": "filters.timeEnd", "description": "End of the window, epoch milliseconds.", "required": True},
            {"name": "clientMac", "type": "string", "description": "Optional client MAC to narrow to (matched in the event content).", "required": False, "local": True},
            {"name": "module", "type": "string", "description": "Log module (default Client).", "required": False, "default": "Client", "local": True},
            {"name": "page", "type": "integer", "description": "Page number (1-based) over the matched set.", "required": False, "default": 1},
            {"name": "pageSize", "type": "integer", "description": "Rows per page (default 50).", "required": False, "default": 50},
        ],
        "transform": "omada_list_client_events",
        "example": '{"totalRows": 4286, "matched": 1, "returned": 1, "truncated": false, "window": {"timeStart": 1789847046000, "timeEnd": 1790451846000}, "rows": [{"id": "6ab81dda77bfbd044e95601b", "time": 1790451159330, "module": "Client", "key": "L_C_CONN", "clientMac": "BC-24-11-6B-B1-41", "content": "[client:BC-24-11-6B-B1-41] went online on [switch:7C-F1-7E-8A-81-1D] on 1-Main_LAN network."}]}',
        "read_only": True,
    },
    {
        "name": "list_site_alerts",
        "description": "List the alert logs for a site in a time window (module, content, time, severity). Provide timeStart and timeEnd as epoch milliseconds; use search to filter by alert content and limit to bound the result.",
        "method": "GET",
        "path_template": "/sites/{siteId}/logs/alerts",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "timeStart", "type": "integer", "query_key": "filters.timeStart", "description": "Start of the window, epoch milliseconds.", "required": True},
            {"name": "timeEnd", "type": "integer", "query_key": "filters.timeEnd", "description": "End of the window, epoch milliseconds.", "required": True},
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
    {
        "name": "acl_reorder",
        "description": "Reorder Omada ACLs (first-match-wins, evaluated top-down). Move ruleId to the position immediately before beforeRuleId. The full rule map is rebuilt server-side — you only express intent; no need to transcribe all rule ids. aclType is switch (osw-acls) or gateway (osg-acls). REQUIRES OPERATOR APPROVAL.",
        "method": "GET",
        "path_template": "/sites/{siteId}/acls/osw-acls",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "aclType", "type": "string", "description": "switch or gateway (osw-acls / osg-acls).", "required": True, "local": True},
            {"name": "ruleId", "type": "string", "description": "The ACL rule id to move.", "required": True, "local": True},
            {"name": "beforeRuleId", "type": "string", "description": "Move ruleId immediately before this ACL rule id.", "required": True, "local": True},
        ],
        "transform": "omada_acl_reorder",
        "example": '{"moved_rule": {"id": "6a86cc3477bfbd044e5f5dc0", "index": 1}, "order": [{"index": 1, "id": "6a86cc3477bfbd044e5f5dc0", "description": "Allow_Kindle2Main"}, {"index": 2, "id": "6a86cc3477bfbd044e5f5dc1", "description": "Block_IoT"}]}',
        "read_only": False,
        "always_gate": True,
    },
    {
        "name": "list_groups",
        "description": "List Omada profile groups for a site with their members (ip, mask, description) for IP / IP-Port groups. Resolve groupId + current membership here before add/remove. Use search (name/id substring) and type (0=IP, 1=IP-Port, 2=MAC, 3=IPv6, 5=Country, 7=Domain) to narrow.",
        "method": "GET",
        "path_template": "/sites/{siteId}/profiles/groups",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "search", "type": "string", "description": "Substring filter on group name or id.", "required": False, "local": True},
            {"name": "type", "type": "integer", "description": "Group type filter (0=IP, 1=IP-Port, 2=MAC, 3=IPv6, 5=Country, 7=Domain).", "required": False, "local": True},
            {"name": "limit", "type": "integer", "description": "Max groups (default 50).", "required": False, "default": 50, "local": True},
        ],
        "transform": "omada_list_groups",
        "example": '[{"groupId": "6a60dbc977bfbd044e48d0b0", "name": "HAOS-Clients", "type": 0, "count": 2, "members": [{"ip": "192.168.15.5", "mask": 32, "description": "Edgewood Frame"}, {"ip": "192.168.15.213", "mask": 32, "description": "Pixel 8 (Megan)"}]}]',
        "read_only": True,
    },
    {
        "name": "group_add_members",
        "description": "Add one or more members to an Omada IP / IP-Port group. Safe path: the group is read first, existing members are preserved, then the merged list is PATCHed (a raw group PATCH REPLACES the whole ipList — this tool never drops members). groupType is derived from the group. REQUIRES OPERATOR APPROVAL.",
        "method": "GET",
        "path_template": "/sites/{siteId}/profiles/groups",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "groupId", "type": "string", "description": "Target group id (from list_groups).", "required": True, "local": True},
            {"name": "members", "type": "array", "description": "Members to add: a JSON array of {ip, mask?, description?} objects. mask defaults to 32.", "required": True, "local": True},
        ],
        "transform": "omada_group_add_members",
        "example": '{"groupId": "6a60dbc977bfbd044e48d0b0", "name": "HAOS-Clients", "type": 0, "count": 3, "members": [{"ip": "192.168.15.5", "mask": 32, "description": "Edgewood Frame"}, {"ip": "192.168.15.213", "mask": 32, "description": "Pixel 8 (Megan)"}, {"ip": "192.168.15.250", "mask": 32, "description": "TMP-plan-test"}]}',
        "read_only": False,
        "always_gate": True,
    },
    {
        "name": "group_remove_members",
        "description": "Remove one or more members (by IP) from an Omada IP / IP-Port group. Safe path: the group is read first, only the listed IPs are removed, and the remaining members are preserved in the PATCHed list. groupType is derived from the group. REQUIRES OPERATOR APPROVAL.",
        "method": "GET",
        "path_template": "/sites/{siteId}/profiles/groups",
        "params": [
            {"name": "siteId", "type": "string", "description": "Site id (from list_sites).", "required": True},
            {"name": "groupId", "type": "string", "description": "Target group id (from list_groups).", "required": True, "local": True},
            {"name": "members", "type": "array", "description": "Members to remove: a JSON array of IP strings (e.g. [\"192.168.15.250\"]).", "required": True, "local": True},
        ],
        "transform": "omada_group_remove_members",
        "example": '{"groupId": "6a60dbc977bfbd044e48d0b0", "name": "HAOS-Clients", "type": 0, "count": 2, "members": [{"ip": "192.168.15.5", "mask": 32, "description": "Edgewood Frame"}]}',
        "read_only": False,
        "always_gate": True,
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
                strip_envelope=tool.get('strip_envelope'),
                transform=tool.get('transform'),
                always_gate=bool(tool.get('always_gate')),
                totals=bool(tool.get('totals')),
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
                version=tool.get('version', 'v1'),
                strip_envelope=tool.get('strip_envelope'),
                transform=tool.get('transform') or '',
                always_gate=bool(tool.get('always_gate')),
                totals=bool(tool.get('totals')),
                seeded=True,
            )
            created += 1
    return created, updated
