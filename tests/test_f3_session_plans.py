"""Chunk F3 — session plans.

The agent proposes the exact commands it intends to run; the operator approves
the list once (a subset, with a TTL) and those exact bytes then auto-approve
for that session, up to each item's max_uses. Everything else keeps going
through normal JIT. The approval path must be atomic, session-bound and
ordered after the encoded-payload block, with Freeze and Zero-Trust winning.
"""

import base64
import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

import pytest

from db.audit import get_audit_log
from db.gateways import (
    clear_trigger_freeze, register_gateway, set_gateway_token,
    set_gateway_zero_trust, set_trigger_freeze,
)
from db.misc import set_feature_flag, set_feature_flag_scope
from db.requests import get_all_requests
from db.session_plans import (
    approve_plan, claim_plan_item, create_plan, get_plan, list_plans,
)
from db.sessions import upsert_session

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f3_harness.js')
NODE = shutil.which('node')


@contextmanager
def as_agent(name):
    """Run an MCP tool call as one agent (the ContextVar the MCP sets)."""
    from core.agent_context import reset_current_agent, set_current_agent
    token = set_current_agent({'name': name})
    try:
        yield
    finally:
        reset_current_agent(token)


def run_node(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


def b64(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def gateways(auth_client):
    """Two enrolled gateways, one host token each (the token is IP-bound)."""
    register_gateway('10.0.0.1', 'web01', 'v15.3')
    set_gateway_token('10.0.0.1', 'tok-web01')
    register_gateway('10.0.0.2', 'web02', 'v15.3')
    set_gateway_token('10.0.0.2', 'tok-web02')
    return auth_client, {'X-Gateway-Token': 'tok-web01'}, {'X-Gateway-Token': 'tok-web02'}


def _mint(session_id, goal='task'):
    upsert_session(session_id, agent='test-agent', goal=goal, source='agent')
    return session_id


def _plan(session_id, items, note=''):
    _mint(session_id)
    return create_plan(session_id, items, note)['plan_id']


def _request(client, headers, command, session_id='', ip='10.0.0.1'):
    body = {'target_ip': ip, 'encoded_command': b64(command)}
    if session_id:
        body['session_id'] = session_id
    response = client.post('/api/request', json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _row_for(command):
    rows = [r for r in get_all_requests() if r['command'] == command]
    assert rows, 'no request row for %r' % command
    return rows[0]


def _audit_events(kind):
    return [event for event in get_audit_log() if event['event_type'] == kind]


class TestApproval:

    def test_f3_plan_approves_once_then_pends(self, gateways):
        client, web01, _web02 = gateways
        sid = 'plan-session-1'
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)

        first = _request(client, web01, 'uptime', sid)
        assert first.get('plan') is True
        row = _row_for('uptime')
        assert row['status'] == 'approved'
        assert row['reason'].startswith('session-plan:%d:' % plan_id)
        assert _audit_events('jit_plan_approved'), 'expected a jit_plan_approved audit event'

        # max_uses=1 is spent: the same command needs a normal JIT now.
        second = _request(client, web01, 'uptime', sid)
        assert 'plan' not in second
        assert _row_for('uptime')['status'] == 'pending'

    def test_f3_plan_approve_subset_removed_item_pends(self, gateways):
        client, web01, _web02 = gateways
        sid = 'plan-session-2'
        plan_id = _plan(sid, [
            {'host': '*', 'command': 'uptime', 'max_uses': 1},
            {'host': '*', 'command': 'df -h', 'max_uses': 1},
        ])
        removed = [item for item in get_plan(plan_id)['items'] if item['command'] == 'df -h']
        approve_plan(plan_id, 60, removed_item_ids=[removed[0]['id']])

        assert _request(client, web01, 'uptime', sid).get('plan') is True
        assert 'plan' not in _request(client, web01, 'df -h', sid)
        assert _row_for('df -h')['status'] == 'pending'


class TestHostAndSessionBinding:

    def test_f3_host_specific_item_does_not_match_another_host(self, gateways):
        client, web01, web02 = gateways
        sid = 'plan-session-3'
        plan_id = _plan(sid, [
            {'host': '10.0.0.1', 'command': 'uptime', 'max_uses': 1},
            {'host': '*', 'command': 'free -h', 'max_uses': 1},
        ])
        approve_plan(plan_id, 60)

        # Host-specific: only the recorded host matches.
        assert 'plan' not in _request(client, web02, 'uptime', sid, ip='10.0.0.2')
        assert _row_for('uptime')['status'] == 'pending'
        assert _request(client, web01, 'uptime', sid, ip='10.0.0.1').get('plan') is True
        # `*` matches any host.
        assert _request(client, web02, 'free -h', sid, ip='10.0.0.2').get('plan') is True

    def test_f3_plan_is_bound_to_its_session(self, gateways):
        client, web01, _web02 = gateways
        plan_id = _plan('plan-session-a', [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)

        # Another session's id never inherits the plan…
        assert 'plan' not in _request(client, web01, 'uptime', 'plan-session-b')
        # …and neither does no id at all.
        assert 'plan' not in _request(client, web01, 'uptime')
        assert _row_for('uptime')['status'] == 'pending'
        # The right session still does.
        assert _request(client, web01, 'uptime', 'plan-session-a').get('plan') is True


class TestInactivePlans:

    def test_f3_expired_denied_and_ended_plans_do_not_approve(self, gateways):
        client, web01, _web02 = gateways

        # Expired: approved, then its TTL is moved into the past.
        sid_exp = 'plan-session-expired'
        plan_exp = _plan(sid_exp, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_exp, 60)
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute('UPDATE session_plans SET until = ? WHERE id = ?',
                         (int(time.time()) - 5, plan_exp))
            conn.commit()
        assert 'plan' not in _request(client, web01, 'uptime', sid_exp)
        assert _row_for('uptime')['status'] == 'pending'

        # Denied.
        sid_den = 'plan-session-denied'
        plan_den = _plan(sid_den, [{'host': '*', 'command': 'free -h', 'max_uses': 1}])
        from db.session_plans import deny_plan
        deny_plan(plan_den)
        assert 'plan' not in _request(client, web01, 'free -h', sid_den)

        # Ended.
        sid_end = 'plan-session-ended'
        plan_end = _plan(sid_end, [{'host': '*', 'command': 'df -h', 'max_uses': 1}])
        approve_plan(plan_end, 60)
        from db.session_plans import end_plan
        end_plan(plan_end)
        assert 'plan' not in _request(client, web01, 'df -h', sid_end)


class TestSafetyWins:

    def test_f3_zero_trust_wins_over_a_plan(self, gateways):
        client, web01, _web02 = gateways
        sid = 'plan-session-zt'
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)
        set_gateway_zero_trust('10.0.0.1', True)
        try:
            assert 'plan' not in _request(client, web01, 'uptime', sid)
            assert _row_for('uptime')['status'] == 'pending'
        finally:
            set_gateway_zero_trust('10.0.0.1', False)

    def test_f3_freeze_wins_over_a_plan(self, gateways):
        client, web01, _web02 = gateways
        sid = 'plan-session-freeze'
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)
        set_trigger_freeze()
        try:
            assert 'plan' not in _request(client, web01, 'uptime', sid)
            assert _row_for('uptime')['status'] == 'pending'
        finally:
            clear_trigger_freeze()

    def test_f3_strict_sessions_refuse_an_unminted_session(self, gateways):
        client, web01, _web02 = gateways
        sid = 'plan-session-strict'
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)
        set_feature_flag('require_sessions', True)
        set_feature_flag_scope('require_sessions', 'prod')
        try:
            # An id with no minted, named session row cannot use the plan.
            assert 'plan' not in _request(client, web01, 'uptime', 'not-minted')
            # A minted, named session still can.
            assert _request(client, web01, 'uptime', sid).get('plan') is True
        finally:
            set_feature_flag('require_sessions', False)


class TestConcurrency:

    def test_f3_two_requests_for_the_last_use_approve_exactly_one(self):
        sid = _mint('plan-session-race')
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)

        results = []
        lock = threading.Lock()

        def worker():
            claim = claim_plan_item(sid, '10.0.0.1', 'uptime')
            with lock:
                results.append(claim)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        approved = [claim for claim in results if claim]
        assert len(approved) == 1, results
        assert approved[0][0] == plan_id

    def test_f3_end_between_select_and_update_consumes_no_use(self, monkeypatch):
        """R4: the UPDATE rechecks the plan's state, not just its uses."""
        import db.session_plans as sp
        sid = _mint('plan-session-end-race')
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 1}])
        approve_plan(plan_id, 60)

        real = sp._claim_candidate

        def hook(conn, session_id, host, command, now, origins=None, agent=None):
            row = real(conn, session_id, host, command, now, origins=origins,
                       agent=agent)
            # The operator ends the plan between the SELECT and the UPDATE.
            sp.end_plan(plan_id)
            return row

        monkeypatch.setattr(sp, '_claim_candidate', hook)
        assert sp.claim_plan_item(sid, '10.0.0.1', 'uptime') is None
        assert get_plan(plan_id)['items'][0]['uses'] == 0


class TestApi:

    def test_f3_api_lists_and_approves_a_subset(self, auth_client):
        sid = _mint('plan-session-api', goal='api test')
        create_plan(sid, [
            {'host': '*', 'command': 'uptime', 'max_uses': 2},
            {'host': '10.0.0.1', 'command': 'df -h', 'max_uses': 1},
        ], note='two changes')
        plan = auth_client.get('/api/session-plans?status=proposed').json()[0]
        assert plan['status'] == 'proposed'
        assert plan['note'] == 'two changes'
        assert plan['kept'] == 2 and plan['hosts'] == 2
        # The items carry the F2 breakdown for the review card.
        assert plan['items'][0]['breakdown']['summary']['steps'] >= 1
        remove = [item['id'] for item in plan['items'] if item['command'] == 'df -h']

        response = auth_client.post('/api/session-plans/%d/approve' % plan['id'],
                                    json={'ttl_minutes': 30,
                                          'removed_item_ids': remove})
        assert response.status_code == 200, response.text
        approved = response.json()
        assert approved['status'] == 'approved'
        assert approved['until'] > int(time.time())
        assert approved['kept'] == 1
        removed = [item for item in approved['items'] if item['id'] in remove][0]
        assert removed['removed'] == 1
        # Audit records who approved which items.
        events = [e for e in _audit_events('plan_approved')]
        assert events and events[0]['actor'] == 'operator'
        assert 'uptime' not in events[0]['details']  # ids/counts, not command text
        assert 'ttl 30 min' in events[0]['details']

    def test_f3_api_deny_and_end(self, auth_client):
        sid = _mint('plan-session-api2')
        plan_id = create_plan(sid, [{'host': '*', 'command': 'uptime'}])['plan_id']
        denied = auth_client.post('/api/session-plans/%d/deny' % plan_id).json()
        assert denied['status'] == 'denied'
        assert auth_client.post('/api/session-plans/%d/approve' % plan_id,
                                json={'ttl_minutes': 30}).status_code == 400

        plan2 = create_plan(sid, [{'host': '*', 'command': 'free -h'}])['plan_id']
        approve_plan(plan2, 60)
        ended = auth_client.post('/api/session-plans/%d/end' % plan2).json()
        assert ended['status'] == 'ended'
        assert not auth_client.get('/api/session-plans?status=approved').json()

    def test_f3_api_requires_a_session(self, client):
        assert client.get('/api/session-plans').status_code in (401, 403)

    def test_f3_deny_approved_and_end_expired_are_rejected(self, auth_client):
        """S1: a decided outcome is never rewritten."""
        sid = _mint('plan-session-state')
        plan_id = create_plan(sid, [{'host': '*', 'command': 'uptime'}])['plan_id']
        approve_plan(plan_id, 60)
        response = auth_client.post('/api/session-plans/%d/deny' % plan_id)
        assert response.status_code == 400
        assert get_plan(plan_id)['status'] == 'approved'

        plan2 = create_plan(sid, [{'host': '*', 'command': 'df -h'}])['plan_id']
        approve_plan(plan2, 60)
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute('UPDATE session_plans SET until = ? WHERE id = ?',
                         (int(time.time()) - 5, plan2))
            conn.commit()
        from db.session_plans import expire_plans
        expire_plans()
        response = auth_client.post('/api/session-plans/%d/end' % plan2)
        assert response.status_code == 400
        assert get_plan(plan2)['status'] == 'expired'

    def test_f3_non_integer_removed_ids_is_422(self, auth_client):
        """S2: a bad body is a validation error, not a 500."""
        sid = _mint('plan-session-s2')
        plan_id = create_plan(sid, [{'host': '*', 'command': 'uptime'}])['plan_id']
        response = auth_client.post('/api/session-plans/%d/approve' % plan_id,
                                    json={'ttl_minutes': 30,
                                          'removed_item_ids': ['abc']})
        assert response.status_code == 422


class TestMcp:

    def _tool(self):
        from core.mcp_server import session_plan_fn
        return session_plan_fn()

    def test_f3_mcp_session_plan_validates_inputs(self):
        tool = self._tool()
        _mint('plan-session-mcp')
        with as_agent('test-agent'):
            out = json.loads(tool(session_id='no-such-session', items=[
                {'host': '*', 'command': 'uptime'}]))
            assert out['error'] == 'unknown_session'
            out = json.loads(tool(session_id='bad id!', items=[{'command': 'uptime'}]))
            assert out['error'] == 'invalid_session_id'
            out = json.loads(tool(session_id='plan-session-mcp', items=[]))
            assert out['error'] == 'invalid_items'
            out = json.loads(tool(session_id='plan-session-mcp',
                                  items=[{'command': 'x' * 5000}]))
            assert out['error'] == 'command_too_long'
            out = json.loads(tool(session_id='plan-session-mcp',
                                  items=[{'host': 'not-an-ip', 'command': 'uptime'}]))
            assert out['error'] == 'invalid_host'
            out = json.loads(tool(session_id='plan-session-mcp',
                                  items=[{'command': 'uptime %d' % i} for i in range(51)]))
            assert out['error'] == 'too_many_items'
            out = json.loads(tool(session_id='plan-session-mcp',
                                  items=[{'command': 'uptime', 'max_uses': 0}]))
            assert out['error'] == 'invalid_max_uses'

    def test_f3_mcp_session_plan_creates_a_plan_and_notifies(self):
        tool = self._tool()
        _mint('plan-session-mcp2', goal='mcp plan')
        with as_agent('test-agent'):
            out = json.loads(tool(session_id='plan-session-mcp2', note='note',
                                  items=[{'host': '*', 'command': 'uptime'},
                                         {'host': '10.0.0.1', 'command': 'df -h'}]))
        assert out['status'] == 'proposed'
        assert out['items'] == 2 and out['hosts'] == 2
        plan = get_plan(out['plan_id'])
        assert plan['session_id'] == 'plan-session-mcp2'
        assert plan['note'] == 'note'
        assert _audit_events('plan_proposed')

    def test_f3_mcp_check_plan_reports_uses(self):
        from core.mcp_server import check_plan_fn
        tool = check_plan_fn()
        sid = _mint('plan-session-mcp3')
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime', 'max_uses': 2}])
        approve_plan(plan_id, 60)
        assert claim_plan_item(sid, '10.0.0.1', 'uptime')
        with as_agent('test-agent'):
            out = json.loads(tool(plan_id=plan_id))
        assert out['status'] == 'approved'
        assert out['items'][0]['uses'] == 1
        assert out['items'][0]['remaining'] == 1
        assert out['until'] > int(time.time())
        with as_agent('test-agent'):
            assert json.loads(tool(plan_id=999999))['error'] == 'not_found'


class TestOwnership:
    """R1: a plan belongs to the agent that minted the session."""

    def test_f3_mcp_session_plan_refuses_another_agents_session(self):
        from core.mcp_server import session_plan_fn
        sid = _mint('plan-owner-session', goal='owned by A')
        tool = session_plan_fn()
        with as_agent('agent-b'):
            out = json.loads(tool(session_id=sid, items=[{'command': 'uptime'}]))
        assert out['error'] == 'session_not_yours'
        assert not list_plans(session_id=sid), 'nothing may be stored for another agent'
        # The owner still can.
        with as_agent('test-agent'):
            out = json.loads(tool(session_id=sid, items=[{'command': 'uptime'}]))
        assert out['status'] == 'proposed'

    def test_f3_mcp_check_plan_refuses_another_agents_plan(self):
        from core.mcp_server import check_plan_fn
        sid = _mint('plan-owner-session2')
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime'}])
        approve_plan(plan_id, 60)
        tool = check_plan_fn()
        with as_agent('agent-b'):
            out = json.loads(tool(plan_id=plan_id))
        assert out['error'] == 'session_not_yours'
        with as_agent('test-agent'):
            assert json.loads(tool(plan_id=plan_id))['status'] == 'approved'

    def test_f3_plan_payload_names_the_agent_and_goal(self, auth_client):
        sid = _mint('plan-payload', goal='Rotate the TLS certificate')
        create_plan(sid, [{'host': '*', 'command': 'uptime'}])
        plan = auth_client.get('/api/session-plans?status=proposed').json()[0]
        assert plan['agent'] == 'test-agent'
        assert plan['session_goal'] == 'Rotate the TLS certificate'


class TestSecurityRound3:
    """SR1–SR4 from the security sign-off."""

    def test_f3_sr1_item_does_not_match_control_bytes(self, gateways):
        client, web01, _web02 = gateways
        sid = _mint('plan-sr1')
        plan_id = _plan(sid, [{'host': '*', 'command': 'echo ok', 'max_uses': 3}])
        approve_plan(plan_id, 60)
        # A stripped compare used to match all three; exact bytes must not.
        for command in ('echo ok\x1f', '\x1cecho ok', 'echo ok\u00a0'):
            assert 'plan' not in _request(client, web01, command, sid), repr(command)
        # The exact approved bytes still match.
        assert _request(client, web01, 'echo ok', sid).get('plan') is True

    def test_f3_sr1_proposal_refuses_hidden_characters(self):
        from db.session_plans import PlanError
        sid = _mint('plan-sr1b')
        for command in ('echo ok\x1f', '\x1cecho ok', 'echo ok\u00a0',
                        'echo\x7fok', 'a\nb'):
            with pytest.raises(PlanError) as exc:
                create_plan(sid, [{'host': '*', 'command': command}])
            assert exc.value.code == 'invalid_command', repr(command)
        # Plain spaces and tabs are the only whitespace a plan may carry.
        assert create_plan(sid, [{'host': '*', 'command': 'echo\ta b'}])['status'] == 'proposed'

    def test_f3_sr2_anonymous_caller_is_refused(self):
        from core.mcp_server import check_plan_fn, session_plan_fn
        sid = _mint('plan-sr2')
        plan_id = _plan(sid, [{'host': '*', 'command': 'uptime'}])
        approve_plan(plan_id, 60)
        # No agent context: current_agent_name() is empty, the session's is not.
        out = json.loads(session_plan_fn()(session_id=sid, items=[{'command': 'uptime'}]))
        assert out['error'] == 'session_not_yours'
        assert json.loads(check_plan_fn()(plan_id=plan_id))['error'] == 'session_not_yours'

    def test_f3_sr2_session_without_owner_is_refused(self):
        from core.mcp_server import session_plan_fn
        upsert_session('plan-sr2b', agent='', goal='no owner', source='caller')
        with as_agent('test-agent'):
            out = json.loads(session_plan_fn()(session_id='plan-sr2b',
                                               items=[{'command': 'uptime'}]))
        assert out['error'] == 'session_not_yours'

    def test_f3_sr2_anonymous_caller_and_ownerless_session_is_refused(self):
        # Empty owner and empty caller must not compare equal ('' == '').
        from core.mcp_server import check_plan_fn, session_plan_fn
        upsert_session('plan-sr2c', agent='', goal='no owner', source='caller')
        out = json.loads(session_plan_fn()(session_id='plan-sr2c',
                                           items=[{'command': 'uptime'}]))
        assert out['error'] == 'session_not_yours'
        plan_id = _plan('plan-sr2c', [{'host': '*', 'command': 'uptime'}])
        assert json.loads(check_plan_fn()(plan_id=plan_id))['error'] == 'session_not_yours'

    def test_f3_sr3_api_reports_whether_strict_sessions_are_on(self, auth_client):
        sid = _mint('plan-sr3', goal='strict hint')
        create_plan(sid, [{'host': '*', 'command': 'uptime'}])
        plan = auth_client.get('/api/session-plans?status=proposed').json()[0]
        assert plan['strict_sessions'] is False
        set_feature_flag('require_sessions', True)
        try:
            plan = auth_client.get('/api/session-plans?status=proposed').json()[0]
            assert plan['strict_sessions'] is True
        finally:
            set_feature_flag('require_sessions', False)

    @pytest.mark.skipif(NODE is None, reason='node is not installed')
    def test_f3_sr3_strict_sessions_hint_in_the_card(self):
        out = run_node({'action': 'plan-strict'})
        assert 'Strict sessions are off' in out['off']
        assert 'Strict sessions are off' not in out['on']

    @pytest.mark.skipif(NODE is None, reason='node is not installed')
    def test_f3_sr4_broad_items_are_highlighted(self):
        out = run_node({'action': 'plan-broad'})
        # `*` and max_uses > 1 are the two broad cases; a single-use exact item
        # is not highlighted.
        assert out['broadCount'] == 2
        assert out['anyHost'] is True
        assert out['multiUse'] is True
        assert 'plan-item-broad' not in out['single']


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:
    """R2, R3, S3 and S4, driven through the real app.js helpers."""

    def test_f3_plan_ttl_survives_a_poll_rerender(self):
        out = run_node({'action': 'plan-ttl', 'ttl': 30})
        assert out['ttl'] == 30
        assert 'value="30" selected' in out['rendered']
        # The queue poll re-renders the card; the choice must not jump back.
        assert 'value="30" selected' in out['again']
        assert 'value="60" selected' not in out['rendered']

    def test_f3_replaced_plan_needs_a_second_press(self):
        out = run_node({'action': 'plan-replaced'})
        assert out['replaced'] is True
        assert out['needsConfirm'] is True
        assert 'replaced the one you were reviewing' in out['card']
        assert 'Press again to approve' in out['afterFirstPress']
        assert out['firstPressPosts'] == 0
        assert out['secondPressPosts'] == 1
        assert '"ttl_minutes":60' in out['secondBody']

    def test_f3_long_unknown_plan_needs_a_second_press(self):
        out = run_node({'action': 'plan-s4'})
        assert out['replaced'] is False
        assert out['needsConfirm'] is True
        assert out['firstPressPosts'] == 0
        assert out['secondPressPosts'] == 1

    def test_f3_plan_fetch_uses_authed_fetch(self):
        out = run_node({'action': 'plan-authfetch'})
        assert any('/api/session-plans' in url for url in out['calls']['auth'])
        assert not any('/api/session-plans' in url for url in out['calls']['plain'])
