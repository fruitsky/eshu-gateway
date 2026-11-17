"""F27: allowances find Home Assistant by its kind, not its name.

A real install names the integration ("Home Assistant"). Allowances used to
look it up by the name `ha`, so on a real install no allowance could be saved
and none ever matched. These tests drive the real `run_tool` path with an
integration named the way the operator names it. All data is synthetic.
"""
import json
from contextlib import contextmanager

import pytest

from core import allowances as a
from core.agent_context import reset_current_agent, set_current_agent
from core.tool_runner import run_tool
from db.agent_policies import get_agent_policy, set_agent_policy
from db.agent_tokens import create_agent_token, get_agent_by_token
from db.integrations import (create_integration, create_tool, get_integration,
                             get_pending_calls, get_tool, update_tool)

REGISTRY = {
    'light.lamp': {'id': 'reg-lamp', 'labels': []},
    'switch.kettle': {'id': 'reg-kettle', 'labels': []},
    'switch.server_rack': {'id': 'reg-rack', 'labels': []},
}


@contextmanager
def acting_as(agent):
    token = set_current_agent(agent)
    try:
        yield agent
    finally:
        reset_current_agent(token)


def _ha(base_url, name='Home Assistant'):
    create_integration(name, base_url, 'none', '', kind='ha', gate_mode='all')
    integration = get_integration(name)
    create_tool(integration['id'], 'call_service', 'Call a service', 'POST',
                '/services/{domain}/{service}', [
                    {'name': 'domain', 'type': 'string', 'required': True},
                    {'name': 'service', 'type': 'string', 'required': True},
                    {'name': 'data', 'type': 'json', 'required': False},
                ], '', read_only=False)
    # P2: call_service is always gated; an allowance is the one way past it.
    update_tool(get_tool(name, 'call_service')['id'], always_gate=True)
    return integration


def _household():
    raw, agent_id = create_agent_token('household')
    set_agent_policy(agent_id, tools_mode='allowlist',
                     tools=['home_assistant_call_service'],
                     approval={'home_assistant_call_service': 'ungated'})
    agent = get_agent_by_token(raw)
    agent['policy'] = get_agent_policy(agent_id)
    return agent


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(a, '_ha_registry', lambda: REGISTRY)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    return REGISTRY


def _call(agent, entity, service='turn_off', name='Home Assistant'):
    with acting_as(agent):
        return json.loads(run_tool(name, 'call_service', {
            'domain': entity.split('.')[0], 'service': service,
            'data': {'entity_id': entity}}, reason='household'))


def test_a_named_ha_integration_is_found_by_kind(mock_upstream):
    integration = _ha(mock_upstream['base_url'])
    assert a._ha_integration()['id'] == integration['id']


def test_an_allowance_runs_on_an_integration_named_home_assistant(
        mock_upstream, registry):
    _ha(mock_upstream['base_url'])
    agent = _household()
    a.create_allowance('operator', agent['id'], 'Plugs off', 'switch',
                       'turn_off', ['switch.kettle'])
    out = _call(agent, 'switch.kettle')
    assert out.get('status') != 'pending', out
    assert get_pending_calls() == []
    assert len(mock_upstream['requests']) == 1


def test_an_entity_left_out_still_gets_a_card(mock_upstream, registry):
    _ha(mock_upstream['base_url'])
    agent = _household()
    a.create_allowance('operator', agent['id'], 'Plugs off', 'switch',
                       'turn_off', ['switch.kettle'])
    out = _call(agent, 'switch.server_rack')
    assert out['status'] == 'pending'
    assert mock_upstream['requests'] == []


def test_two_enabled_ha_integrations_match_nothing(mock_upstream, registry):
    _ha(mock_upstream['base_url'])
    agent = _household()
    a.create_allowance('operator', agent['id'], 'Plugs off', 'switch',
                       'turn_off', ['switch.kettle'])
    _ha(mock_upstream['base_url'], name='Second HA')
    assert a._ha_integration() is None
    assert _call(agent, 'switch.kettle')['status'] == 'pending'
    assert mock_upstream['requests'] == []


def test_two_enabled_ha_integrations_refuse_a_save(mock_upstream):
    _ha(mock_upstream['base_url'])
    _ha(mock_upstream['base_url'], name='Second HA')
    agent = _household()
    with pytest.raises(a.AllowanceError) as exc:
        a.create_allowance('operator', agent['id'], 'Plugs off', 'switch',
                           'turn_off', ['switch.kettle'])
    assert exc.value.status == 503
    assert 'exactly one' in exc.value.detail


def test_an_integration_merely_named_ha_does_not_match(mock_upstream,
                                                         registry):
    ha = _ha(mock_upstream['base_url'])
    agent = _household()
    a.create_allowance('operator', agent['id'], 'Plugs off', 'switch',
                       'turn_off', ['switch.kettle'])
    other = dict(ha, id=ha['id'] + 1000, name='ha', kind='pihole')
    assert a.match_and_claim(agent, other, 'call_service', {
        'domain': 'switch', 'service': 'turn_off',
        'data': {'entity_id': 'switch.kettle'}}) is None
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'switch', 'service': 'turn_off',
        'data': {'entity_id': 'switch.kettle'}}) is None
