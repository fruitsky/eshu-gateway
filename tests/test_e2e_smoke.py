"""E2E-1 round 2: the suite's pure rules, preflight and cleanup, off-network."""
import os
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from e2e import run  # noqa: E402


GATEWAY = '192.0.2.10'
OTHER = '192.0.2.11'


def _secret(path, value='secret-value', mode=0o600):
    path.write_text(value, encoding='utf-8')
    os.chmod(path, mode)
    return str(path)


def _env(tmp_path, base='http://127.0.0.1:8000', gateway=GATEWAY,
         mode=0o600, extra=None):
    env = {
        'ESHU_E2E_AGENT_TOKEN_FILE': _secret(tmp_path / 'agent.token',
                                            'agent-secret', mode),
        'ESHU_E2E_APPROVER_TOKEN_FILE': _secret(tmp_path / 'approver.token',
                                               'e2e_approver', mode),
        'ESHU_E2E_GATEWAY': gateway,
        'ESHU_E2E_BASE_URL': base,
    }
    env.update(extra or {})
    return env


def _agent(test_agent=True, mode='normal', hosts=None, tools_mode='allowlist'):
    return {'id': 7, 'policy': {
        'test_agent': test_agent, 'ssh_mode': mode,
        'ssh_hosts_allow': [GATEWAY] if hosts is None else hosts,
        'tools_mode': tools_mode,
    }}


class TestConfig:
    def test_loopback_http_is_the_default_and_is_accepted(self, tmp_path):
        cfg = run.Config.from_env(_env(tmp_path))
        assert cfg.base_url == 'http://127.0.0.1:8000'
        assert cfg.gateway == GATEWAY
        assert cfg.agent_token == 'agent-secret'
        assert cfg.approver_token == 'e2e_approver'

    def test_group_or_world_readable_token_file_is_refused(self, tmp_path):
        env = _env(tmp_path)
        os.chmod(env['ESHU_E2E_AGENT_TOKEN_FILE'], 0o644)
        with pytest.raises(run.UnsafeConfig):
            run.Config.from_env(env)

    def test_empty_token_file_is_refused(self, tmp_path):
        env = _env(tmp_path)
        open(env['ESHU_E2E_APPROVER_TOKEN_FILE'], 'w').close()
        with pytest.raises(run.UnsafeConfig):
            run.Config.from_env(env)

    def test_a_non_loopback_http_url_is_refused(self, tmp_path):
        env = _env(tmp_path, base='http://203.0.113.5:8000')
        with pytest.raises(run.UnsafeConfig):
            run.Config.from_env(env)

    def test_non_loopback_https_needs_the_remote_flag(self, tmp_path):
        env = _env(tmp_path, base='https://203.0.113.5:8000')
        with pytest.raises(run.UnsafeConfig):
            run.Config.from_env(env)
        env['ESHU_E2E_ALLOW_REMOTE'] = '1'
        cfg = run.Config.from_env(env)
        assert cfg.base_url == 'https://203.0.113.5:8000'

    def test_the_gateway_must_be_one_ip(self, tmp_path):
        with pytest.raises(run.UnsafeConfig):
            run.Config.from_env(_env(tmp_path, gateway='gateway.example.test'))


class TestTestAgentGuard:
    def _cfg(self):
        return SimpleNamespace(gateway=GATEWAY)

    def test_the_narrow_policy_passes(self):
        assert run.check_test_agent(_agent(), self._cfg())['test_agent'] is True

    @pytest.mark.parametrize('agent', [
        _agent(test_agent=False),
        _agent(mode='approval'),
        _agent(hosts=[]),
        _agent(hosts=[GATEWAY, OTHER]),
        _agent(hosts=['gateway.example.test']),
        _agent(tools_mode='all'),
    ])
    def test_every_wider_policy_is_refused(self, agent):
        with pytest.raises(run.UnsafeConfig):
            run.check_test_agent(agent, self._cfg())


class TestApprover:
    def _row(self, **kw):
        row = {'agent_id': 7, 'session_id': 'e2e-session',
               'target_ip': GATEWAY, 'command': run.JIT_COMMAND}
        row.update(kw)
        return row

    def test_own_row_is_allowed_and_a_deny_needs_no_command_list(self):
        approver = run.Approver(7, 'e2e-session', GATEWAY)
        assert approver.request(self._row())[0]
        assert approver.request(self._row(command='rm -rf /'), deny=True)[0]
        assert not approver.request(self._row(command='rm -rf /'))[0]

    def test_another_agent_session_host_or_zero_is_refused(self):
        approver = run.Approver(7, 'e2e-session', GATEWAY)
        assert not approver.request(self._row(agent_id=0))[0]
        assert not approver.request(self._row(agent_id=8))[0]
        assert not approver.request(self._row(session_id='other'))[0]
        assert not approver.request(self._row(target_ip=OTHER))[0]

    def test_a_window_must_be_exact_single_use_and_listed(self):
        approver = run.Approver(7, 'e2e-session', GATEWAY)
        base = self._row(command=run.WINDOW_COMMAND, match_type='exact',
                         max_executions=1)
        assert approver.window(base)[0]
        assert not approver.window(dict(base, match_type='prefix'))[0]
        assert not approver.window(dict(base, max_executions=2))[0]
        assert not approver.window(dict(base, command='whoami'))[0]


class TestRedaction:
    def test_tokens_commands_hosts_and_entity_ids_never_reach_the_table(self):
        runner = run.Runner.__new__(run.Runner)
        runner.cfg = SimpleNamespace(
            agent_token='agent-secret', approver_token='e2e_approver',
            gateway_ssh_key='/home/reviewer/.ssh/e2e',
            gateway=GATEWAY, dns_name='e2e.example.test',
            allowance_entity='light.e2e', allowance_other_entity='light.other')
        text = ('agent-secret e2e_approver %s %s light.e2e token=abcdef '
                'request=42' % (GATEWAY, run.JIT_COMMAND))
        out = runner.redact(text)
        for secret in ('agent-secret', 'e2e_approver', GATEWAY,
                       run.JIT_COMMAND, 'light.e2e', 'abcdef'):
            assert secret not in out
        assert 'request=42' in out


class FakeOperator:
    def __init__(self, refuse_deny=False):
        self.actions = []
        self.denied = False
        self.refuse_deny = refuse_deny

    def requests(self, session_id):
        return [] if self.denied else [{'id': 1, 'status': 'pending',
                                        'session_id': session_id,
                                        'agent_id': 7, 'target_ip': GATEWAY,
                                        'command': run.JIT_COMMAND}]

    def plans(self, session_id):
        return [{'id': 2, 'status': 'proposed', 'session_id': session_id,
                 'agent_id': 7}]

    def pending_calls(self, session_id):
        return [{'id': 3, 'status': 'pending', 'session_id': session_id,
                 'agent_id': 7}]

    def deny_request(self, rid, approver, session_id):
        if self.refuse_deny:
            raise run.Failure('row agent is not the token agent')
        self.actions.append('deny_request')
        self.denied = True

    def deny_plan(self, pid, approver, session_id):
        self.actions.append('deny_plan')

    def deny_call(self, cid, approver, session_id):
        self.actions.append('deny_call')

    def reject_conclusion(self, cid):
        self.actions.append('reject')

    def exclude(self, session_id):
        self.actions.append('exclude')

    def delete_window(self, wid):
        self.actions.append('delete_window')


class FakeAgent:
    def __init__(self):
        self.actions = []

    def call(self, tool, **kwargs):
        self.actions.append(tool)
        return {'id': 9}


class TestCleanup:
    def _runner(self, op):
        runner = run.Runner.__new__(run.Runner)
        runner.operator = op
        runner.agent = FakeAgent()
        runner.session_id = 'e2e-session'
        runner.approver = run.Approver(7, 'e2e-session', GATEWAY)
        runner.window_id = 4
        runner.cfg = SimpleNamespace(
            keep=False, agent_token='', approver_token='',
            gateway_ssh_key='', gateway=GATEWAY, dns_name='',
            allowance_entity='', allowance_other_entity='')
        return runner

    def test_deny_then_conclude_reject_exclude_and_delete(self):
        op = FakeOperator()
        runner = self._runner(op)
        assert runner.cleanup() == []
        assert op.actions == ['deny_request', 'deny_plan', 'deny_call',
                              'reject', 'exclude', 'delete_window']
        assert runner.agent.actions == ['session_conclude']

    def test_the_first_by_refusal_stops_cleanup(self):
        op = FakeOperator(refuse_deny=True)
        runner = self._runner(op)
        errors = runner.cleanup()
        assert errors and 'deny' in errors[0]
        assert op.actions == []
        assert runner.agent.actions == []


class WatchOperator:
    def __init__(self):
        self.approved = []
        self.rows = [
            {'id': 1, 'status': 'pending', 'agent_id': 7,
             'session_id': 'e2e-session', 'target_ip': GATEWAY,
             'command': run.WAIT_COMMAND},
            {'id': 2, 'status': 'pending', 'agent_id': 8,
             'session_id': 'foreign', 'target_ip': GATEWAY,
             'command': run.WAIT_COMMAND},
        ]

    def requests(self, session_id):
        return list(self.rows)

    def approve_request(self, rid, approver, session_id):
        self.approved.append(rid)


class TestWatcher:
    def test_it_approves_only_the_own_row(self):
        op = WatchOperator()
        approver = run.Approver(7, 'e2e-session', GATEWAY)
        waiter = run.Waiter(op, approver, 'e2e-session', run.WAIT_COMMAND)
        waiter.start()
        time.sleep(0.9)
        waiter.stop()
        waiter.join(timeout=3)
        assert op.approved == [1]
        assert not waiter.error


class TestOperatorRealPath:
    def _setup(self):
        from db.agent_tokens import create_agent_token
        from db.agent_policies import set_agent_policy
        from db.approver_tokens import create_approver_token
        from db.gateways import register_gateway
        from db.requests import create_request
        from db.sessions import get_session, upsert_session

        register_gateway(GATEWAY, 'web01.example.test', 'v6.0.0')
        _raw, aid = create_agent_token('e2e-agent')
        set_agent_policy(aid, tools_mode='allowlist', tools=[],
                         approval={}, ssh_hosts_allow=[GATEWAY],
                         ssh_mode='normal', test_agent=True)
        raw, _tid = create_approver_token(aid, [run.JIT_COMMAND], GATEWAY)
        upsert_session('e2e-session', agent='e2e-agent', agent_id=aid,
                       goal='E2E: smoke')
        own = create_request(GATEWAY, run.JIT_COMMAND, session_id='e2e-session',
                             agent='e2e-agent', agent_id=aid)
        _r2, other_id = create_agent_token('other-agent')
        upsert_session('other-session', agent='other-agent',
                       agent_id=other_id, goal='E2E: other')
        other = create_request(GATEWAY, run.JIT_COMMAND,
                               session_id='other-session',
                               agent='other-agent', agent_id=other_id)
        return raw, aid, own, other

    def test_the_approver_token_moves_only_its_own_row(self, client):
        raw, aid, own, other = self._setup()

        class ClientHttp:
            def request(self, method, path, body=None):
                response = client.request(
                    method, path, json=body,
                    headers={'Authorization': 'Bearer ' + raw})
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                return response.status_code, payload

        approver = run.Approver(aid, 'e2e-session', GATEWAY)
        operator = run.Operator(ClientHttp())
        operator.approve_request(own, approver, 'e2e-session')

        from db.requests import get_request_row
        assert get_request_row(own)['status'] == 'approved'
        assert get_request_row(other)['status'] == 'pending'
