"""Seed catalog for the mediated web fetch integration.

The tool is read-only and restricted to the household principal. The URL
allowlist itself is per-principal config in ``web_fetch_policies`` (not a seed).
"""

WEB_FETCH_SEED_TOOLS = [
    {
        'name': 'fetch_url',
        'description': (
            'Fetch a public http(s) URL from the operator allowlist and return '
            'its text as untrusted data (up to 20,000 characters; the cap is '
            'fixed). The allowlist, SSRF checks, redirects, size and '
            'content-type limits are enforced server-side. A result with error '
            '"forbidden" is final — do not retry with a variation.'
        ),
        'method': 'GET',
        'path_template': '',
        'params': [
            {'name': 'url', 'type': 'string',
             'description': 'Absolute http(s) URL on the household allowlist.',
             'required': True},
        ],
        'fields': [],
        'read_only': True,
        'transport': 'http',
        'handler': 'web_fetch',
        'seeded': True,
        'principal_allow': ['household'],
    },
]


def seed_web_fetch_tools(integration_id: int):
    """Idempotently insert/refresh the mediated fetch tool. Preserves the
    operator's enabled toggle; the caller restriction is a security default and
    is refreshed on every seed."""
    from db.integrations import create_tool, get_tools, update_tool
    from db.web_fetch import web_fetch_callers

    # The per-agent URL policy is the source of truth for who can call the
    # tool. Before any policy exists, fall back to the documented placeholder.
    callers = web_fetch_callers() or ['household']
    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in WEB_FETCH_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool['fields'],
                search_field='',
                filter_fields=None,
                transport=tool['transport'],
                generic=False,
                always_gate=False,
                read_only=tool['read_only'],
                seeded=True,
                handler=tool['handler'],
                principal_allow=callers,
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
                '',
                read_only=tool['read_only'],
                fields=tool['fields'],
                transport=tool['transport'],
                generic=False,
                always_gate=False,
                seeded=True,
                handler=tool['handler'],
                principal_allow=callers,
            )
            created += 1
    return created, updated
