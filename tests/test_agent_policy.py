"""Per-agent MCP policy: tool allowlists, argument rules, approval overrides,
attribution, and approval ownership."""
import asyncio
import json
from contextlib import contextmanager

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from core.agent_context import reset_current_agent, set_current_agent
from core.agent_policy import evaluate_call, tool_key
from core.mcp_server import check_approval_fn, mcp as eshu_mcp
from db.agent_policies import (
    default_policy,
    get_agent_policy,
    set_agent_policy,
    validate_policy,
)
from db.agent_tokens import create_agent_token, get_agent_by_token
from db.integrations import (
    create_integration,
    create_pending_call,
    create_tool,
    get_integration,
    get_integration_calls,
    get_pending_call,
    get_pending_calls,
)
from core.tool_runner import run_tool


@contextmanager
def acting_as(agent):
    token = set_current_agent(agent)
    try:
        yield agent
    finally:
        reset_current_agent(token)


def _agent(name, **policy):
    raw, agent_id = create_agent_token(name)
    if policy:
        set_agent_policy(agent_id, **policy)
    agent = get_agent_by_token(raw)
    agent['policy'] = get_agent_policy(agent_id)
    return agent


def _ha(mock_upstream, gate_mode='all'):
    create_integration('Home Assistant', mock_upstream['base_url'], 'none', '',
                       kind='ha', gate_mode=gate_mode)
    integration = get_integration('Home Assistant')
    create_tool(integration['id'], 'list_entities', 'List states', 'GET', '/states',
                [], '', read_only=True)
    create_tool(integration['id'], 'call_service', 'Call a service', 'POST',
                '/services/{domain}/{service}', [
                    {'name': 'domain', 'type': 'string', 'required': True},
                    {'name': 'service', 'type': 'string', 'required': True},
                    {'name': 'data', 'type': 'json', 'required': False},
                ], '', read_only=False)
    create_tool(integration['id'], 'write', 'Generic write', 'POST', '', [
        {'name': 'method', 'type': 'string', 'required': True},
        {'name': 'path', 'type': 'string', 'required': True},
        {'name': 'data', 'type': 'json', 'required': False},
    ], '', read_only=False, generic=True)
    return integration


def _household(name='household', **overrides):
    policy = dict(
        tools_mode='allowlist',
        tools=['home_assistant_list_entities', 'home_assistant_call_service'],
        arg_policy={
            'home_assistant_call_service': {
                'domain_allow': ['light', 'switch', 'scene'],
                'domain_deny': ['homeassistant', 'hassio', 'shell_command',
                                'script', 'command_line'],
                'service_deny': ['reload', 'restart', 'stop'],
            }
        },
        approval={'home_assistant_call_service': 'ungated'},
    )
    policy.update(overrides)
    return _agent(name, **policy)


class TestPolicyValidation:
    def test_default_is_admin_compatible(self):
        policy = default_policy(7)
        assert policy['tools_mode'] == 'all'
        assert policy['approval'] == {}
        assert policy['rate_limit_per_min'] == 0
        assert get_agent_policy(7)['tools_mode'] == 'all'

    def test_bad_values_rejected(self):
        with pytest.raises(ValueError):
            validate_policy(tools_mode='nope')
        with pytest.raises(ValueError):
            validate_policy(approval={'x': 'maybe'})
        with pytest.raises(ValueError):
            validate_policy(tools=[''])
        with pytest.raises(ValueError):
            validate_policy(rate_limit_per_min=-1)

    def test_tool_key_is_namespaced(self):
        assert tool_key('Home Assistant', 'call_service') == 'home_assistant_call_service'


class TestToolAllowlist:
    def test_unknown_tool_denied_by_default(self):
        agent = _household()
        allowed, reason = evaluate_call(
            agent, 'Home Assistant', 'write', {'method': 'POST', 'path': '/x'})
        assert not allowed
        assert 'allowlist' in reason

    def test_unlisted_tool_call_returns_explicit_denial(self, mock_upstream):
        _ha(mock_upstream)
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'write', {
                'method': 'POST', 'path': '/api/config'}, reason='x'))
        assert out['error'] == 'forbidden'
        assert 'allowlist' in out['reason']
        assert mock_upstream['requests'] == []
        row = get_integration_calls(limit=1)['rows'][0]
        assert row['agent'] == 'household'
        assert row['outcome'] == 'denied'
        assert row['approval'] == 'denied'

    def test_call_tool_denies_unlisted_at_transport(self, mock_upstream):
        from core.mcp_server import refresh_mcp_tools
        _ha(mock_upstream)
        refresh_mcp_tools()
        agent = _household()
        with acting_as(agent):
            with pytest.raises(ToolError) as exc:
                asyncio.run(eshu_mcp.call_tool('home_assistant_write', {
                    'method': 'POST', 'path': '/api/config'}))
        assert 'not in this agent' in str(exc.value)
        assert mock_upstream['requests'] == []

    def test_call_tool_binds_caller_for_run_tool(self, mock_upstream):
        from core.mcp_server import refresh_mcp_tools
        _ha(mock_upstream)
        refresh_mcp_tools()
        agent = _household()
        with acting_as(agent):
            asyncio.run(eshu_mcp.call_tool('home_assistant_list_entities', {}))
        row = get_integration_calls(limit=1)['rows'][0]
        assert row['tool'] == 'list_entities'
        assert row['agent'] == 'household'

    def test_list_tools_filters_for_allowlisted_agent(self, mock_upstream):
        from core.mcp_server import refresh_mcp_tools
        _ha(mock_upstream)
        refresh_mcp_tools()

        # Admin with no policy row keeps the historical full surface.
        admin = _agent('admin-plain')
        with acting_as(admin):
            names = {t.name for t in asyncio.run(eshu_mcp.list_tools())}
        assert 'home_assistant_call_service' in names
        assert 'home_assistant_write' in names

        household = _household()
        with acting_as(household):
            names = {t.name for t in asyncio.run(eshu_mcp.list_tools())}
        assert 'home_assistant_call_service' in names
        assert 'home_assistant_list_entities' in names
        assert 'home_assistant_write' not in names
        assert 'check_approval' in names


class TestHaArgumentPolicy:
    def test_admin_domain_denied_without_upstream(self, mock_upstream):
        _ha(mock_upstream)
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'homeassistant', 'service': 'restart'}, reason='x'))
        assert out['error'] == 'forbidden'
        assert "domain 'homeassistant'" in out['reason']
        assert mock_upstream['requests'] == []

    def test_light_turn_on_allowed_and_ungated(self, mock_upstream):
        _ha(mock_upstream, gate_mode='all')
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'light', 'service': 'turn_on',
                'data': {'entity_id': 'light.living_room'}}, reason='turn on'))
        assert 'status' not in out or out.get('status') != 'pending'
        assert mock_upstream['requests'][-1]['method'] == 'POST'
        assert mock_upstream['requests'][-1]['path'] == '/services/light/turn_on'
        # The agent override bypassed the integration's gate_mode=all.
        assert get_pending_calls() == []
        row = get_integration_calls(limit=1)['rows'][0]
        assert row['agent'] == 'household'
        assert row['approval'] == 'auto'

    def test_script_domain_denied(self, mock_upstream):
        _ha(mock_upstream)
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'script', 'service': 'turn_on'}, reason='x'))
        assert out['error'] == 'forbidden'
        assert mock_upstream['requests'] == []

    def test_service_deny_wins(self, mock_upstream):
        _ha(mock_upstream)
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'light', 'service': 'reload'}, reason='x'))
        assert out['error'] == 'forbidden'
        assert 'service' in out['reason']

    def test_domain_deny_beats_allow(self, mock_upstream):
        _ha(mock_upstream)
        agent = _household(arg_policy={
            'home_assistant_call_service': {
                'domain_allow': ['light', 'homeassistant'],
                'domain_deny': ['homeassistant'],
            }
        })
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'homeassistant', 'service': 'restart'}, reason='x'))
        assert out['error'] == 'forbidden'
        assert 'denied' in out['reason']


class TestApprovalClass:
    def test_admin_call_service_gated_with_attribution(self, mock_upstream):
        _ha(mock_upstream, gate_mode='all')
        admin = _agent('hermes-admin')
        with acting_as(admin):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'light', 'service': 'turn_on'}, reason='admin turn on'))
        assert out['status'] == 'pending'
        pending = get_pending_call(out['id'])
        assert pending['agent'] == 'hermes-admin'
        assert mock_upstream['requests'] == []

    def test_admin_always_gate_wins_over_destructive(self, mock_upstream):
        from db.integrations import get_tool, update_tool
        _ha(mock_upstream, gate_mode='destructive')
        update_tool(get_tool('Home Assistant', 'call_service')['id'], always_gate=True)
        admin = _agent('hermes-admin')
        with acting_as(admin):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'light', 'service': 'turn_on'}, reason='admin turn on'))
        assert out['status'] == 'pending'
        assert get_pending_call(out['id'])['agent'] == 'hermes-admin'

    def test_household_ungated_never_creates_pending(self, mock_upstream):
        _ha(mock_upstream, gate_mode='all')
        agent = _household()
        with acting_as(agent):
            out = json.loads(run_tool('Home Assistant', 'call_service', {
                'domain': 'light', 'service': 'turn_on'}, reason='household'))
        assert out.get('status') != 'pending'
        assert get_pending_calls() == []


class TestApprovalOwnership:
    def test_check_approval_scoped_to_creator(self):
        alice = _agent('alice')
        bob = _agent('bob')
        call_id = create_pending_call('ha', 'call_service', {'domain': 'light'},
                                      'why', agent='alice')
        check = check_approval_fn()
        with acting_as(bob):
            assert 'not found' in check(call_id)
        with acting_as(alice):
            assert 'pending' in check(call_id)


class TestAttributionAndRevocation:
    def test_approved_call_keeps_caller_and_records_decider(self, mock_upstream, auth_client):
        _ha(mock_upstream, gate_mode='all')
        _agent('household')
        call_id = create_pending_call(
            'Home Assistant', 'call_service',
            {'domain': 'light', 'service': 'turn_on'}, 'turn on', agent='household')
        r = auth_client.post(f'/api/integration-calls/{call_id}/approve')
        assert r.status_code == 200
        rows = get_integration_calls(limit=1)['rows']
        assert rows[0]['agent'] == 'household'
        assert rows[0]['decided_by'] == 'operator'
        assert rows[0]['approval'] == 'approved'

    def test_revoked_agent_request_is_rejected(self, auth_client):
        raw, agent_id = create_agent_token('gone')
        assert auth_client.post(f'/api/agents/{agent_id}/revoke').status_code == 200
        r = auth_client.get('/mcp', headers={'Authorization': 'Bearer ' + raw})
        assert r.status_code == 401


class TestRateLimit:
    def test_call_tool_returns_explicit_rate_limited(self):
        agent = _agent('limited', tools_mode='all', rate_limit_per_min=1)
        with acting_as(agent):
            asyncio.run(eshu_mcp.call_tool('check_approval', {'call_id': 999}))
            with pytest.raises(ToolError) as exc:
                asyncio.run(eshu_mcp.call_tool('check_approval', {'call_id': 999}))
        payload = json.loads(str(exc.value))
        assert payload['error'] == 'rate_limited'
        assert payload['retry_after'] >= 1

    def test_list_tools_is_not_rate_limited(self):
        agent = _agent('limited-list', tools_mode='all', rate_limit_per_min=1)
        with acting_as(agent):
            asyncio.run(eshu_mcp.list_tools())
            asyncio.run(eshu_mcp.list_tools())
