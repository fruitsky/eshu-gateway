"""X6/D18b — the Pi-hole seeder must store the handler.

On a live install the seeded `local_dns_records` row had `handler=''` and
`path_template=''`, so the read went to the integration's base URL (Pi-hole's
HTML login page) and the inventory reported `bad_json`. The curated catalog
carried the handler; `seed_pihole_tools` never passed it to create_tool /
update_tool. Tests created the tool with the handler set, which is why they
missed it.
"""
import json

import pytest


def _pihole(name='pihole-x6'):
    from db.integrations import create_integration, get_integration
    create_integration(name, 'http://192.0.2.10/api', 'query_token', 'tok',
                       kind='pihole')
    return get_integration(name)


def test_a_fresh_seed_stores_the_handler():
    from core.pihole_seed import seed_pihole_tools
    from db.integrations import get_tool
    integration = _pihole()
    seed_pihole_tools(integration['id'])
    tool = get_tool('pihole-x6', 'local_dns_records')
    assert tool['handler'] == 'pihole_local_dns_records'
    assert tool['path_template'] == ''
    # The other seeded tools are plain HTTP calls with a path.
    assert get_tool('pihole-x6', 'summary')['path_template'] == 'api.php?summary'


def test_reseeding_repairs_a_live_install(tmp_path):
    """The old seeder left handler='' — a re-seed must fill it in."""
    from core.pihole_seed import seed_pihole_tools
    from db.integrations import create_tool, get_tool
    integration = _pihole('pihole-old')
    create_tool(integration['id'], 'local_dns_records',
                'old description', 'GET', '', [], 'example',
                read_only=True, seeded=True)
    before = get_tool('pihole-old', 'local_dns_records')
    assert before['handler'] == '' and before['path_template'] == ''
    seed_pihole_tools(integration['id'])
    after = get_tool('pihole-old', 'local_dns_records')
    assert after['handler'] == 'pihole_local_dns_records'


def test_the_inventory_source_works_through_the_seeded_tool(monkeypatch):
    from core.inventory import _call_tool
    from core.pihole_seed import seed_pihole_tools
    integration = _pihole('pihole-inv')
    seed_pihole_tools(integration['id'])

    def fake_exec(integration, probe, args, agent=''):
        return {'body': json.dumps({'config': {'dns': {
            'hosts': ['192.0.2.10 web01.example.test'],
            'cnameRecords': ['app.example.test web01.example.test'],
        }}})}

    monkeypatch.setattr('core.tool_handlers.execute_integration_call', fake_exec)
    data = _call_tool(integration, 'local_dns_records', {})
    assert data['hosts'] == [{'name': 'web01.example.test',
                              'ips': ['192.0.2.10']}]
    assert data['cnames'] == [{'name': 'app.example.test',
                               'target': 'web01.example.test'}]


SEEDERS = [
    ('arr_seed', 'seed_sonarr_tools'), ('arr_seed', 'seed_radarr_tools'),
    ('bazarr_seed', 'seed_bazarr_tools'), ('commute_seed', 'seed_commute_tools'),
    ('ha_seed', 'seed_ha_tools'), ('jellyfin_seed', 'seed_jellyfin_tools'),
    ('npm_seed', 'seed_npm_tools'), ('omada_seed', 'seed_omada_tools'),
    ('pihole_seed', 'seed_pihole_tools'),
    ('prowlarr_seed', 'seed_prowlarr_tools'),
    ('proxmox_seed', 'seed_proxmox_tools'), ('pulse_seed', 'seed_pulse_tools'),
    ('seerr_seed', 'seed_seerr_tools'), ('train_seed', 'seed_train_tools'),
    ('web_fetch_seed', 'seed_web_fetch_tools'),
]


def test_every_seeded_tool_has_a_handler_or_a_path(temp_db):
    """The audit the reviewer asked for: no seeded tool may end up with both
    empty. Every tool in every catalog has a handler (it runs in-process) or a
    path_template (it is a plain HTTP call)."""
    import importlib

    from db.integrations import create_integration, get_tools

    missing = []
    for module_name, function_name in SEEDERS:
        name = 'seed-' + function_name
        integration_id = create_integration(
            name, 'http://192.0.2.10', 'none', '',
            kind=module_name.replace('_seed', ''))
        module = importlib.import_module('core.' + module_name)
        getattr(module, function_name)(integration_id)
        for tool in get_tools(integration_id):
            if not (tool.get('handler') or tool.get('path_template')):
                missing.append((module_name, tool['name']))
    assert missing == [], missing
