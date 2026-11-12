"""F11: the curated tool catalog for an `ha_ssh` integration.

One integration record per Advanced SSH & Web Terminal add-on: `base_url` is
`host` or `host:port`, `secret` is the Eshu-only private key, and
`config_json` holds `ssh_user`, `ssh_port`, the pinned `host_key` and its
`host_fingerprint`. All five tools run fixed command templates over SSH; the
write and restore tools are always approval-gated.
"""
HA_SSH_SEED_TOOLS = [
    {
        'name': 'ha_config_list',
        'description': (
            'List the YAML files under /config and /config/packages on the '
            'Home Assistant SSH add-on, with size and mtime. Read-only. The '
            'first call also pins the add-on\'s host key (TOFU); compare the '
            'fingerprint in the integration card with the add-on before '
            'writing.'),
        'method': 'GET',
        'path_template': '',
        'handler': 'ha_config_list',
        'params': [],
        'fields': [],
        'example': '{"files": [{"path": "/config/configuration.yaml", "size": 812, "mtime": 1762000000}]}',
        'read_only': True,
    },
    {
        'name': 'ha_config_read',
        'description': (
            'Read one YAML file under /config or /config/packages: content, '
            'sha256 and size. secrets.yaml returns key names with values '
            'masked (secrets_masked: true). Pass the sha256 as expect_hash to '
            'ha_config_write so the write refuses if the file changed. '
            'Read-only.'),
        'method': 'GET',
        'path_template': '',
        'handler': 'ha_config_read',
        'params': [
            {'name': 'path', 'type': 'string',
             'description': 'e.g. /config/configuration.yaml or /config/packages/net/x.yaml',
             'required': True},
        ],
        'fields': [],
        'example': '{"path": "/config/configuration.yaml", "sha256": "…", "size": 812, "content": "…"}',
        'read_only': True,
    },
    {
        'name': 'ha_config_write',
        'description': (
            'Replace one YAML file: one remote script backs up the current '
            'file, writes the new content atomically, keeps its mode/owner, '
            'runs ha core check and restores the backup automatically if the '
            'check fails. dry_run=true returns the diff without approval. '
            'A real write is always approval-gated, and expect_hash (from '
            'ha_config_read) must still match or nothing is written.'),
        'method': 'POST',
        'path_template': '',
        'handler': 'ha_config_write',
        'params': [
            {'name': 'path', 'type': 'string', 'description': 'target .yaml/.yml path', 'required': True},
            {'name': 'content', 'type': 'string', 'description': 'the complete new file content', 'required': True},
            {'name': 'expect_hash', 'type': 'string', 'description': 'sha256 from ha_config_read', 'required': True},
            {'name': 'dry_run', 'type': 'boolean', 'description': 'return the diff only, no write', 'required': False, 'default': False},
        ],
        'fields': [],
        'example': '{"ok": true, "path": "/config/configuration.yaml", "backup": "/config/configuration.yaml.eshu-1762000000.bak", "check": "valid"}',
        'read_only': False,
        'always_gate': True,
    },
    {
        'name': 'ha_config_restore',
        'description': (
            'Restore one .eshu-<timestamp>.bak for a YAML path, then run '
            'ha core check; a failed check puts the previous content back. '
            'Always approval-gated. Nothing is deleted.'),
        'method': 'POST',
        'path_template': '',
        'handler': 'ha_config_restore',
        'params': [
            {'name': 'path', 'type': 'string', 'description': 'the .yaml path the backup belongs to', 'required': True},
            {'name': 'backup', 'type': 'string', 'description': 'the .eshu-<ts>.bak name', 'required': True},
        ],
        'fields': [],
        'example': '{"ok": true, "path": "/config/configuration.yaml", "backup": "/config/configuration.yaml.eshu-1762000000.bak", "check": "valid"}',
        'read_only': False,
        'always_gate': True,
    },
    {
        'name': 'ha_config_check',
        'description': (
            'Run ha core check on the add-on. A valid result is recorded for '
            'the next ha_core_restart (10-minute window, shared with F10\'s '
            'check). Read-only.'),
        'method': 'GET',
        'path_template': '',
        'handler': 'ha_config_check',
        'params': [],
        'fields': [],
        'example': '{"result": "valid", "ok": true, "output": ""}',
        'read_only': True,
    },
]


def seed_ha_ssh_tools(integration_id: int):
    """Idempotently insert/refresh the HA SSH catalog. Returns (created, updated)."""
    from db.integrations import create_tool, get_tools, update_tool

    existing = {t['name']: t for t in get_tools(integration_id)}
    created = 0
    updated = 0
    for tool in HA_SSH_SEED_TOOLS:
        if tool['name'] in existing:
            update_tool(
                existing[tool['name']]['id'],
                name=tool['name'],
                description=tool['description'],
                method=tool['method'],
                path_template=tool['path_template'],
                params=tool['params'],
                fields=tool.get('fields'),
                always_gate=tool.get('always_gate'),
                example=tool['example'],
                read_only=tool['read_only'],
                handler=tool.get('handler') or '',
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
                always_gate=bool(tool.get('always_gate')),
                handler=tool.get('handler') or '',
                seeded=True,
            )
            created += 1
    return created, updated
