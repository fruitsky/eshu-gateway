"""X11 Stage 4: the one counting rule behind the summary and the tiles."""
import json

import pytest

from core import agent_tools_summary as summary
from core import allowances
from db import agent_policies, agent_tokens, integrations


def _tool(integration_id, name, read_only=True, always_gate=False):
    integrations.create_tool(
        integration_id, name, name.replace('_', ' ').title(), 'GET', '/x',
        [], '{}', read_only=read_only, always_gate=always_gate)


def _agent(name='test-agent'):
    _raw, agent_id = agent_tokens.create_agent_token(name)
    return agent_id


def _ha_and_sonarr():
    ha = integrations.create_integration(
        'Home Assistant', 'http://192.0.2.30:8123', 'bearer', 'synthetic',
        kind='ha')
    _tool(ha, 'get_config')
    _tool(ha, 'call_service', read_only=False, always_gate=True)
    files = integrations.create_integration(
        'ha-files', '192.0.2.32', 'none', '', kind='ha_ssh')
    integrations.set_integration_config(
        integrations.get_integration('ha-files'),
        {'ha_integration': 'Home Assistant', 'ssh_user': 'root',
         'ssh_port': 22})
    _tool(files, 'ha_config_list')
    _tool(files, 'ha_config_write', read_only=False, always_gate=True)
    sonarr = integrations.create_integration(
        'Sonarr', 'http://192.0.2.40:8989', 'header', 'synthetic',
        kind='sonarr')
    _tool(sonarr, 'series')
    _tool(sonarr, 'add_series', read_only=False, always_gate=True)
    return ha, files, sonarr


def _devices():
    return [
        {'id': 'switch.kettle', 'name': 'Kettle', 'area': 'Kitchen',
         'domain': 'switch', 'category': 'switch', 'sensor_kind': '',
         'useful': True, 'labels': [], 'state': 'off', 'unit': ''},
        {'id': 'switch.lamp', 'name': 'Lamp', 'area': 'Living room',
         'domain': 'switch', 'category': 'switch', 'sensor_kind': '',
         'useful': True, 'labels': [{'id': 'lbl-plugs', 'name': 'Plugs'}],
         'state': 'on', 'unit': ''},
        {'id': 'light.ceiling', 'name': 'Ceiling', 'area': 'Living room',
         'domain': 'light', 'category': 'light', 'sensor_kind': '',
         'useful': True, 'labels': [], 'state': 'on', 'unit': ''},
    ]


def _allowance(agent_id, monkeypatch):
    registry = {
        'switch.kettle': {'id': 'reg-kettle', 'name': 'Kettle'},
        'switch.lamp': {'id': 'reg-lamp', 'name': 'Lamp'},
    }
    monkeypatch.setattr(allowances, '_ha_registry', lambda: registry)
    monkeypatch.setattr(allowances, '_risky_classes', lambda entities: [])
    allowances.ensure_initialised()
    allowances.create_allowance(
        'test', agent_id, 'Kitchen plugs', 'switch', 'turn_off',
        ['switch.kettle'], rate_per_hour=30)
    allowances.create_allowance(
        'test', agent_id, 'Lamp plugs', 'switch', 'turn_off',
        ['switch.lamp'], rate_per_hour=30)


def _custom(gate_mode='destructive', principal_allow=None):
    integration_id = integrations.create_integration(
        'Custom', 'http://192.0.2.80/api', 'none', '', kind='custom',
        gate_mode=gate_mode)
    integrations.create_tool(
        integration_id, 'look', 'Look', 'GET', '/x', [], '{}', read_only=True,
        principal_allow=principal_allow or [])
    integrations.create_tool(
        integration_id, 'change', 'Change', 'POST', '/x', [], '{}',
        read_only=False, principal_allow=principal_allow or [])
    return integrations.get_integration('Custom')


def _policy(agent_id, **kw):
    agent_policies.set_agent_policy(agent_id, **kw)
    return agent_id


def test_wildcard_allowlist_and_approval_fallback(monkeypatch):
    agent_id = _policy(_agent(), tools_mode='allowlist', tools=['custom_*'],
                       approval={'*': 'ungated'})
    _custom()
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']['custom_look'] == 'run'
    assert payload['tool_outcomes']['custom_change'] == 'run'
    _policy(agent_id, tools_mode='allowlist', tools=['custom_*'],
            approval={'*': 'gated'})
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']['custom_change'] == 'ask'
    _policy(agent_id, tools_mode='allowlist', tools=['other_*'])
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']['custom_look'] == 'none'
    assert payload['tool_outcomes']['custom_change'] == 'none'


@pytest.mark.parametrize('gate_mode, expected', [
    ('all', 'ask'), ('destructive', 'depends'), ('none', 'run')])
def test_gate_mode_is_the_real_rule(monkeypatch, gate_mode, expected):
    agent_id = _agent()
    _custom(gate_mode=gate_mode)
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']['custom_look'] == 'run'
    assert payload['tool_outcomes']['custom_change'] == expected


def test_a_principal_allowlist_that_excludes_the_agent_is_none(monkeypatch):
    agent_id = _agent()
    _custom(principal_allow=['someone-else'])
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']['custom_look'] == 'none'
    assert payload['tool_outcomes']['custom_change'] == 'none'


def test_freeze_is_shown_and_reads_still_run(monkeypatch):
    agent_id = _agent()
    _custom()
    monkeypatch.setattr(summary, 'get_trigger_freeze', lambda: True)
    payload = summary.build(agent_id)
    assert payload['frozen'] is True
    assert payload['tool_outcomes']['custom_look'] == 'run'
    assert payload['tool_outcomes']['custom_change'] == 'depends'
    assert payload['frozen_tools']['custom_change'] is True
    assert payload['totals']['frozen'] == 1
    assert payload['totals']['run'] == 1
    assert payload['totals']['depends'] == 1
    assert 'Emergency Freeze is on' in payload['summary']


def test_summary_and_tiles_use_one_counting_rule(monkeypatch):
    agent_id = _agent()
    _ha_and_sonarr()
    _devices_mock = _devices()
    monkeypatch.setattr('core.ha_devices.devices', lambda: _devices_mock)
    _allowance(agent_id, monkeypatch)

    payload = summary.build(agent_id)
    totals = payload['totals']
    tiles = payload['tiles']

    assert totals['tools'] == 6
    assert totals['run'] == 3
    assert totals['ask'] == 3
    assert totals['integrations'] == 4
    assert totals['in_use'] == 2
    assert totals['devices'] == 2
    assert totals['device_noun'] == 'plugs'
    assert len(tiles) == 4
    assert sum(t['usable'] for t in tiles) == totals['tools']
    assert sum(t['run'] for t in tiles) == totals['run']
    assert sum(t['ask'] for t in tiles) == totals['ask']
    assert sum(1 for t in tiles if t['in_use']) == totals['in_use']
    assert payload['summary'] == (
        'test-agent can use 6 tools in 2 of 4 integrations. '
        '3 ask you first; 3 run on their own, plus 2 plugs.')
    assert 'secret' not in json.dumps(payload).lower()
    assert 'synthetic' not in json.dumps(payload)

    ha = [t for t in tiles if t['name'] == 'Home Assistant'][0]
    assert (ha['total'], ha['run'], ha['ask'], ha['none']) == (4, 2, 2, 0)
    assert ha['extra'] == '2 plugs run on their own'
    sonarr = [t for t in tiles if t['name'] == 'Sonarr'][0]
    assert (sonarr['total'], sonarr['run'], sonarr['ask']) == (2, 1, 1)
    ssh = [t for t in tiles if t['name'] == 'SSH'][0]
    assert ssh['extra'] == 'No hosts yet' and ssh['in_use'] is False
    web = [t for t in tiles if t['name'] == 'Websites'][0]
    assert web['extra'] == 'No sites allowed'


@pytest.mark.parametrize('mode, expected', [
    ('normal', 'depends'), ('approval', 'ask'), ('read_only', 'run')])
def test_the_ssh_tile_uses_the_same_static_decision(monkeypatch, mode, expected):
    agent_id = _agent()
    agent_policies.set_agent_policy(
        agent_id, tools_mode='allowlist',
        tools=['home_assistant_call_service'],
        ssh_hosts_allow=['192.0.2.10'], ssh_mode=mode)
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    payload = summary.build(agent_id)
    ssh = [t for t in payload['tiles'] if t['name'] == 'SSH'][0]
    assert payload['tool_outcomes']['ssh_exec'] == expected
    assert (ssh['run'], ssh['ask'], ssh['depends']) == (
        (1, 0, 0) if expected == 'run' else
        (0, 1, 0) if expected == 'ask' else (0, 0, 1))
    assert 'ssh_exec' in agent_policies.get_agent_policy(agent_id)['tools']
    assert ssh['extra'] == 'On 192.0.2.10'


def test_the_ssh_tile_does_not_run_on_hosts_alone(monkeypatch):
    agent_id = _agent()
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    payload = summary.build(agent_id)
    ssh = [t for t in payload['tiles'] if t['name'] == 'SSH'][0]
    assert payload['tool_outcomes']['ssh_exec'] == 'none'
    assert ssh['run'] == 0 and ssh['usable'] == 0 and ssh['in_use'] is False


def test_allowlist_and_explicit_approval_change_the_same_totals(monkeypatch):
    agent_id = _agent()
    _ha_and_sonarr()
    monkeypatch.setattr('core.ha_devices.devices', lambda: _devices())
    _allowance(agent_id, monkeypatch)
    agent_policies.set_agent_policy(
        agent_id, tools_mode='allowlist',
        tools=['home_assistant_get_config', 'home_assistant_call_service',
               'ha_files_ha_config_list', 'sonarr_series'],
        approval={'home_assistant_get_config': 'gated'})

    payload = summary.build(agent_id)
    assert payload['totals']['tools'] == 4
    # A gated override does not affect a read; call_service is always gated.
    assert payload['totals']['ask'] == 1
    assert payload['totals']['run'] == 3
    assert payload['totals']['in_use'] == 2
    assert 'can use 4 tools in 2 of 4 integrations' in payload['summary']
    assert payload['totals']['tools'] == sum(
        t['usable'] for t in payload['tiles'])


def test_ha_files_are_counted_under_ha_not_as_a_tile(monkeypatch):
    agent_id = _agent()
    _ha_and_sonarr()
    monkeypatch.setattr('core.ha_devices.devices', lambda: _devices())
    payload = summary.build(agent_id)
    names = [t['name'] for t in payload['tiles']]
    assert 'ha-files' not in names
    assert 'Home Assistant' in names


def test_integration_with_no_tools_is_not_connected(monkeypatch):
    agent_id = _agent()
    integrations.create_integration(
        'Empty shelf', 'http://192.0.2.70', 'none', '', kind='custom')
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    payload = summary.build(agent_id)
    empty = [t for t in payload['tiles'] if t['name'] == 'Empty shelf'][0]
    assert empty['total'] == 0 and empty['in_use'] is False
    assert payload['totals']['in_use'] == 0
    assert 'has no tools switched on yet' in payload['summary']


def test_unlinked_files_row_is_its_own_tile(monkeypatch):
    agent_id = _agent()
    files = integrations.create_integration(
        'orphan-files', '192.0.2.33', 'none', '', kind='ha_ssh')
    _tool(files, 'ha_config_list')
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    payload = summary.build(agent_id)
    assert 'orphan-files' in [t['name'] for t in payload['tiles']]


def test_endpoint_is_operator_only(auth_client, client):
    import main
    agent_id = _agent()
    raw, _ = agent_tokens.create_agent_token('bearer-agent')
    resp = auth_client.get('/api/agents/%d/tools-summary' % agent_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body['agent']['id'] == agent_id
    assert 'tiles' in body and 'summary' in body

    from fastapi.testclient import TestClient
    fresh = TestClient(main.app)
    unauth = fresh.get('/api/agents/%d/tools-summary' % agent_id)
    assert unauth.status_code == 401
    bearer = fresh.get('/api/agents/%d/tools-summary' % agent_id,
                       headers={'Authorization': 'Bearer ' + raw})
    assert bearer.status_code in (401, 403)
    from db.approver_tokens import create_approver_token
    approver_raw, _tid = create_approver_token(
        agent_id, ['hostname'], '192.0.2.10', 1, actor='test')
    approver = fresh.get('/api/agents/%d/tools-summary' % agent_id,
                         headers={'Authorization': 'Bearer ' + approver_raw})
    assert approver.status_code in (401, 403)


def test_seed_labels_reach_available_tools(auth_client):
    from core.seeds import seed_for_kind, seed_label
    from db.integrations import get_integration
    import main as app_main

    assert seed_label('sonarr', 'wanted_missing') == 'Missing episodes'
    assert seed_label('sonarr', 'series') == 'Series'
    assert seed_label('ha_ssh', 'ha_config_write') == 'Write a file'
    assert seed_label('sonarr', 'not_a_tool') == ''
    integrations.create_integration(
        'Sonarr', 'http://192.0.2.40:8989', 'header', 'synthetic',
        kind='sonarr')
    seed_for_kind(get_integration('Sonarr'))
    agent_id = _agent()
    resp = auth_client.get('/api/agents/%d/available-tools' % agent_id)
    assert resp.status_code == 200
    groups = resp.json()['integrations']
    sonarr = [g for g in groups if g['integration'] == 'Sonarr'][0]
    wanted = [t for t in sonarr['tools'] if t['tool'] == 'wanted_missing'][0]
    assert wanted['label'] == 'Missing episodes'
    assert isinstance(wanted['params'], list) and wanted['params']


def test_every_seeded_tool_matches_the_shared_decision(monkeypatch):
    from core import seeds
    from core.agent_policy import static_tool_decision

    agent_id = _agent()
    for kind in seeds.SEEDERS:
        integration_id = integrations.create_integration(
            'seed-' + kind, 'http://192.0.2.90/' + kind, 'none', '',
            kind=kind)
        integration = integrations.get_integration_by_id(integration_id)
        seeds.seed_for_kind(integration)
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    agent = agent_tokens.get_agent(agent_id)
    agent['policy'] = agent_policies.get_agent_policy(agent_id)
    payload = summary.build(agent_id)
    assert payload['tool_outcomes']
    from core.agent_policy import safe_ident
    owners = {safe_ident(i['name']).lower(): i
              for i in integrations.get_integrations()}
    for name, outcome in payload['tool_outcomes'].items():
        if name == 'ssh_exec':
            # The SSH tile has its own synthetic owner; pinned below.
            continue
        key = next(key for key in sorted(owners, key=len, reverse=True)
                   if name.startswith(key + '_'))
        owner = owners[key]
        tool_name = name[len(key) + 1:]
        tool = integrations.get_tool(owner['name'], tool_name)
        unfrozen = static_tool_decision(agent, owner, tool_name, tool)
        frozen = static_tool_decision(
            agent, owner, tool_name, tool, frozen=True)
        assert outcome == unfrozen['outcome']
        assert frozen['outcome'] == unfrozen['outcome']
        assert frozen['should_gate'] == unfrozen['should_gate'], name
        assert frozen['frozen'] == bool(unfrozen['allowed'] and not tool.get('read_only')), name


def test_label_scope_counts_pins_and_waiting_separately(monkeypatch):
    agent_id = _agent()
    monkeypatch.setattr('core.ha_devices.devices', lambda: _devices())
    monkeypatch.setattr(allowances, '_ha_registry', lambda: {
        'switch.kettle': {'id': 'reg-kettle', 'name': 'Kettle'},
        'switch.lamp': {'id': 'reg-lamp', 'name': 'Lamp'},
    })
    monkeypatch.setattr(allowances, '_risky_classes', lambda entities: [])
    allowances.ensure_initialised()
    row = allowances.create_allowance(
        'test', agent_id, 'Plugs', 'switch', 'turn_off', ['switch.kettle'],
        rate_per_hour=30, source_label_id='lbl-plugs')
    from db.core import db_conn
    with db_conn() as conn:
        conn.execute(
            'UPDATE allowances SET pending_json = ? WHERE id = ?',
            (json.dumps([{'entity_id': 'switch.lamp',
                          'registry_id': 'reg-lamp'}]), row['id']))
        conn.commit()
    payload = summary.build(agent_id)
    assert payload['totals']['devices'] == 1
    assert payload['totals']['waiting'] == 1
    ha = [t for t in payload['tiles'] if t['name'] == 'Home Assistant']
    assert ha == []  # no HA integration in this test
    assert 'waiting to join' in payload['summary'] or payload['totals']['waiting'] == 1


def test_a_non_useful_pinned_device_still_counts(monkeypatch):
    agent_id = _agent()
    hidden = {'id': 'switch.hidden', 'name': 'Hidden plug',
              'area': 'Utility', 'domain': 'switch', 'category': 'switch',
              'sensor_kind': '', 'useful': False, 'labels': [],
              'state': 'off', 'unit': ''}
    monkeypatch.setattr('core.ha_devices.devices', lambda: [hidden])
    monkeypatch.setattr(allowances, '_ha_registry', lambda: {
        'switch.hidden': {'id': 'reg-hidden', 'name': 'Hidden plug'}})
    monkeypatch.setattr(allowances, '_risky_classes', lambda entities: [])
    allowances.ensure_initialised()
    allowances.create_allowance(
        'test', agent_id, 'Hidden plug', 'switch', 'turn_off',
        ['switch.hidden'], rate_per_hour=30)
    payload = summary.build(agent_id)
    # `useful` is a picker default, never the permission's scope.
    assert payload['totals']['devices'] == 1


def test_tiles_carry_curated_display_names(monkeypatch):
    agent_id = _agent()
    integrations.create_integration(
        'commute', 'http://192.0.2.91', 'none', '', kind='commute')
    integrations.create_integration(
        'tfl', 'http://192.0.2.92', 'none', '', kind='trains')
    integrations.create_integration(
        'webfetch', 'http://192.0.2.93', 'none', '', kind='webfetch')
    integrations.create_integration(
        'lower case', 'http://192.0.2.94', 'none', '', kind='custom')
    monkeypatch.setattr('core.ha_devices.devices', lambda: [])
    rows = {t['name']: t for t in summary.build(agent_id)['tiles']}
    assert rows['commute']['display'] == 'Commute'
    assert rows['tfl']['display'] == 'TfL'
    assert rows['webfetch']['display'] == 'Web fetch'
    assert rows['lower case']['display'] == 'Lower case'
