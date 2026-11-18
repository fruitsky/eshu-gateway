"""The label registry is read-only metadata and only `list` is a read."""
import json

import pytest

from core.agent_context import reset_current_agent, set_current_agent
from core.generic_tools import seed_generic_tools
from core.ha_ws_policy import is_read_command
from core.tool_runner import run_tool
from db.agent_policies import get_agent_policy, set_agent_policy
from db.agent_tokens import create_agent_token, get_agent_by_token
from db.integrations import create_integration, get_integration


def _ha():
    create_integration('ha', 'http://192.0.2.10:8123/api', 'none', '', kind='ha')
    seed_generic_tools(get_integration('ha')['id'], 'ha')


def test_label_list_is_an_explicit_read():
    assert is_read_command('config/label_registry/list') is True


def test_label_writes_and_other_registries_are_unchanged():
    for command in ('config/label_registry/create',
                    'config/label_registry/update',
                    'config/label_registry/delete',
                    'config/device_registry/remove',
                    'config/entity_registry/update'):
        assert is_read_command(command) is False, command
    # The existing read registries keep their behaviour.
    for command in ('config/area_registry/list',
                    'config/device_registry/list',
                    'config/entity_registry/list'):
        assert is_read_command(command) is True, command


def test_an_agent_with_ha_read_access_can_list_labels(monkeypatch):
    import core.ha_ws as ha_ws
    _ha()
    seen = {}
    monkeypatch.setattr(ha_ws, 'ha_ws_request',
                        lambda integration, command, payload: seen.update(
                            command=command) or [{'label_id': 'lbl-1',
                                                  'name': 'Plugs'}])
    out = json.loads(run_tool('ha', 'ws_read', {
        'command': 'config/label_registry/list'}))
    assert seen['command'] == 'config/label_registry/list'
    assert out == [{'label_id': 'lbl-1', 'name': 'Plugs'}]


def test_an_agent_without_ha_read_access_is_refused_before_connecting(
        monkeypatch):
    import core.ha_ws as ha_ws
    _ha()
    monkeypatch.setattr(ha_ws, 'ha_ws_request',
                        lambda *args, **kwargs: pytest.fail('opened a WebSocket'))
    raw, aid = create_agent_token('no-ha-read')
    set_agent_policy(aid, tools_mode='allowlist', tools=[])
    agent = get_agent_by_token(raw)
    agent['policy'] = get_agent_policy(aid)
    token = set_current_agent(agent)
    try:
        out = json.loads(run_tool('ha', 'ws_read', {
            'command': 'config/label_registry/list'}))
    finally:
        reset_current_agent(token)
    assert out.get('error') == 'forbidden'
    assert 'allowlist' in (out.get('reason') or '')


def test_label_create_update_delete_are_refused_before_connecting(monkeypatch):
    import core.ha_ws as ha_ws
    _ha()
    monkeypatch.setattr(ha_ws, 'ha_ws_request',
                        lambda *args, **kwargs: pytest.fail('opened a WebSocket'))
    for command in ('config/label_registry/create',
                    'config/label_registry/update',
                    'config/label_registry/delete'):
        out = json.loads(run_tool('ha', 'ws_read', {'command': command}))
        assert out.get('error') == 'not_a_read_command', command
        assert out.get('status_code') == 400
