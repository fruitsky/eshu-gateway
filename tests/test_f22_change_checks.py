"""Chunk F22 — changes carry a check.

Synthetic data only: TEST-NET addresses, an `ha` integration with one read
tool, a fake SSH runner. The engine's own seams (`ssh_exec`, `run_ssh`) are
stubbed where a run is needed; the plan claim, the token, the expect
comparison, the state machine and the sweep run for real.
"""
import base64
import json
import time

import pytest

from core import checks
from core import ssh_exec as sx
from core.ssh_crypto import canonical, prefix_line
from db.agent_policies import set_agent_policy
from db.agent_tokens import (
    create_agent_token, get_agent_by_name, revoke_agent_token,
)
from db.audit import get_audit_log
from db.gateways import (
    register_gateway, set_gateway_run_limits, set_gateway_ssh_capabilities,
    set_gateway_ssh_host_key, set_gateway_token,
)
from db.integrations import create_integration, create_tool, get_tool, update_tool
from db.requests import (
    create_request, expire_stale_requests, get_all_requests, get_request_row,
)
from db.session_plans import (
    CHECK_INTERNAL, PlanError, approve_plan, claim_check_item, create_plan,
    deny_plan, end_plan, get_plan, get_plan_item, link_check_card,
    release_check_link,
)
from db.sessions import upsert_session

IP = '10.0.0.1'
SESSION = 's-f22'


def b64(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def env(temp_db, monkeypatch):
    register_gateway(IP, 'web01', 'v15.3')
    set_gateway_token(IP, 'tok-web01')
    set_gateway_run_limits(IP, 'v1')
    set_gateway_ssh_host_key(IP, 'ssh-ed25519 ' + base64.b64encode(
        b'0' * 32).decode() + ' root@web01')
    set_gateway_ssh_capabilities(IP, sx.AGENT_FORMAT, sx.TICKET_FORMAT)
    create_agent_token('agent-a')
    agent = get_agent_by_name('agent-a')
    set_agent_policy(agent['id'], ssh_hosts_allow=[IP], ssh_mode='normal')
    upsert_session(SESSION, agent='agent-a', goal='f22')
    ha = create_integration('HA', 'https://192.0.2.10/api', 'bearer', 'tok',
                            kind='ha')
    create_tool(ha, 'get_state', 'Read one state', 'GET',
                '/states/{entity_id}', [
                    {'name': 'entity_id', 'type': 'string', 'required': True},
                ], '{"state": "on"}', read_only=True)
    return {'agent': agent, 'ha': ha}


def _plan(items, ttl=60, decide_by='me'):
    create_plan(SESSION, items)
    plan = get_plan(1)
    approve_plan(plan['id'], ttl, decided_by=decide_by)
    return get_plan(plan['id'])


def _ssh_item(check=None, command='systemctl restart nginx', host=IP,
              max_uses=1):
    item = {'host': host, 'command': command, 'max_uses': max_uses}
    if check is not None:
        item['check'] = check
    return item


def _post(client, command, session=SESSION, force_jit=True, agent='agent-a',
          check_token='', run_limits='v1', headers=None):
    body = {'target_ip': IP, 'encoded_command': b64(command),
            'session_id': session, 'force_jit': force_jit,
            'agent': agent, 'check_token': check_token,
            'run_limits': run_limits}
    return client.post('/api/request', json=body,
                       headers=headers or {'X-Gateway-Token': 'tok-web01'})


def _stub_ssh(monkeypatch, results):
    """Stub the runner; `results` is a list of (status, exit_code)."""
    calls = []
    queue = list(results)

    def fake(gateway, command_line, known_hosts, key_path, timeout):
        calls.append(command_line)
        status, exit_code = queue.pop(0) if queue else ('ran', 0)
        envelope = {'status': status, 'request_id': 70,
                    'exit_code': exit_code, 'output_b64': '',
                    'truncated': False, 'messages': []}
        return {'rc': 0,
                'stdout': sx.ENVELOPE_PREFIX + json.dumps(envelope),
                'stderr': '', 'timed_out': False}

    monkeypatch.setattr(sx, 'run_ssh', fake)
    monkeypatch.setattr(sx.ssh_keys, 'write_known_hosts',
                        lambda ip, key: '/tmp/kh.' + ip)
    monkeypatch.setattr(sx.ssh_keys, 'ensure_key_pair', lambda: '/tmp/key')
    return calls


def _stub_tool(monkeypatch, payload, status=200):
    monkeypatch.setattr('core.tool_runner.run_tool',
                        lambda integration, tool, args, reason='':
                        json.dumps(payload))


def _linked_request(client, check=None):
    """An approval-mode card for the first item, with its link."""
    plan = _plan([_ssh_item(check or {'kind': 'ssh',
                                      'command': 'systemctl is-active nginx'})])
    response = _post(client, 'systemctl restart nginx')
    assert response.status_code == 200
    body = response.json()
    row = get_request_row(body['request_id'])
    item = plan['items'][0]
    return plan, item, row


# ── proposal ───────────────────────────────────────────────────────────

class TestProposal:
    def test_a_write_ssh_check_is_refused(self, env):
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [_ssh_item(
                {'kind': 'ssh', 'command': 'systemctl restart nginx'})])
        assert exc.value.code == 'check_not_a_read'

    def test_a_write_tool_check_is_refused(self, env):
        create_tool(env['ha'], 'set_state', 'Write one state', 'POST',
                    '/states/{entity_id}', [
                        {'name': 'entity_id', 'type': 'string', 'required': True},
                    ], '{}', read_only=False)
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [_ssh_item(
                {'kind': 'tool', 'integration': 'HA', 'tool': 'set_state',
                 'args': {'entity_id': 'light.a'},
                 'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}})])
        assert exc.value.code == 'check_not_a_read'

    def test_a_valid_ssh_check_is_stored_on_the_item(self, env):
        check = {'kind': 'ssh', 'command': 'systemctl is-active nginx',
                 'wait_s': 3, 'retries': 1}
        plan = _plan([_ssh_item(check)])
        stored = plan['items'][0]['check']
        assert stored['kind'] == 'ssh'
        assert stored['command'] == 'systemctl is-active nginx'
        assert stored['wait_s'] == 3 and stored['retries'] == 1

    def test_a_valid_tool_check_is_stored(self, env):
        check = {'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
                 'args': {'entity_id': 'light.a'},
                 'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}}
        plan = _plan([_ssh_item(check)])
        assert plan['items'][0]['check']['expect']['op'] == 'eq'

    @pytest.mark.parametrize('check,code', [
        ({'kind': 'ssh', 'command': 'uptime',
          'retries': 4}, 'invalid_retries'),
        ({'kind': 'ssh', 'command': 'uptime',
          'wait_s': 31}, 'invalid_wait'),
        ({'kind': 'nope'}, 'invalid_check'),
        ({'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
          'args': {'entity_id': 'light.a'},
          'expect': {'path': 'a.b.c.d.e', 'op': 'eq', 'value': 1}},
         'invalid_expect_path'),
        ({'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
          'args': {'entity_id': 'light.a'},
          'expect': {'path': 'state', 'op': 'regex', 'value': 'on'}},
         'invalid_expect_op'),
        ({'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
          'args': {'entity_id': 'light.a'},
          'expect': {'path': 'state', 'op': 'eq', 'value': True}},
         'expect_value_type'),
        ({'kind': 'tool', 'integration': 'HA', 'tool': 'nope',
          'args': {}, 'expect': {'path': 'x', 'op': 'eq', 'value': 1}},
         'check_tool_disabled'),
    ])
    def test_bad_shapes_are_refused(self, env, check, code):
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [_ssh_item(check)])
        assert exc.value.code == code

    def test_a_tool_that_became_a_write_is_refused(self, env):
        tool = get_tool('HA', 'get_state')
        update_tool(tool['id'], read_only=False)
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [_ssh_item(
                {'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
                 'args': {'entity_id': 'light.a'},
                 'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}})])
        assert exc.value.code == 'check_not_a_read'


# ── expect ─────────────────────────────────────────────────────────────

class TestExpect:
    def test_eq_is_type_strict(self):
        expect = {'path': 'state', 'op': 'eq', 'value': 1}
        assert checks.evaluate_expect(expect, {'state': 1})[0] == 'passed'
        assert checks.evaluate_expect(expect, {'state': '1'})[0] == 'failed'
        assert checks.evaluate_expect(expect, {'state': True})[0] == 'failed'

    def test_ne(self):
        expect = {'path': 'state', 'op': 'ne', 'value': 'off'}
        assert checks.evaluate_expect(expect, {'state': 'on'})[0] == 'passed'
        assert checks.evaluate_expect(expect, {'state': 'off'})[0] == 'failed'

    def test_in_needs_a_list(self):
        expect = {'path': 'tags', 'op': 'in', 'value': 'b'}
        assert checks.evaluate_expect(expect, {'tags': ['a', 'b']})[0] == 'passed'
        assert checks.evaluate_expect(expect, {'tags': 'b'})[0] == 'not_run'

    def test_lt_gt_need_numbers(self):
        assert checks.evaluate_expect(
            {'path': 'n', 'op': 'lt', 'value': 5}, {'n': 4})[0] == 'passed'
        assert checks.evaluate_expect(
            {'path': 'n', 'op': 'gt', 'value': 5}, {'n': 5})[0] == 'failed'
        assert checks.evaluate_expect(
            {'path': 'n', 'op': 'lt', 'value': 5}, {'n': '4'})[0] == 'not_run'

    def test_a_list_index_is_a_path(self):
        expect = {'path': 'items.0.state', 'op': 'eq', 'value': 'on'}
        value = {'items': [{'state': 'on'}]}
        assert checks.evaluate_expect(expect, value)[0] == 'passed'

    def test_a_missing_path_is_not_run(self):
        expect = {'path': 'state', 'op': 'eq', 'value': 'on'}
        state, reason, _obs, _exp = checks.evaluate_expect(expect, {})
        assert state == 'not_run' and reason == 'expect_path_missing'


# ── the check plan ─────────────────────────────────────────────────────

class TestCheckPlan:
    def test_a_check_plan_borrows_the_parent(self, env):
        plan = _plan([_ssh_item({'kind': 'ssh',
                                 'command': 'systemctl is-active nginx'})])
        parent = plan['items'][0]
        created = create_plan(
            SESSION, [{'host': IP, 'command': 'systemctl is-active nginx',
                       'max_uses': 1}],
            note='check', origin='check', internal=CHECK_INTERNAL, parent=plan)
        check_plan = get_plan(created['plan_id'])
        assert check_plan['status'] == 'approved'
        assert check_plan['parent_plan_id'] == plan['id']
        assert check_plan['decided_by'] == plan['decided_by'] == 'me'
        assert check_plan['until'] <= plan['until']
        assert len(check_plan['items']) == 1
        assert parent['check_plan_id'] == 0   # set by the worker, not here

    def test_the_sentinel_is_required(self, env):
        plan = _plan([_ssh_item()])
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [{'host': IP, 'command': 'uptime'}],
                        origin='check', parent=plan)
        assert exc.value.code == 'invalid_origin'

    def test_two_items_are_refused(self, env):
        plan = _plan([_ssh_item()])
        with pytest.raises(PlanError) as exc:
            create_plan(SESSION, [{'host': IP, 'command': 'uptime'},
                                  {'host': IP, 'command': 'df -h'}],
                        origin='check', internal=CHECK_INTERNAL, parent=plan)
        assert exc.value.code == 'check_items'

    def test_ending_the_parent_ends_the_check_plan(self, env):
        plan = _plan([_ssh_item()])
        created = create_plan(
            SESSION, [{'host': IP, 'command': 'systemctl is-active nginx'}],
            origin='check', internal=CHECK_INTERNAL, parent=plan)
        end_plan(plan['id'])
        assert get_plan(created['plan_id'])['status'] == 'ended'

    def test_a_claimed_check_spends_one_use(self, env):
        plan = _plan([_ssh_item()])
        created = create_plan(
            SESSION, [{'host': IP, 'command': 'systemctl is-active nginx'}],
            origin='check', internal=CHECK_INTERNAL, parent=plan)
        from db.session_plans import set_check_item_token
        token = 'a' * 32
        set_check_item_token(created['plan_id'], token)
        assert claim_check_item(SESSION, IP, 'systemctl is-active nginx',
                                token, owner='agent-a') is not None
        assert claim_check_item(SESSION, IP, 'systemctl is-active nginx',
                                token, owner='agent-a') is None


def _armed(client, check=None):
    """A linked card, the F5 hook, and the one-item check plan the worker
    would have made before its ssh attempt."""
    plan = _plan([_ssh_item(check or {'kind': 'ssh',
                                      'command': 'systemctl is-active nginx'})])
    response = _post(client, 'systemctl restart nginx').json()
    row = get_request_row(response['request_id'])
    checks.on_result(row['id'], 0)
    item = get_plan_item(row['plan_item_id'])
    from db.session_plans import set_check_item_parent, set_check_item_token
    created = create_plan(
        SESSION, [{'host': IP, 'command': 'systemctl is-active nginx',
                   'max_uses': 1}],
        note='check', origin='check', internal=CHECK_INTERNAL, parent=plan)
    set_check_item_token(created['plan_id'], item['check_token'])
    set_check_item_parent(created['plan_id'], item['id'])
    return plan, item, created


# ── AV1: the token and the claim ──────────────────────────────────────

class TestToken:
    def test_the_card_is_linked_by_server_code(self, auth_client, env):
        plan, item, row = _linked_request(auth_client)
        assert row['plan_item_id'] == item['id']
        fresh = get_plan_item(item['id'])
        assert fresh['check_request_id'] == row['id']
        # The stored row carries no token, and neither does the plan view.
        assert 'plan-check' not in (row['reason'] or '')
        assert all('check_token' not in it for it in get_plan(plan['id'])['items'])

    def test_a_second_identical_card_gets_no_link(self, auth_client, env):
        plan, item, first = _linked_request(auth_client)
        second = _post(auth_client, 'systemctl restart nginx').json()
        assert get_request_row(second['request_id'])['plan_item_id'] is None

    def test_a_different_host_or_bytes_gets_no_link(self, auth_client, env):
        _linked_request(auth_client)
        other = _post(auth_client, 'systemctl restart other').json()
        assert get_request_row(other['request_id'])['plan_item_id'] is None

    def test_the_check_claim_needs_the_signed_token(self, auth_client, env):
        plan, item, _created = _armed(auth_client)
        token = item['check_token']
        assert token and len(token) >= 16
        # A direct-SSH request carrying the token never claims.
        direct = _post(auth_client, 'systemctl is-active nginx',
                       force_jit=False, check_token=token)
        assert 'plan' not in direct.json()
        assert get_request_row(direct.json()['request_id'])['status'] == 'pending'
        # The dashboard-signed one does, and the row has no token.
        claimed = _post(auth_client, 'systemctl is-active nginx',
                        check_token=token)
        claimed = claimed.json()
        assert claimed.get('plan') is True
        claimed_row = get_request_row(claimed['request_id'])
        assert claimed_row['status'] == 'approved'
        assert claimed_row['unattended'] == 1
        assert claimed_row['plan_item_id'] == item['id']
        assert token not in (claimed_row['reason'] or '')
        assert claimed_row['reason'].startswith('plan-check:')
        # The stored row, and every view, lack the token.
        assert token not in json.dumps(get_plan(plan['id']))
        from core.mcp_server import check_plan_fn
        assert token not in check_plan_fn()(plan['id'])

    def test_a_wrong_token_consumes_nothing(self, auth_client, env):
        plan, item, created = _armed(auth_client)
        wrong = 'b' * 32
        response = _post(auth_client, 'systemctl is-active nginx',
                         check_token=wrong).json()
        assert 'plan' not in response
        assert get_plan(created['plan_id'])['items'][0]['uses'] == 0

    def test_attempt_ones_token_does_not_spend_attempt_two(self, auth_client,
                                                            env):
        plan, item, created = _armed(auth_client)
        first = item['check_token']
        assert _post(auth_client, 'systemctl is-active nginx',
                     check_token=first).json().get('plan') is True
        second = _post(auth_client, 'systemctl is-active nginx',
                       check_token=first).json()
        assert 'plan' not in second

    def test_a_claim_needs_the_verified_agent(self, auth_client, env):
        # AY2/AY3: the verified agent must be the check plan's agent.
        plan, item, created = _armed(auth_client)
        response = _post(auth_client, 'systemctl is-active nginx',
                         check_token=item['check_token'],
                         agent='agent-b').json()
        assert 'plan' not in response
        assert get_plan(created['plan_id'])['items'][0]['uses'] == 0

    def test_a_gateway_without_limits_never_claims(self, auth_client, env):
        plan, item, created = _armed(auth_client)
        response = _post(auth_client, 'systemctl is-active nginx',
                         check_token=item['check_token'],
                         run_limits='').json()
        assert 'plan' not in response
        assert get_plan(created['plan_id'])['items'][0]['uses'] == 0

    def test_the_audit_row_has_no_token(self, auth_client, env):
        plan, item, created = _armed(auth_client)
        token = item['check_token']
        _post(auth_client, 'systemctl is-active nginx', check_token=token)
        events = [e for e in get_audit_log()
                  if e['event_type'] == 'jit_check_approved']
        assert events
        assert all(token not in json.dumps(e) for e in events)


# ── the worker ────────────────────────────────────────────────────────

def _pending_item(env, client, check):
    """A linked card, the F5 hook, and the item in `pending`."""
    plan = _plan([_ssh_item(check)])
    response = _post(client, 'systemctl restart nginx').json()
    row = get_request_row(response['request_id'])
    checks.on_result(row['id'], 0)
    item = get_plan_item(row['plan_item_id'])
    return plan, item, item['check_token']


class TestWorker:
    def test_a_passing_ssh_check_keeps_the_plan(self, auth_client, env,
                                                monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'passed'
        assert fresh['check_attempts'] == 1
        assert get_plan(plan['id'])['status'] == 'approved'
        assert fresh['check_observed'] == ''   # never the output

    def test_a_failing_ssh_check_stops_the_plan(self, auth_client, env,
                                                monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 3)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'failed'
        assert fresh['check_exit_code'] == 3
        stopped = get_plan(plan['id'])
        assert stopped['status'] == 'stopped'
        assert stopped['stopped_reason'].startswith('check_failed:%d' % item['id'])

    def test_a_passing_tool_check(self, auth_client, env, monkeypatch):
        _stub_tool(monkeypatch, {'state': 'on'})
        plan = _plan([_ssh_item({
            'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
            'args': {'entity_id': 'light.a'},
            'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}})])
        response = _post(auth_client, 'systemctl restart nginx').json()
        row = get_request_row(response['request_id'])
        checks.on_result(row['id'], 0)
        item = get_plan_item(row['plan_item_id'])
        checks._run_check(item['id'], item['check_token'])
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'passed'
        assert '"on"' in fresh['check_observed']

    def test_a_failing_tool_check_stops_the_plan(self, auth_client, env,
                                                 monkeypatch):
        _stub_tool(monkeypatch, {'state': 'off'})
        plan = _plan([_ssh_item({
            'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
            'args': {'entity_id': 'light.a'},
            'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}})])
        response = _post(auth_client, 'systemctl restart nginx').json()
        row = get_request_row(response['request_id'])
        checks.on_result(row['id'], 0)
        item = get_plan_item(row['plan_item_id'])
        checks._run_check(item['id'], item['check_token'])
        assert get_plan_item(item['id'])['check_state'] == 'failed'
        assert get_plan(plan['id'])['status'] == 'stopped'

    def test_a_missing_path_is_not_run_and_stops(self, auth_client, env,
                                                 monkeypatch):
        _stub_tool(monkeypatch, {'other': 1})
        plan = _plan([_ssh_item({
            'kind': 'tool', 'integration': 'HA', 'tool': 'get_state',
            'args': {'entity_id': 'light.a'},
            'expect': {'path': 'state', 'op': 'eq', 'value': 'on'}})])
        response = _post(auth_client, 'systemctl restart nginx').json()
        row = get_request_row(response['request_id'])
        checks.on_result(row['id'], 0)
        item = get_plan_item(row['plan_item_id'])
        checks._run_check(item['id'], item['check_token'])
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] == 'expect_path_missing'
        assert get_plan(plan['id'])['status'] == 'stopped'

    def test_a_pending_ssh_result_is_not_run(self, auth_client, env,
                                             monkeypatch):
        _stub_ssh(monkeypatch, [('pending', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] in ('check_unclaimed', 'check_no_limits')
        assert get_plan(plan['id'])['status'] == 'stopped'

    def test_a_read_that_became_a_write_is_not_run(self, auth_client, env,
                                                   monkeypatch):
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        # The command stops parsing as a read (simulate a rule change).
        calls = _stub_ssh(monkeypatch, [('ran', 0)])
        monkeypatch.setattr(checks, 'is_read_ssh', lambda bd: (False, 'now a write'))
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] == 'check_not_a_read'
        assert calls == []

    def test_a_revoked_agent_never_runs_the_check(self, auth_client, env,
                                                  monkeypatch):
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        calls = _stub_ssh(monkeypatch, [('ran', 0)])
        revoke_agent_token(get_agent_by_name('agent-a')['id'])
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] == 'check_agent_inactive'
        assert calls == []

    def test_retries_stop_at_the_bound(self, auth_client, env, monkeypatch):
        calls = _stub_ssh(monkeypatch, [('ran', 1), ('ran', 1), ('ran', 1)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx',
                               'retries': 1})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'failed'
        assert fresh['check_attempts'] == 2
        assert len(calls) == 2

    def test_a_retry_can_pass(self, auth_client, env, monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 1), ('ran', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx',
                               'retries': 1})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'passed'
        assert fresh['check_attempts'] == 2
        assert get_plan(plan['id'])['status'] == 'approved'

    def test_a_not_run_never_retries(self, auth_client, env, monkeypatch):
        calls = _stub_ssh(monkeypatch, [('pending', 0), ('ran', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx',
                               'retries': 3})
        checks._run_check(item['id'], token)
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_attempts'] == 1
        assert len(calls) == 1

    def test_a_stale_token_is_abandoned(self, auth_client, env, monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        checks._run_check(item['id'], 'c' * 32)
        assert get_plan_item(item['id'])['check_state'] == 'pending'

    def test_a_skipped_check_and_its_stop(self, auth_client, env):
        plan = _plan([_ssh_item({'kind': 'ssh',
                                 'command': 'systemctl is-active nginx'})])
        response = _post(auth_client, 'systemctl restart nginx').json()
        row = get_request_row(response['request_id'])
        checks.on_result(row['id'], 1)
        fresh = get_plan_item(row['plan_item_id'])
        assert fresh['check_state'] == 'skipped'
        assert get_plan(plan['id'])['status'] == 'stopped'

    def test_a_limiter_stop_is_not_run(self, auth_client, env):
        plan = _plan([_ssh_item({'kind': 'ssh',
                                 'command': 'systemctl is-active nginx'})])
        response = _post(auth_client, 'systemctl restart nginx').json()
        row = get_request_row(response['request_id'])
        checks.on_result(row['id'], 0, limit_kind='time')
        fresh = get_plan_item(row['plan_item_id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] == 'check_limit'
        assert get_plan(plan['id'])['status'] == 'stopped'

    def test_a_duplicate_result_dispatches_once(self, auth_client, env):
        plan, item, row = _linked_request(auth_client)
        assert checks.on_result(row['id'], 0) is None
        first = get_plan_item(item['id'])['check_token']
        assert checks.on_result(row['id'], 0) is None
        assert get_plan_item(item['id'])['check_token'] == first

    def test_the_sweep_turns_an_abandoned_check_into_not_run(self, auth_client,
                                                             env, monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 0)])
        plan, item, token = _pending_item(
            env, auth_client, {'kind': 'ssh', 'command': 'systemctl is-active nginx'})
        with __import__('db.core', fromlist=['db_conn']).db_conn() as conn:
            conn.execute('UPDATE session_plan_items SET check_at = 1 WHERE id = ?',
                         (item['id'],))
            conn.commit()
        assert checks.sweep_interrupted() == 1
        fresh = get_plan_item(item['id'])
        assert fresh['check_state'] == 'not_run'
        assert fresh['check_reason'] == 'check_interrupted'
        assert get_plan(plan['id'])['status'] == 'stopped'
        assert checks.sweep_interrupted() == 0


# ── order, and the next item ──────────────────────────────────────────

class TestOrder:
    def test_an_open_check_blocks_the_next_item(self, auth_client, env):
        plan = _plan([
            _ssh_item({'kind': 'ssh', 'command': 'systemctl is-active nginx'},
                      command='systemctl restart nginx'),
            _ssh_item(command='systemctl restart db'),
        ])
        # Normal mode: the first item is claimed through the plan.
        first = _post(auth_client, 'systemctl restart nginx',
                      force_jit=False).json()
        assert first.get('plan') is True
        checks.on_result(first['request_id'], 0)   # the check is now pending
        blocked = _post(auth_client, 'systemctl restart db',
                        force_jit=False).json()
        assert 'plan' not in blocked
        assert get_request_row(blocked['request_id'])['status'] == 'pending'

    def test_an_open_check_blocks_the_items_own_second_use(self, auth_client,
                                                           env):
        _plan([_ssh_item({'kind': 'ssh',
                          'command': 'systemctl is-active nginx'},
                         max_uses=2)])
        first = _post(auth_client, 'systemctl restart nginx',
                      force_jit=False).json()
        checks.on_result(first['request_id'], 0)
        again = _post(auth_client, 'systemctl restart nginx',
                      force_jit=False).json()
        assert 'plan' not in again

    def test_a_passed_check_releases_the_next_item(self, auth_client, env,
                                                   monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 0)])
        plan = _plan([
            _ssh_item({'kind': 'ssh', 'command': 'systemctl is-active nginx'},
                      command='systemctl restart nginx'),
            _ssh_item(command='systemctl restart db'),
        ])
        first = _post(auth_client, 'systemctl restart nginx',
                      force_jit=False).json()
        checks.on_result(first['request_id'], 0)
        item = get_plan_item(first['request_id'] and get_request_row(
            first['request_id'])['plan_item_id'])
        checks._run_check(item['id'], item['check_token'])
        assert get_plan_item(item['id'])['check_state'] == 'passed'
        second = _post(auth_client, 'systemctl restart db',
                       force_jit=False).json()
        assert second.get('plan') is True


# ── AV2 ────────────────────────────────────────────────────────────────

class TestCardWarning:
    def test_the_linked_card_warns_while_the_check_is_open(self, auth_client,
                                                           env):
        plan, item, row = _linked_request(auth_client)
        assert checks.card_warning(row['id']) is None
        checks.on_result(row['id'], 0)
        warning = checks.card_warning(row['id'])
        assert warning['state'] == 'open'
        assert 'check open' in warning['text']

    def test_the_warning_turns_red_when_the_check_failed(self, auth_client, env,
                                                         monkeypatch):
        _stub_ssh(monkeypatch, [('ran', 1)])
        plan, item, row = _linked_request(auth_client)
        checks.on_result(row['id'], 0)
        fresh = get_plan_item(item['id'])
        checks._run_check(fresh['id'], fresh['check_token'])
        warning = checks.card_warning(row['id'])
        assert warning['state'] == 'failed'

    def test_denying_the_card_releases_the_link(self, auth_client, env):
        plan, item, row = _linked_request(auth_client)
        assert auth_client.post('/api/deny/%d' % row['id']).status_code == 200
        assert get_plan_item(item['id'])['check_request_id'] == 0
        again = _post(auth_client, 'systemctl restart nginx').json()
        assert get_request_row(again['request_id'])['plan_item_id'] == item['id']

    def test_an_expired_card_releases_the_link(self, auth_client, env):
        plan, item, row = _linked_request(auth_client)
        with __import__('db.core', fromlist=['db_conn']).db_conn() as conn:
            conn.execute('UPDATE requests SET expires_at = 1 WHERE id = ?',
                         (row['id'],))
            conn.commit()
        expire_stale_requests()
        assert get_plan_item(item['id'])['check_request_id'] == 0


# ── the signed field (AV1) ────────────────────────────────────────────

class TestSignedField:
    def test_v2_includes_the_token_and_v1_is_unchanged(self):
        base = dict(agent='agent-a', session='s1', mcp='1', force_jit='0',
                    ts='1700000000', nonce='0' * 16, gateway_ip=IP)
        v1 = canonical('uptime', **base)
        assert v1.startswith('v1\n')
        token = 'a' * 32
        v2 = canonical('uptime', check_token=token, **base)
        assert v2.startswith('v2\n')
        assert token in v2.split('\n')
        # The token is inside the MACed text, before the command hash.
        assert v2.split('\n')[-2] == token

    def test_the_prefix_carries_the_token_only_when_set(self):
        assert 'ESHU_CHECK_TOKEN' not in prefix_line(
            'agent-a', '', True, '1', '0' * 16, 'f' * 64)
        line = prefix_line('agent-a', '', True, '1', '0' * 16, 'f' * 64,
                           check_token='a' * 32)
        assert 'ESHU_CHECK_TOKEN=' + 'a' * 32 in line

    def test_a_bad_token_is_refused(self):
        with pytest.raises(ValueError):
            canonical('uptime', agent='a', session='', mcp='1',
                      force_jit='0', ts='1', nonce='0' * 16, gateway_ip=IP,
                      check_token='short')
