"""Curated seed catalog for Nginx Proxy Manager v2 (session/CSRF auth).

Auth is a JWT + CSRF session (core/session_auth.py): login via
POST {token_url} {identity, secret} -> JWT; GET {base}/tokens -> CSRF; every
mutating call carries Authorization: Bearer <jwt> + X-Csrf-Token. The
integration uses auth_type `session`, with client_id (identity / email) and
client_secret (password) + token_url = <base>/tokens.

Read tools are un-gated and compact-by-default; write tools are approval-gated.
NPM is fully curated (NO_GENERIC_KINDS): a generic passthrough would expose
/api/settings (may carry keys) and arbitrary mutations.
"""

NPM_ERROR_CODES = {
    '400': 'invalid_request',
    '401': 'unauthorized',
    '403': 'csrf_failed',
    '404': 'not_found',
    '500': 'npm_unavailable',
    '502': 'npm_unavailable',
    '503': 'npm_unavailable',
    '504': 'npm_unavailable',
}

NPM_SEED_TOOLS = [
    # ── Read tools (un-gated) ─────────────────────────────────────────────
    {
        "name": "proxy_hosts",
        "description": "List NPM proxy hosts (compact: id, domains, forward host/port, enabled, ssl forced, certificate id, nginx online). search filters by domain; limit bounds; full=true adds locations, advanced config and access lists. A host with nginxOnline=false means its config generation failed — the 'host offline' trap.",
        "method": "GET",
        "path_template": "/nginx/proxy-hosts",
        "params": [],
        "fields": ["id", "domain_names", "forward_scheme", "forward_host", "forward_port",
                   "enabled", "ssl_forced", "certificate_id", "meta.nginx_online"],
        "search_field": "domain_names",
        "transform": "npm_proxy_hosts",
        "error_codes": NPM_ERROR_CODES,
        "example": '[{"id": 22, "domain_names": ["haos.local.kenguelacloud.com"], "forward_scheme": "http", "forward_host": "192.168.1.235", "forward_port": 8123, "enabled": true, "ssl_forced": true, "certificate_id": 2, "nginx_online": true}]',
        "read_only": True,
    },
    {
        "name": "proxy_host",
        "description": "Full detail for one proxy host by id, including meta (nginx_online / nginx_err — the 'host offline' diagnostic), locations, advanced config and access lists. Use proxy_hosts first to get ids.",
        "method": "GET",
        "path_template": "/nginx/proxy-hosts/{id}",
        "params": [
            {"name": "id", "type": "integer", "description": "Proxy host id (from npm_proxy_hosts).", "required": True},
        ],
        "fields": [],
        "error_codes": NPM_ERROR_CODES,
        "example": '{"id": 22, "domain_names": ["haos.local.kenguelacloud.com"], "meta": {"nginx_online": true}}',
        "read_only": True,
    },
    {
        "name": "redirection_hosts",
        "description": "List NPM redirection hosts (id, domains, redirect target, enabled). Compact projection.",
        "method": "GET",
        "path_template": "/nginx/redirection-hosts",
        "params": [],
        "fields": ["id", "domain_names", "forward_scheme", "forward_domain_name",
                   "forward_domain_port", "enabled"],
        "error_codes": NPM_ERROR_CODES,
        "example": '[{"id": 1, "domain_names": ["old.local"], "forward_domain_name": "new.local", "enabled": true}]',
        "read_only": True,
    },
    {
        "name": "streams",
        "description": "List NPM TCP/UDP streams (id, incoming host/port, forwarding host/port, enabled). Compact projection.",
        "method": "GET",
        "path_template": "/nginx/streams",
        "params": [],
        "fields": ["id", "incoming_port", "forwarding_host", "forwarding_port", "enabled"],
        "error_codes": NPM_ERROR_CODES,
        "example": '[{"id": 1, "incoming_port": 8443, "forwarding_host": "192.168.1.108", "forwarding_port": 443, "enabled": true}]',
        "read_only": True,
    },
    {
        "name": "custom_locations",
        "description": "List NPM custom locations (id, location path, forward host/port, enabled). Compact projection.",
        "method": "GET",
        "path_template": "/nginx/custom-locations",
        "params": [],
        "fields": ["id", "location", "forward_scheme", "forward_host", "forward_port", "enabled"],
        "error_codes": NPM_ERROR_CODES,
        "example": '[{"id": 1, "location": "/api", "forward_host": "192.168.1.100", "forward_port": 8080, "enabled": true}]',
        "read_only": True,
    },
    {
        "name": "certificates",
        "description": "List NPM SSL certificates (id, domains, provider, expires_on, valid) — for cert-expiry monitoring. Never request Let's Encrypt for internal .local domains (HTTP-01 cannot validate them); the wildcard *.local.kenguelacloud.com cert covers internal hosts.",
        "method": "GET",
        "path_template": "/certificates",
        "params": [],
        "fields": ["id", "provider", "domain_names", "expires_on", "valid", "meta.letsencrypt_email"],
        "transform": "npm_certificates",
        "error_codes": NPM_ERROR_CODES,
        "example": '[{"id": 2, "provider": "other", "domain_names": ["*.local.kenguelacloud.com"], "expires_on": "2027-08-08", "valid": true}]',
        "read_only": True,
    },
    {
        "name": "nginx_status",
        "description": "NPM nginx engine status: running, version and load. Use to confirm the proxy is up before diagnosing a 'host offline' report.",
        "method": "GET",
        "path_template": "/nginx/status",
        "params": [],
        "fields": ["running", "version", "load"],
        "error_codes": NPM_ERROR_CODES,
        "example": '{"running": true, "version": "1.27.3", "load": [1.0, 0.8, 0.6]}',
        "read_only": True,
    },
    {
        "name": "version",
        "description": "NPM API version (major.minor.revision). Confirms the API is reachable.",
        "method": "GET",
        "path_template": "/",
        "params": [],
        "transform": "npm_version",
        "error_codes": NPM_ERROR_CODES,
        "example": '{"version": "2.11.2"}',
        "read_only": True,
    },

    # ── Write tools (approval-gated) ──────────────────────────────────────
    {
        "name": "create_proxy_host",
        "description": "Create a new NPM proxy host. `body` is the full proxy-host object (GET one first for the exact shape — NPM validates the whole payload). REQUIRES OPERATOR APPROVAL.",
        "method": "POST",
        "path_template": "/nginx/proxy-hosts",
        "params": [
            {"name": "body", "type": "json", "description": "Full proxy-host object (domain_names, forward_scheme/host/port, ssl_forced, certificate_id, enabled, ...).", "required": True},
        ],
        "always_gate": True,
        "error_codes": NPM_ERROR_CODES,
        "response_hint": "Proxy host created. Verify via npm_proxy_hosts (and check nginxOnline is true).",
        "example": '{"domain_names": ["app.local.kenguelacloud.com"], "forward_scheme": "http", "forward_host": "192.168.1.100", "forward_port": 8080, "ssl_forced": true, "certificate_id": 2, "enabled": true}',
        "read_only": False,
    },
    {
        "name": "update_proxy_host",
        "description": "Update an NPM proxy host. NPM requires the FULL object — GET the host first, mutate, then PUT it back (partial PUTs 400). The main lifecycle tool: enable/disable, change forward target, toggle ssl_forced. REQUIRES OPERATOR APPROVAL.",
        "method": "PUT",
        "path_template": "/nginx/proxy-hosts/{id}",
        "params": [
            {"name": "id", "type": "integer", "description": "Proxy host id to update.", "required": True},
            {"name": "body", "type": "json", "description": "The full, mutated proxy-host object (GET first).", "required": True},
        ],
        "always_gate": True,
        "error_codes": NPM_ERROR_CODES,
        "response_hint": "Proxy host updated. Verify via npm_proxy_hosts.",
        "example": '{"id": 22, "domain_names": ["haos.local.kenguelacloud.com"], "forward_scheme": "http", "forward_host": "192.168.1.235", "forward_port": 8123, "ssl_forced": true, "certificate_id": 2, "enabled": false}',
        "read_only": False,
    },
    {
        "name": "delete_proxy_host",
        "description": "Delete an NPM proxy host by id — takes the site offline permanently. The approval card shows the domains being removed. REQUIRES OPERATOR APPROVAL.",
        "method": "DELETE",
        "path_template": "/nginx/proxy-hosts/{id}",
        "params": [
            {"name": "id", "type": "integer", "description": "Proxy host id to delete.", "required": True},
        ],
        "always_gate": True,
        "error_codes": NPM_ERROR_CODES,
        "response_hint": "Proxy host deleted. Confirm it is gone from npm_proxy_hosts.",
        "example": '{"ok": true}',
        "read_only": False,
    },
]


def seed_npm_tools(integration_id: int):
    """Idempotently insert/refresh the curated NPM seed tools."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in NPM_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                transform=tool.get('transform'),
                error_codes=tool.get('error_codes'),
                always_gate=tool.get('always_gate'),
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
                transform=tool.get('transform') or '',
                error_codes=tool.get('error_codes') or None,
                always_gate=bool(tool.get('always_gate')),
                response_hint=tool.get('response_hint') or '',
            )
            created += 1
    return created, updated