"""E2E-1 round 1: the scoped approver token, BY1–BY4, CA4 and CA5.

Synthetic data only. The approver is exercised through the real FastAPI app
with its bearer token; no network and no real gateway.
"""
import json
import time

import pytest

from db.agent_tokens import create_agent_token, get_agent
from db.agent_policies import get_agent_policy, set_agent_policy
from db.approver_tokens import (
    create_approver_token, get_approver_token, list_approver_tokens,
)
from db.gateways import register_gateway, set_gateway_installer_hash
from db.integrations import (
    create_integration, create_pending_call, create_tool, get_integration,
    get_pending_call, record_integration_call,
)
from db.requests import create_request, get_request_row, get_session_requests
from db.session_plans import create_plan, get_plan
from db.sessions import get_session, get_session_summary, upsert_session
from db.windows import create_window_request, get_window_request

GATEWAY = '192.0.2.10'
OTHER_GATEWAY = '192.0.2.11'
COMMANDS = ['hostname', 'id -un', 'printf e2e-ok', 'cat /etc/hostname']


def _ha():
    create_integration('Home Assistant', 'http://192.0.2.30:8123', 'none', '',
                       kind='ha')
    ha = get_integration('Home Assistant')
    create_tool(ha['id'], 'list_entities', 'List', 'GET', '/states', [], '',
                read_only=True)
    create_tool(ha['id'], 'call_service', 'Call', 'POST', '/services', [], '',
                read_only=False)
    return ha


def _test_agent(name='e2e-agent', test_agent=True, hosts=None,
                tools=None, approval=None):
    raw, aid = create_agent_token(name)
    set_agent_policy(
        aid, tools_mode='allowlist',
        tools=tools or ['home_assistant_list_entities',
                        'home_assistant_call_service'],
        approval=approval or {'home_assistant_call_service': 'inherit'},
        ssh_hosts_allow=hosts or [GATEWAY], ssh_mode='normal',
        test_agent=test_agent)
    return raw, aid


@pytest.fixture
def setup():
    register_gateway(GATEWAY, 'web01.example.test', 'v6.0.0')
    register_gateway(OTHER_GATEWAY, 'web02.example.test', 'v6.0.0')
    _ha()
    agent_raw, aid = _test_agent()
    approver_raw, token_id = create_approver_token(aid, COMMANDS, GATEWAY)
    upsert_session('e2e-session', agent='e2e-agent', agent_id=aid,
                   goal='E2E: smoke')
    return {
        'agent_raw': agent_raw, 'agent_id': aid,
        'approver_raw': approver_raw, 'token_id': token_id,
        'session': 'e2e-session',
        'headers': {'Authorization': 'Bearer ' + approver_raw},
    }


def _row(agent_id, session='e2e-session', command='hostname',
         target=GATEWAY, status='pending'):
    return create_request(target, command, status=status, session_id=session,
                          agent='e2e-agent', agent_id=agent_id)


def _plan(agent_id, session='e2e-session', command='hostname', origin='agent',
          host=GATEWAY):
    return create_plan(session, [{'host': host, 'command': command,
                                  'max_uses': 1}], origin=origin,
                       agent_id=agent_id)


class TestApproverToken:
    def test_token_is_hashed_shown_once_and_listed_without_it(self, setup, auth_client):
        rows = auth_client.get('/api/approver-tokens' +
                               '?agent_id=%d' % setup['agent_id']).json()
        assert rows and 'token' not in rows[0]
        assert rows[0]['id'] == setup['token_id']
        stored = get_approver_token(setup['token_id'])
        assert stored['token_hash'] != setup['approver_raw']
        assert stored['commands'] == COMMANDS

    def test_invalid_and_expired_tokens_are_401(self, client):
        r = client.get('/api/requests', headers={'Authorization': 'Bearer e2e_nope'})
        assert r.status_code == 401

    def test_expired_token_is_refused(self, setup, client):
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute('UPDATE approver_tokens SET expires_at = ? WHERE id = ?',
                         (int(time.time()) - 1, setup['token_id']))
            conn.commit()
        r = client.get('/api/requests', headers=setup['headers'])
        assert r.status_code == 401

    def test_cookie_never_carries_approver_powers(self, setup, client):
        # No bearer: the route is session-only and no session cookie is sent.
        r = client.get('/api/requests')
        assert r.status_code == 401


class TestBy1:
    def test_own_request_approves_and_other_or_zero_is_refused(self, setup, client):
        own = _row(setup['agent_id'])
        r = client.post('/api/approve/%d' % own, headers=setup['headers'])
        assert r.status_code == 200, r.text
        assert get_request_row(own)['status'] == 'approved'

        zero = _row(0)
        r = client.post('/api/approve/%d' % zero, headers=setup['headers'])
        assert r.status_code == 403
        assert get_request_row(zero)['status'] == 'pending'

        _other_raw, other_aid = _test_agent('other-agent')
        other = _row(other_aid)
        r = client.post('/api/approve/%d' % other, headers=setup['headers'])
        assert r.status_code == 403

    def test_session_must_belong_to_the_agent_and_start_e2e(self, setup, client,
                                                           monkeypatch):
        # Same agent id, but a session opened by another agent.
        upsert_session('foreign', agent='other', agent_id=999999,
                       goal='E2E: foreign')
        rid = _row(setup['agent_id'], session='foreign')
        r = client.post('/api/approve/%d' % rid, headers=setup['headers'])
        assert r.status_code == 403
        # Same agent and session, but the goal is not E2E:.
        upsert_session('not-e2e', agent='e2e-agent',
                       agent_id=setup['agent_id'], goal='not a suite run')
        rid2 = _row(setup['agent_id'], session='not-e2e')
        r = client.post('/api/approve/%d' % rid2, headers=setup['headers'])
        assert r.status_code == 403

    def test_approve_variant_with_a_body_is_refused(self, setup, client):
        rid = _row(setup['agent_id'])
        r = client.post('/api/approve/%d' % rid,
                        json={'learn': True}, headers=setup['headers'])
        assert r.status_code == 403
        assert get_request_row(rid)['status'] == 'pending'

    def test_deny_needs_no_command_list(self, setup, client):
        rid = _row(setup['agent_id'], command='rm -rf /tmp/x')
        r = client.post('/api/deny/%d' % rid, headers=setup['headers'])
        assert r.status_code == 200, r.text
        assert get_request_row(rid)['status'] == 'denied'

    def test_decision_is_audited_as_e2e_approver(self, setup, client):
        from db.audit import get_audit_log
        rid = _row(setup['agent_id'])
        client.post('/api/approve/%d' % rid, headers=setup['headers'])
        rows = [r for r in get_audit_log(50)
                if r['event_type'] == 'e2e_approver_decision']
        assert rows and rows[0]['actor'] == 'e2e-approver'
        assert 'token #%d' % setup['token_id'] in rows[0]['details']


class TestBy2:
    def test_command_and_gateway_are_exact(self, setup, client):
        bad_cmd = _row(setup['agent_id'], command='hostname -f')
        r = client.post('/api/approve/%d' % bad_cmd, headers=setup['headers'])
        assert r.status_code == 403
        bad_gw = _row(setup['agent_id'], target=OTHER_GATEWAY)
        r = client.post('/api/approve/%d' % bad_gw, headers=setup['headers'])
        assert r.status_code == 403
        good = _row(setup['agent_id'], command='id -un')
        r = client.post('/api/approve/%d' % good, headers=setup['headers'])
        assert r.status_code == 200, r.text

    def test_plan_approve_variants_and_command_list(self, setup, client):
        plan_id = _plan(setup['agent_id'])['plan_id']
        r = client.post('/api/session-plans/%d/approve' % plan_id,
                        json={'ttl_minutes': 60, 'removed_item_ids': []},
                        headers=setup['headers'])
        assert r.status_code == 200, r.text
        assert get_plan(plan_id)['status'] == 'approved'

        p2 = _plan(setup['agent_id'])['plan_id']
        r = client.post('/api/session-plans/%d/approve' % p2,
                        json={'ttl_minutes': 60, 'removed_item_ids': [1]},
                        headers=setup['headers'])
        assert r.status_code == 403
        p3 = _plan(setup['agent_id'])['plan_id']
        r = client.post('/api/session-plans/%d/approve' % p3,
                        json={'ttl_minutes': 120, 'removed_item_ids': []},
                        headers=setup['headers'])
        assert r.status_code == 403
        p4 = _plan(setup['agent_id'], command='id -un')['plan_id']
        r = client.post('/api/session-plans/%d/approve' % p4,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 200, r.text
        p5 = _plan(setup['agent_id'], command='whoami')['plan_id']
        r = client.post('/api/session-plans/%d/approve' % p5,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 403
        p6 = _plan(setup['agent_id'], command='hostname',
                   host=OTHER_GATEWAY)['plan_id']
        r = client.post('/api/session-plans/%d/approve' % p6,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 403

    def test_only_the_two_probe_templates(self, setup, client):
        from db.misc import set_meta_json
        p1 = _plan(setup['agent_id'], origin='probe')['plan_id']
        set_meta_json('probe_plan_%d' % p1,
                      {'probe': 'diagnose_service',
                       'params': {'host': GATEWAY, 'unit': 'ssh'}})
        r = client.post('/api/session-plans/%d/approve' % p1,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 200, r.text
        p2 = _plan(setup['agent_id'], origin='probe')['plan_id']
        set_meta_json('probe_plan_%d' % p2,
                      {'probe': 'diagnose_service',
                       'params': {'host': GATEWAY, 'unit': 'nginx'}})
        r = client.post('/api/session-plans/%d/approve' % p2,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 403
        p3 = _plan(setup['agent_id'], origin='probe')['plan_id']
        set_meta_json('probe_plan_%d' % p3,
                      {'probe': 'disk_pressure', 'params': {'host': GATEWAY}})
        r = client.post('/api/session-plans/%d/approve' % p3,
                        json={'ttl_minutes': 60}, headers=setup['headers'])
        assert r.status_code == 200, r.text


class TestIntegrationCalls:
    def test_approve_is_refused_deny_is_allowed(self, setup, client):
        call_id = create_pending_call('Home Assistant', 'call_service',
                                      {'domain': 'light'}, session_id='e2e-session',
                                      agent='e2e-agent', agent_id=setup['agent_id'])
        r = client.post('/api/integration-calls/%d/approve' % call_id,
                        headers=setup['headers'])
        assert r.status_code == 403
        assert get_pending_call(call_id)['status'] == 'pending'
        r = client.post('/api/integration-calls/%d/deny' % call_id,
                        headers=setup['headers'])
        assert r.status_code == 200, r.text
        assert get_pending_call(call_id)['status'] == 'denied'


class TestWindows:
    def _window(self, setup, agent_id=None, command='hostname',
                target=GATEWAY, match_type='exact', max_executions=1,
                expires_in=600):
        now = int(time.time())
        return create_window_request(
            target, command, expires_at=now + expires_in,
            match_type=match_type, max_executions=max_executions,
            label='E2E: smoke', window_start=now, session_id='e2e-session',
            agent_id=agent_id if agent_id is not None else setup['agent_id'])

    def test_only_a_narrow_pending_window_is_approved(self, setup, client):
        good = self._window(setup)
        r = client.post('/api/window-requests/%d/approve' % good['id'],
                        headers=setup['headers'])
        assert r.status_code == 200, r.text
        bad_max = self._window(setup, max_executions=2)
        r = client.post('/api/window-requests/%d/approve' % bad_max['id'],
                        headers=setup['headers'])
        assert r.status_code == 403
        bad_exp = self._window(setup, expires_in=901)
        r = client.post('/api/window-requests/%d/approve' % bad_exp['id'],
                        headers=setup['headers'])
        assert r.status_code == 403
        bad_match = self._window(setup, match_type='prefix')
        r = client.post('/api/window-requests/%d/approve' % bad_match['id'],
                        headers=setup['headers'])
        assert r.status_code == 403
        bad_cmd = self._window(setup, command='whoami')
        r = client.post('/api/window-requests/%d/approve' % bad_cmd['id'],
                        headers=setup['headers'])
        assert r.status_code == 403
        foreign = create_window_request(
            GATEWAY, 'hostname', expires_at=int(time.time()) + 600,
            match_type='exact', max_executions=1, session_id='e2e-session',
            agent_id=999999)
        r = client.post('/api/window-requests/%d/approve' % foreign['id'],
                        headers=setup['headers'])
        assert r.status_code == 403
        bad_gw = self._window(setup, target=OTHER_GATEWAY)
        r = client.post('/api/window-requests/%d/approve' % bad_gw['id'],
                        headers=setup['headers'])
        assert r.status_code == 403

    def test_window_create_edit_toggle_are_refused(self, setup, client):
        for method, path in (
                ('post', '/api/approved-windows'),
                ('put', '/api/approved-windows/1'),
                ('post', '/api/approved-windows/1/toggle')):
            r = getattr(client, method)(path, json={}, headers=setup['headers'])
            assert r.status_code == 403, (method, path, r.text)

    def test_agent_window_request_records_attribution(self, setup, client):
        now = int(time.time())
        r = client.post('/api/window-requests', headers={
            'Authorization': 'Bearer ' + setup['agent_raw']}, json={
                'gateway_ip': GATEWAY, 'command': 'hostname',
                'window_start': now - 5, 'expires_at': now + 600,
                'max_executions': 1, 'session_id': 'e2e-session'})
        assert r.status_code == 200, r.text
        row = get_window_request(r.json()['id'])
        assert row['agent_id'] == setup['agent_id']
        assert row['session_id'] == 'e2e-session'


class TestCleanup:
    def test_exclude_reject_and_delete_are_own_rows_only(self, setup, client,
                                                         auth_client):
        rid = _row(setup['agent_id'])
        r = client.post('/api/requests/exclude',
                        json={'session_id': 'e2e-session'},
                        headers=setup['headers'])
        assert r.status_code == 200, r.text
        assert r.json()['excluded'] >= 1
        other_sid = 'other-session'
        upsert_session(other_sid, agent='other', agent_id=999999,
                       goal='E2E: other')
        r = client.post('/api/requests/exclude',
                        json={'session_id': other_sid},
                        headers=setup['headers'])
        assert r.status_code == 403

        from db.conclusions import upsert_draft
        draft = upsert_draft('e2e-session', 'e2e-agent', 'E2E: smoke',
                             {'summary': 'x'})
        r = client.post('/api/conclusions/%d/reject' % draft['id'],
                        json={'note': 'e2e cleanup'}, headers=setup['headers'])
        assert r.status_code == 200, r.text
        other_draft = upsert_draft(other_sid, 'other', 'E2E: other',
                                   {'summary': 'x'})
        r = client.post('/api/conclusions/%d/reject' % other_draft['id'],
                        json={'note': 'x'}, headers=setup['headers'])
        assert r.status_code == 403

        window = create_window_request(GATEWAY, 'hostname',
                                       expires_at=int(time.time()) + 600,
                                       max_executions=1,
                                       session_id='e2e-session',
                                       agent_id=setup['agent_id'])
        r = client.delete('/api/approved-windows/%d' % window['id'],
                          headers=setup['headers'])
        assert r.status_code == 200, r.text
        foreign = create_window_request(GATEWAY, 'hostname',
                                        expires_at=int(time.time()) + 600,
                                        max_executions=1, session_id=other_sid,
                                        agent_id=999999)
        r = client.delete('/api/approved-windows/%d' % foreign['id'],
                          headers=setup['headers'])
        assert r.status_code == 403


class TestUiEscaping:
    def test_approver_token_row_escapes_every_field(self):
        import os
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            pytest.skip('node is not installed')
        harness = os.path.join(os.path.dirname(__file__), 'ui', 'e2e_approver.cjs')
        proc = subprocess.run([node, harness], capture_output=True, text=True,
                              timeout=20)
        assert proc.returncode == 0, proc.stderr
        assert '<img' not in proc.stdout and '<script>' not in proc.stdout
        assert '&lt;img' in proc.stdout and '&lt;script&gt;' in proc.stdout
        assert 'hostname' in proc.stdout


class TestUiSurface:
    def test_the_agent_page_has_the_switch_and_the_approver_card(self):
        import os
        root = os.path.join(os.path.dirname(__file__), '..')
        html = open(os.path.join(root, 'dashboard', 'static', 'index.html'),
                    encoding='utf-8').read()
        js = open(os.path.join(root, 'dashboard', 'static', 'app.js'),
                  encoding='utf-8').read()
        for needle in ('agent-test-toggle', 'approver-commands',
                       'approver-gateway', 'approver-token-list'):
            assert needle in html
        for needle in ('toggleTestAgent', 'mintApproverToken',
                       'revokeApproverToken', 'approverTokenRow'):
            assert needle in js
        # The row helper is the one that must escape values.
        start = js.index('function approverTokenRow')
        end = js.index('function renderApproverTokenList')
        body = js[start:end]
        assert 'esc(t.gateway_ip' in body
        assert "esc((t.commands" in body


class TestRefusals:
    @pytest.mark.parametrize('method,path', [
        ('put', '/api/policies'),
        ('post', '/api/agents'),
        ('post', '/api/agents/1/revoke'),
        ('post', '/api/gateways/192.0.2.10/mode'),
        ('put', '/api/settings/notify'),
        ('post', '/api/fleet/commands'),
        ('post', '/api/freeze'),
        ('post', '/api/notify-test'),
        ('post', '/api/dev/seed'),
        ('post', '/api/dev/promote'),
        ('post', '/api/dev/rollback'),
        ('post', '/api/policies/commit'),
        ('post', '/api/policies/restore-core'),
        ('post', '/api/session-plans/1/end'),
        ('put', '/api/allowances/ceiling'),
        ('post', '/api/allowances'),
        ('post', '/api/approved-windows'),
        ('put', '/api/approved-windows/1'),
        ('post', '/api/approved-windows/1/toggle'),
        ('post', '/api/window-requests'),
        ('post', '/api/approver-tokens'),
        ('post', '/api/approver-tokens/1/revoke'),
        ('post', '/api/conclusions/1/confirm'),
        ('post', '/api/integration-calls/1/approve'),
        ('post', '/api/request'),
    ])
    def test_routes_outside_scope_are_refused(self, setup, client, method, path):
        r = getattr(client, method)(path, json={}, headers=setup['headers'])
        assert r.status_code == 403, (method, path, r.text)

    def test_a_refused_route_is_audited(self, setup, client):
        from db.audit import get_audit_log
        client.post('/api/policies', json={}, headers=setup['headers'])
        rows = [r for r in get_audit_log(50)
                if r['event_type'] == 'e2e_approver_refused']
        assert rows and rows[0]['actor'] == 'e2e-approver'
        assert 'token #%d' % setup['token_id'] in rows[0]['details']

    def test_mcp_is_never_an_approver_route(self, setup, client):
        r = client.get('/mcp', headers=setup['headers'])
        assert r.status_code in (401, 403)


class TestTestAgent:
    def _ready_agent(self, name):
        raw, aid = create_agent_token(name)
        set_agent_policy(aid, tools_mode='allowlist',
                         tools=['home_assistant_call_service'],
                         ssh_hosts_allow=[GATEWAY], ssh_mode='normal')
        return raw, aid

    def test_flag_is_operator_only_and_narrows(self, auth_client):
        _raw, aid = self._ready_agent('e2e-new')
        r = auth_client.post('/api/agents/%d/test-agent' % aid,
                             json={'enabled': True})
        assert r.status_code == 200, r.text
        assert get_agent_policy(aid)['test_agent'] is True
        # Two IPs are refused once the flag is set.
        with pytest.raises(ValueError):
            set_agent_policy(aid, ssh_hosts_allow=[GATEWAY, OTHER_GATEWAY],
                             test_agent=True)
        # Ungated is refused.
        with pytest.raises(ValueError):
            set_agent_policy(aid, approval={'home_assistant_call_service': 'ungated'},
                             test_agent=True)
        # A non-HA tool is refused.
        with pytest.raises(ValueError):
            set_agent_policy(aid, tools=['whereis'], test_agent=True)

    def test_an_agent_with_activity_cannot_become_a_test_agent(self, auth_client):
        _raw, aid = self._ready_agent('e2e-used')
        create_request(GATEWAY, 'hostname', session_id='used-session',
                       agent='e2e-used', agent_id=aid)
        r = auth_client.post('/api/agents/%d/test-agent' % aid,
                             json={'enabled': True})
        assert r.status_code == 400

    def test_setting_the_flag_notifies(self, auth_client, monkeypatch):
        sent = []
        monkeypatch.setattr('main.send_notify', lambda *a, **k: sent.append((a, k)))
        raw, aid = create_agent_token('e2e-notify')
        set_agent_policy(aid, tools_mode='allowlist',
                         tools=['home_assistant_call_service'],
                         ssh_hosts_allow=[GATEWAY], ssh_mode='normal')
        r = auth_client.post('/api/agents/%d/test-agent' % aid,
                             json={'enabled': True})
        assert r.status_code == 200, r.text
        assert sent

    def test_clearing_revokes_tokens(self, auth_client, setup):
        r = auth_client.post('/api/agents/%d/test-agent' % setup['agent_id'],
                             json={'enabled': False})
        assert r.status_code == 200, r.text
        assert get_approver_token(setup['token_id'])['revoked'] == 1

    def test_mint_notifies_and_returns_the_token_once(self, auth_client, setup,
                                                      monkeypatch):
        sent = []
        monkeypatch.setattr('main.send_notify',
                            lambda *a, **k: sent.append((a, k)))
        r = auth_client.post('/api/approver-tokens', json={
            'agent_id': setup['agent_id'], 'commands': ['hostname'],
            'gateway_ip': GATEWAY, 'days': 30})
        assert r.status_code == 200, r.text
        assert r.json()['token'].startswith('e2e_')
        assert sent
        listed = auth_client.get('/api/approver-tokens?agent_id=%d'
                                 % setup['agent_id']).json()
        assert all('token' not in row for row in listed)


class TestCa4Ca5C16:
    def test_ca5_excluded_calls_leave_statistics(self, setup):
        record_integration_call('Home Assistant', 'list_entities', 'e2e-agent',
                                'GET', '/states', 200, 1, 'ok', 1, 0,
                                session_id='e2e-session',
                                agent_id=setup['agent_id'])
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("UPDATE integration_calls SET excluded = 1 "
                         "WHERE session_id = 'e2e-session'")
            conn.commit()
        assert get_session_summary('e2e-session')['mcp_calls'] == 0

    def test_c16_is_one_narrow_read(self, setup, client, monkeypatch):
        set_gateway_installer_hash(GATEWAY, 'aaaaaaaaaaaa')
        monkeypatch.setattr('main.installer_channels.channel_hash',
                            lambda channel: 'aaaaaaaaaaaa')
        r = client.get('/api/e2e/installer-hash', headers=setup['headers'])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['gateway_ip'] == GATEWAY
        assert body['edge_hash'] == 'aaaaaaaaaaaa'
        assert body['matches'] is True
        assert 'hostname' not in json.dumps(body)
