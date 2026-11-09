"""Chunk F10 — HA Supervisor actions, approval-gated.

Same shape as F9: `core/ha_supervisor.py` is the allowlist, and these tests
prove every action sends exactly its fixed endpoint (or WS command), that an
argument cannot move it, that nothing reaches a FORBIDDEN endpoint, and that
every call lands in the approval queue instead of running.
"""

import json
import os

import pytest

from core.ha_seed import HA_SEED_TOOLS, seed_ha_tools
from core.ha_supervisor import (
    FORBIDDEN, SUPERVISOR_ACTIONS, SUPERVISOR_READS, WS_ACTIONS, endpoint_for,
    is_supervisor_rest_path, run_supervisor_action,
)
from core.integration_proxy import ProxyError
from core.tool_runner import run_tool
from db.audit import get_audit_log
from db.gateways import clear_trigger_freeze, set_trigger_freeze
from db.integrations import (
    create_integration, create_pending_call, get_integration,
    get_pending_call, get_pending_calls, get_tools,
)
from db.misc import set_meta_json

DASHBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'dashboard')
HA_PREFIX = 'hassio'


@pytest.fixture
def ha(mock_upstream):
    from core.generic_tools import seed_generic_tools
    create_integration('ha', mock_upstream['base_url'], 'bearer', 'tok', kind='ha')
    integration = get_integration('ha')
    seed_ha_tools(integration['id'])
    seed_generic_tools(integration['id'], 'ha')
    return integration


def _patch_ws(monkeypatch, result=None, error=None):
    calls = []

    def fake(integration, command, payload=None, **kwargs):
        calls.append({'command': command, 'payload': payload,
                      'tool': kwargs.get('tool_name')})
        if error is not None:
            raise error
        return result if result is not None else {'result': 'ok'}

    monkeypatch.setattr('core.ha_supervisor.ha_ws_exec', fake)
    return calls


def err_code(out):
    """Tools return either {'error': 'code', …} or {'error': {'code': …}}."""
    err = out.get('error')
    if isinstance(err, dict):
        return err.get('code')
    return err


class TestEndpointTable:
    def test_actions_are_a_separate_table(self):
        assert SUPERVISOR_ACTIONS
        assert not (set(SUPERVISOR_ACTIONS) & set(SUPERVISOR_READS))

    def test_addon_action_builds_its_endpoint(self):
        assert endpoint_for('ha_addon_action',
                            {'slug': 'core_mosquitto',
                             'action': 'restart'}) == \
            ('/addons/core_mosquitto/restart', 'post')

    @pytest.mark.parametrize('action', ['install', 'uninstall', 'options', ''])
    def test_unknown_action_is_refused(self, action):
        with pytest.raises(ValueError):
            endpoint_for('ha_addon_action', {'slug': 'core_mosquitto',
                                             'action': action})

    @pytest.mark.parametrize('slug', ['../core', 'core_mosquitto/../x',
                                      'Core', '', 'a' * 80])
    def test_bad_slug_is_refused(self, slug):
        with pytest.raises(ValueError):
            endpoint_for('ha_addon_action', {'slug': slug, 'action': 'start'})

    def test_forbidden_endpoints_are_unreachable(self):
        """No tool, read or action, may send a FORBIDDEN prefix."""
        endpoints = []
        for template, method, _shape in SUPERVISOR_ACTIONS.values():
            if method != 'ws':
                endpoints.append(template)
        for template, method, _shape in SUPERVISOR_READS.values():
            if method != 'ws':
                endpoints.append(template)
        endpoints.extend(WS_ACTIONS.values())
        for endpoint in endpoints:
            for prefix in FORBIDDEN:
                if endpoint.startswith('/backups/'):
                    # Creating a backup is allowed; every other /backups path
                    # (restore, delete, download) is not.
                    assert endpoint in ('/backups/new/full', '/backups/new/partial')
                    continue
                if endpoint.startswith('/addons/'):
                    assert endpoint.startswith('/addons/{slug}/')
                    assert not endpoint.startswith('/addons/{slug}/options')
                    assert not endpoint.startswith('/addons/{slug}/install')
                    assert not endpoint.startswith('/addons/{slug}/uninstall')
                    continue
                assert not endpoint.startswith(prefix), (endpoint, prefix)


class TestActionPayloads:
    def test_addon_action_sends_supervisor_api(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_addon_action',
                              {'slug': 'core_mosquitto', 'action': 'restart'}, {})
        assert calls[0]['command'] == 'supervisor/api'
        assert calls[0]['payload'] == {'endpoint': '/addons/core_mosquitto/restart',
                                       'method': 'post', 'data': {},
                                       'timeout': 120}

    def test_addon_update_sends_the_ws_command(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_addon_update',
                              {'slug': 'core_mosquitto', 'backup': False}, {})
        assert calls[0]['command'] == 'hassio/update/addon'
        assert calls[0]['payload'] == {'addon': 'core_mosquitto', 'backup': False}

    def test_core_update_converts_backup(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_core_update', {'backup': True}, {})
        assert calls[0]['command'] == 'hassio/update/core'
        assert calls[0]['payload'] == {'backup': True}

    def test_full_backup(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_backup_create', {'name': 'before 1.2'}, {})
        assert calls[0]['payload']['endpoint'] == '/backups/new/full'
        assert calls[0]['payload']['data'] == {'name': 'before 1.2'}

    def test_partial_backup(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_backup_create',
                              {'name': 'partial', 'partial_folders': ['ssl'],
                               'partial_addons': ['core_mosquitto']}, {})
        assert calls[0]['payload']['endpoint'] == '/backups/new/partial'
        assert calls[0]['payload']['data'] == {'name': 'partial',
                                               'addons': ['core_mosquitto'],
                                               'folders': ['ssl']}

    @pytest.mark.parametrize('name', ['../x', '', 'a/b'])
    def test_bad_backup_name_is_refused(self, ha, monkeypatch, name):
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_supervisor_action(ha, 'ha_backup_create',
                                               {'name': name}, {}))
        assert err_code(out) == 'ha_supervisor_bad_argument'
        assert calls == []

    def test_core_restart(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        set_meta_json('ha_check_config_ha', {'result': 'valid',
                                             'at': __import__('time').time()})
        run_supervisor_action(ha, 'ha_core_restart', {}, {})
        assert calls[0]['payload']['endpoint'] == '/core/restart'

    def test_host_reboot(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        run_supervisor_action(ha, 'ha_host_reboot', {}, {})
        assert calls[0]['payload']['endpoint'] == '/host/reboot'

    def test_proxy_error_is_translated(self, ha, monkeypatch):
        _patch_ws(monkeypatch, error=ProxyError(502, 'upstream down'))
        out = json.loads(run_supervisor_action(ha, 'ha_core_restart', {}, {}))
        assert err_code(out) == 'ha_supervisor_action_failed'


class TestApprovalGate:
    def test_every_action_is_seeded_gated(self, ha):
        tools = {t['name']: t for t in get_tools(ha['id'])}
        for name in ('ha_addon_action', 'ha_addon_update', 'ha_core_update',
                     'ha_backup_create', 'ha_core_restart', 'ha_host_reboot'):
            tool = tools[name]
            assert tool['read_only'] == 0
            assert tool['always_gate'] == 1
            assert tool['handler'] == 'ha_supervisor_action'

    def test_host_reboot_is_seeded_disabled(self, ha):
        tools = {t['name']: t for t in get_tools(ha['id'])}
        assert tools['ha_host_reboot']['enabled'] == 0
        assert tools['ha_addon_action']['enabled'] == 1

    def test_a_call_queues_an_approval_and_does_not_run(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  {'slug': 'core_mosquitto',
                                   'action': 'restart'}))
        assert out['status'] == 'pending'
        assert calls == []
        pending = get_pending_calls()
        assert [c['tool'] for c in pending] == ['ha_addon_action']

    def test_freeze_refuses_before_queueing(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        set_trigger_freeze()
        try:
            out = json.loads(run_tool('ha', 'ha_core_restart', {}))
        finally:
            clear_trigger_freeze()
        assert err_code(out) == 'frozen'
        assert calls == []
        assert get_pending_calls() == []

    def test_restart_without_a_recent_valid_check_is_refused(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_core_restart', {}))
        assert err_code(out) == 'ha_check_config_stale'
        assert calls == []
        assert get_pending_calls() == []

    def test_restart_after_a_valid_check_is_queued_with_a_summary(
            self, ha, monkeypatch):
        _patch_ws(monkeypatch)
        set_meta_json('ha_check_config_ha',
                      {'result': 'valid', 'at': __import__('time').time()})
        out = json.loads(run_tool('ha', 'ha_core_restart', {}))
        assert out['status'] == 'pending'
        pending = get_pending_calls()
        assert 'core_restart' in pending[0]['summary']


class TestPreflightSummary:
    def test_addon_update_summary_has_current_and_target(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result={'name': 'Mosquitto', 'version': '6.5.1',
                                       'version_latest': '6.6.0'})
        out = json.loads(run_tool('ha', 'ha_addon_update',
                                  {'slug': 'core_mosquitto'}))
        assert out['status'] == 'pending'
        summary = get_pending_calls()[0]['summary']
        assert 'core_mosquitto' in summary and '6.5.1 -> 6.6.0' in summary

    def test_an_up_to_date_addon_is_not_queued(self, ha, monkeypatch):
        _patch_ws(monkeypatch, result={'name': 'Mosquitto', 'version': '6.6.0',
                                       'version_latest': '6.6.0'})
        out = json.loads(run_tool('ha', 'ha_addon_update',
                                  {'slug': 'core_mosquitto'}))
        assert err_code(out) == 'ha_update_not_available'
        assert get_pending_calls() == []

    def test_bad_slug_never_reaches_the_queue(self, ha, monkeypatch):
        _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  {'slug': '../etc', 'action': 'start'}))
        assert err_code(out) == 'ha_supervisor_bad_argument'
        assert get_pending_calls() == []


class TestCallServiceLoophole:
    def test_hassio_domain_is_refused_by_default(self, ha, monkeypatch):
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'call_service',
                                  {'domain': 'hassio', 'service': 'host_reboot'}))
        assert err_code(out) == 'hassio_domain_refused'
        assert out['status_code'] == 403
        assert calls == []
        assert any(e['event_type'] == 'hassio_domain_refused'
                   for e in get_audit_log())

    def test_an_explicit_policy_allows_it(self, ha, monkeypatch):
        from test_agent_policy import _agent  # reuse the policy fixture shape
        agent = _agent('operator')
        agent['policy']['arg_policy'] = {
            'ha_call_service': {'domain_allow': ['hassio']}}
        from core.agent_context import set_current_agent
        set_current_agent(agent)
        try:
            out = run_tool('ha', 'call_service',
                           {'domain': 'hassio', 'service': 'host_reboot'})
        finally:
            set_current_agent(None)
        # It is queued for approval (never auto-run), not refused by this rule.
        data = json.loads(out)
        assert err_code(data) != 'hassio_domain_refused'

    def test_other_domains_are_untouched(self, ha, monkeypatch):
        out = run_tool('ha', 'call_service',
                       {'domain': 'light', 'service': 'turn_on',
                        'data': {'entity_id': 'light.x'}})
        data = json.loads(out)
        assert err_code(data) != 'hassio_domain_refused'


class TestMcpPlanItems:
    def _plan(self, session_id, args):
        from db.session_plans import args_hash, create_plan
        return create_plan(session_id, [{'kind': 'mcp', 'integration': 'ha',
                                         'tool': 'ha_addon_action',
                                         'args': args, 'max_uses': 1}]), \
            args_hash(args)

    def _owned(self, session_id='sess-1', agent='agent-a'):
        """M2: a plan item belongs to a session with an owner."""
        from core.agent_context import set_current_agent
        from db.sessions import upsert_session
        upsert_session(session_id, agent=agent)
        set_current_agent({'id': 1, 'name': agent} if agent else None)

    def test_an_mcp_item_carries_a_hash_not_the_args(self, temp_db):
        from db.session_plans import args_hash, create_plan, get_plan
        args = {'slug': 'core_mosquitto', 'action': 'restart'}
        created, digest = self._plan('sess-1', args)
        plan = get_plan(created['plan_id'])
        item = plan['items'][0]
        assert item['kind'] == 'mcp'
        assert item['args_hash'] == digest
        assert 'core_mosquitto' not in item['command']
        assert args_hash(args) == digest

    def test_an_approved_plan_runs_exactly_one_matching_call(self, ha, monkeypatch):
        from db.session_plans import approve_plan
        self._owned()
        args = {'slug': 'core_mosquitto', 'action': 'restart'}
        created, _digest = self._plan('sess-1', args)
        approve_plan(created['plan_id'], ttl_minutes=30)
        calls = _patch_ws(monkeypatch)
        payload = dict(args, session_id='sess-1')
        out = json.loads(run_tool('ha', 'ha_addon_action', payload,
                                  reason='plan run'))
        assert out.get('status') != 'pending'
        assert calls and calls[0]['payload']['endpoint'] == \
            '/addons/core_mosquitto/restart'
        # One use only: the same call is queued again next time.
        payload = dict(args, session_id='sess-1')
        out = json.loads(run_tool('ha', 'ha_addon_action', payload))
        assert out['status'] == 'pending'

    def test_a_different_argument_hash_is_not_covered(self, ha, monkeypatch):
        from db.session_plans import approve_plan
        self._owned()
        created, _ = self._plan('sess-1', {'slug': 'core_mosquitto',
                                           'action': 'restart'})
        approve_plan(created['plan_id'], ttl_minutes=30)
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  {'slug': 'core_mosquitto', 'action': 'stop'}))
        assert out['status'] == 'pending'
        assert calls == []

    def test_an_ssh_item_is_not_claimable_by_mcp(self, ha):
        from db.session_plans import claim_mcp_plan_item, create_plan
        create_plan('sess-2', [{'host': '*', 'command': 'uptime'}])
        assert claim_mcp_plan_item('sess-2', 'ha', 'ha_addon_action',
                                   '0' * 64) is None

    def test_another_agents_session_is_not_claimable(self, ha, monkeypatch):
        """M2: an MCP caller can name any session id, so the owner must match."""
        from db.session_plans import approve_plan
        args = {'slug': 'core_mosquitto', 'action': 'restart'}
        self._owned('sess-1', 'agent-a')
        created, _ = self._plan('sess-1', args)
        approve_plan(created['plan_id'], ttl_minutes=30)
        from core.agent_context import set_current_agent
        set_current_agent({'id': 2, 'name': 'agent-b'})   # B reaches for A's
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  dict(args, session_id='sess-1')))
        assert out['status'] == 'pending'
        assert calls == []
        from db.integrations import get_pending_calls
        assert len(get_pending_calls()) == 1     # queued for the operator
        # A still holds the unused item and can spend it exactly once.
        set_current_agent({'id': 1, 'name': 'agent-a'})
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  dict(args, session_id='sess-1')))
        assert out.get('status') != 'pending'
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  dict(args, session_id='sess-1')))
        assert out['status'] == 'pending'

    def test_an_ownerless_session_is_not_claimable(self, ha, monkeypatch):
        """M2/F3 SR2: fail closed when there is no owner to match."""
        from db.session_plans import approve_plan
        args = {'slug': 'core_mosquitto', 'action': 'restart'}
        self._owned('sess-1', '')            # a row with no owner
        created, _ = self._plan('sess-1', args)
        approve_plan(created['plan_id'], ttl_minutes=30)
        calls = _patch_ws(monkeypatch)
        out = json.loads(run_tool('ha', 'ha_addon_action',
                                  dict(args, session_id='sess-1')))
        assert out['status'] == 'pending'
        assert calls == []

    def test_host_reboot_is_not_plan_coverable(self, temp_db):
        """S1: a reboot always gets its own approval card."""
        from db.session_plans import PlanError, create_plan
        with pytest.raises(PlanError) as exc:
            create_plan('sess-1', [{'kind': 'mcp', 'integration': 'ha',
                                    'tool': 'ha_host_reboot',
                                    'args': {}, 'max_uses': 1}])
        assert exc.value.code == 'not_plan_coverable'


class TestSupervisorRestGuard:
    """M1: the generic REST tools cannot reach the Supervisor either.

    HA Core proxies `/api/hassio/…` to the Supervisor (one catch-all view in
    `homeassistant/components/hassio/http.py`) and `/api/services/hassio/…`
    runs its services. The curated table cannot see a path the agent chose,
    so the guard runs before gating and again in the executors.
    """

    SPELLINGS = (
        '/api/hassio/backups',
        'api/hassio/backups',
        '//API//HASSIO//backups',
        '/api/%68assio/backups',
        '/api/hassio',
        '/api/services/hassio/host/reboot',
        '/API/Services/HASSIO/host/reboot',
        '/api/services/%68assio/restart',
    )

    @pytest.mark.parametrize('path', SPELLINGS)
    def test_the_generic_read_tool_refuses_before_any_request(
            self, ha, mock_upstream, path):
        out = json.loads(run_tool('ha', 'read', {'path': path}))
        assert err_code(out) == 'supervisor_rest_refused'
        assert out['status_code'] == 403
        assert mock_upstream['requests'] == []
        assert get_pending_calls() == []

    @pytest.mark.parametrize('path', SPELLINGS)
    def test_the_generic_write_tool_refuses_before_any_request(
            self, ha, mock_upstream, path):
        out = json.loads(run_tool(
            'ha', 'write',
            {'method': 'POST', 'path': path, 'data': {'zip': 'x'}}))
        assert err_code(out) == 'supervisor_rest_refused'
        assert mock_upstream['requests'] == []
        assert get_pending_calls() == []

    def test_an_approved_rest_call_is_refused_and_runs_nothing(
            self, ha, mock_upstream, auth_client):
        call_id = create_pending_call(
            'ha', 'write',
            {'method': 'POST', 'path': '/api/hassio/backups',
             'data': {'zip': 'x'}},
            'test', agent='operator')
        response = auth_client.post(f'/api/integration-calls/{call_id}/approve')
        assert response.status_code == 200
        stored = json.loads(get_pending_call(call_id)['result'])
        assert stored['status_code'] == 403
        assert 'Supervisor' in stored['error']
        assert mock_upstream['requests'] == []

    def test_every_forbidden_prefix_has_a_rest_spelling(self):
        for prefix in FORBIDDEN:
            assert is_supervisor_rest_path('/api/hassio' + prefix), prefix
            assert is_supervisor_rest_path(
                '/api/services/hassio' + prefix.rstrip('/') + '/x'), prefix

    def test_no_curated_ha_tool_declares_a_supervisor_path(self, ha):
        """Walk the whole registry: the generic pair is covered by the guard,
        the curated templates must never start with a Supervisor path."""
        names = {tool['name'] for tool in get_tools(ha['id'])}
        assert {'read', 'write'} <= names
        for tool in get_tools(ha['id']):
            template = tool.get('path_template') or ''
            assert not is_supervisor_rest_path(template), tool['name']

    def test_a_refusal_is_audited_without_the_path_values(self, ha):
        run_tool('ha', 'write',
                 {'method': 'POST', 'path': '/api/hassio/backups',
                  'data': {'zip': 'x'}})
        events = [e for e in get_audit_log()
                  if e['event_type'] == 'rest_supervisor_refused']
        assert events
        assert 'hassio/backups' in events[0]['details']
