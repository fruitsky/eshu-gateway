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


def test_outcome_defaults_and_overrides():
    config = {'tools': {'mode': 'all', 'allow': [], 'approval': {}}}
    read = {'name': 'ha_get', 'read_only': True, 'always_gate': False}
    write = {'name': 'ha_call', 'read_only': False, 'always_gate': False}
    assert summary.tool_outcome(read, config) == 'run'
    assert summary.tool_outcome(write, config) == 'ask'
    config['tools']['approval'] = {'ha_get': 'gated', 'ha_call': 'ungated'}
    assert summary.tool_outcome(read, config) == 'ask'
    assert summary.tool_outcome(write, config) == 'run'
    always = {'name': 'ha_call', 'read_only': False, 'always_gate': True}
    config['tools']['approval'] = {'ha_call': 'ungated'}
    assert summary.tool_outcome(always, config) == 'ask'
    config['tools']['mode'] = 'allowlist'
    config['tools']['allow'] = ['ha_get']
    assert summary.tool_outcome(write, config) == 'none'


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
    assert payload['totals']['ask'] == 2   # get_config gated; call_service always
    assert payload['totals']['run'] == 2   # files list, series
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
