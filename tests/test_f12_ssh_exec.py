"""F12 — `ssh_exec`: the dashboard side.

The gateway is stubbed at `core.ssh_exec.run_ssh`, so no SSH, no key
generation and no network: these tests exercise the policy, the signed
prefix, the envelope parser and the F5 hook. The gateway's own half lives in
`tests/test_f12_gateway.py`, which runs the real script in a sandbox.
"""
import base64
import json
import time

import pytest

from core import ssh_crypto, ssh_exec as sx
from db.agent_policies import set_agent_policy
from db.gateways import (
    get_gateway, get_gateway_ssh_capabilities, get_gateway_ssh_host_key,
    get_gateway_ssh_key_derived, register_gateway, set_gateway_ssh_capabilities,
    set_gateway_ssh_host_key, set_gateway_ssh_mode, set_gateway_token,
)
from db.requests import create_request, get_request_row, update_request_status
from db.sessions import upsert_session

IP = '192.0.2.10'
HOST = 'web01.example.test'
TOKEN = 'eshu-test-token'

# A syntactically valid ed25519 public key line (the blob does not have to
# verify: the dashboard only pins and formats it).
HOST_KEY = ('ssh-ed25519 '
            + base64.b64encode(b'0' * 32).decode()
            + ' root@web01')

AGENT = {'id': 1, 'name': 'agent-a'}
OTHER_AGENT = {'id': 2, 'name': 'agent-b'}


def _gateway(ready=True, token=TOKEN):
    register_gateway(IP, HOST, 'v-test')
    set_gateway_token(IP, token)
    if ready:
        set_gateway_ssh_host_key(IP, HOST_KEY)
        set_gateway_ssh_capabilities(IP, sx.AGENT_FORMAT, sx.TICKET_FORMAT)
    return get_gateway(IP)


def _envelope(status='ran', request_id=7, exit_code=0, output=b'hello\n',
              truncated=False, messages=()):
    return sx.ENVELOPE_PREFIX + json.dumps({
        'status': status, 'request_id': request_id, 'exit_code': exit_code,
        'output_b64': base64.b64encode(output).decode(),
        'truncated': truncated, 'messages': list(messages)})


@pytest.fixture
def gw_headers(temp_db):
    """Seed the gateway this module uses and authenticate as it."""
    _gateway()
    return {'X-Gateway-Token': TOKEN}


@pytest.fixture
def stub(monkeypatch):
    """Replace the runner and the key material; record the calls."""
    calls = []
    state = {'envelope': _envelope(), 'rc': 0, 'timed_out': False}

    def fake_run(gateway, command_line, known_hosts, key_path, timeout):
        calls.append({'gateway': gateway, 'command_line': command_line,
                      'timeout': timeout, 'known_hosts': known_hosts})
        return {'rc': state['rc'], 'stdout': state['envelope'],
                'stderr': '', 'timed_out': state['timed_out']}

    monkeypatch.setattr(sx, 'run_ssh', fake_run)
    monkeypatch.setattr(sx.ssh_keys, 'write_known_hosts',
                        lambda ip, key: f'/tmp/kh.{ip}')
    monkeypatch.setattr(sx.ssh_keys, 'ensure_key_pair',
                        lambda: '/tmp/key')
    return {'calls': calls, 'state': state}


class TestSharedVector:
    """The design note's test vector, so the gateway and the dashboard can
    never drift apart silently."""

    def test_derived_key_and_signature(self):
        key = ssh_crypto.derive_key('eshu-test-token')
        assert key == ('04bfe393d32b73020113f6cefcec7ad1825272cfa3b48547e0648c5d2fa041d5')
        canonical = ssh_crypto.canonical(
            'uptime', 'agent-a', session='sess-1', mcp='1', force_jit='0',
            ts='1700000000', nonce='0123456789abcdef', gateway_ip='192.0.2.10')
        assert canonical.splitlines()[0] == 'v1'
        assert canonical.splitlines()[-1] == (
            'dd291cd6294bafef2a7e9c378eb320e87198d6dae214272addb569775750c802')
        assert ssh_crypto.sign(key, canonical) == (
            '8e16581e343844cc2d75453461d3464b4c36cd22ae046573093fa63f93db0b04')

    def test_canonical_refuses_a_bad_field(self):
        with pytest.raises(ValueError):
            ssh_crypto.canonical('uptime', 'agent\nb', session='')
        with pytest.raises(ValueError):
            ssh_crypto.canonical('uptime', 'agent-a', nonce='zz')

    def test_empty_token_has_no_key(self):
        assert ssh_crypto.derive_key('') == ''
        with pytest.raises(ValueError):
            ssh_crypto.sign('', 'v1')


class TestCommandBytes:
    """G3 and the `ESHU_` lookalike rule (G2)."""

    @pytest.mark.parametrize('command', ['uptime', 'df -h', 'a\tb'])
    def test_acceptable(self, command):
        assert sx.command_problem(command) == ''

    @pytest.mark.parametrize('command,reason', [
        (' uptime', 'command-whitespace'),
        ('uptime ', 'command-whitespace'),
        # BK2: a line break is refused up front; it is never signed or sent.
        ('uptime\n', 'multiline_command'),
        ('a\nb', 'multiline_command'),
        ('a\r\nb', 'multiline_command'),
        ('up\rtime', 'multiline_command'),
        ('up\x00time', 'command-control-character'),
        ('', 'empty-command'),
        ('ESHU_AGENT=x uptime', 'command-prefix-lookalike'),
    ])
    def test_refused(self, command, reason):
        assert sx.command_problem(command) == reason

    def test_too_long(self):
        assert sx.command_problem('x' * (sx.MAX_COMMAND + 1)) == 'command-too-long'


class TestHostAndPolicy:
    def test_unknown_host(self, stub):
        _gateway()
        out = sx.ssh_exec('198.51.100.9', 'uptime', agent=AGENT)
        assert out['status'] == 'refused' and out['reason'] == 'unknown_host'

    def test_host_cannot_carry_options(self, stub):
        _gateway()
        for host in ('-o ProxyCommand=x', 'root@192.0.2.10', '192.0.2.10:22'):
            assert sx.ssh_exec(host, 'uptime', agent=AGENT)['reason'] == 'unknown_host'

    def test_hostname_resolves_uniquely(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[HOST], ssh_mode='normal')
        out = sx.ssh_exec(HOST, 'uptime', agent=AGENT)
        assert out['status'] == 'ran'

    def test_no_hosts_means_none(self, stub):
        _gateway()
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['reason'] == 'host_not_allowed'

    def test_wildcard_allows(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=['*'], ssh_mode='normal')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['status'] == 'ran'

    def test_read_only_refuses_a_write(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='read_only')
        out = sx.ssh_exec(IP, 'rm -rf /tmp/x', agent=AGENT)
        assert out['reason'] == 'read-only agent'
        assert stub['calls'] == []

    def test_read_only_allows_a_read(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='read_only')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['status'] == 'ran'

    def test_approval_mode_forces_jit(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='approval')
        sx.ssh_exec(IP, 'uptime', agent=AGENT)
        line = stub['calls'][0]['command_line']
        assert 'ESHU_FORCE_JIT=1' in line
        assert line.split()[0].startswith('ESHU_AGENT=')

    def test_normal_mode_does_not_force_jit(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert 'ESHU_FORCE_JIT=1' not in stub['calls'][0]['command_line']

    def test_session_must_be_owned(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        upsert_session('sess-1', agent='agent-b', goal='theirs')
        out = sx.ssh_exec(IP, 'uptime', session_id='sess-1', agent=AGENT)
        assert out['reason'] == 'session_not_yours'
        out = sx.ssh_exec(IP, 'uptime', session_id='sess-unknown', agent=AGENT)
        assert out['reason'] == 'unknown-session'
        out = sx.ssh_exec(IP, 'uptime', session_id='bad id', agent=AGENT)
        assert out['reason'] == 'invalid-session-id'

    def test_owned_session_is_in_the_prefix(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        upsert_session('sess-1', agent='agent-a', goal='mine')
        sx.ssh_exec(IP, 'uptime', session_id='sess-1', agent=AGENT)
        assert 'ESHU_SESSION_ID=sess-1' in stub['calls'][0]['command_line']


class TestCapabilities:
    """§9: fail closed, and name the reason."""

    def test_no_capability(self, stub):
        _gateway(ready=False)
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['reason'] == 'ssh_not_ready'

    def test_old_agent_format(self, stub):
        _gateway()
        set_gateway_ssh_capabilities(IP, 'ssh_agent_v1', sx.TICKET_FORMAT)
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['reason'] == 'ssh_not_ready'

    def test_old_ticket_format(self, stub):
        _gateway()
        set_gateway_ssh_capabilities(IP, sx.AGENT_FORMAT, 'v1')
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['reason'] == 'ssh_not_ready'

    def test_unpinned_host_key(self, stub):
        _gateway()
        set_gateway_ssh_host_key(IP, '')
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        assert sx.ssh_exec(IP, 'uptime', agent=AGENT)['reason'] == 'ssh_host_unpinned'

    def test_unprovisioned_key(self):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        # Simulate a row created before F12 whose key could not be derived.
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("UPDATE gateways SET ssh_key_derived = '' WHERE ip = ?", (IP,))
            conn.commit()
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['reason'] == 'ssh_key_unprovisioned'


class TestDerivedKey:
    """G1: derived on set/rotate, stored, never returned."""

    def test_set_token_derives(self, temp_db):
        _gateway()
        assert get_gateway_ssh_key_derived(IP) == ssh_crypto.derive_key(TOKEN)

    def test_rotation_changes_the_key(self, stub, temp_db):
        _gateway()
        before = get_gateway_ssh_key_derived(IP)
        set_gateway_token(IP, 'rotated-token')
        after = get_gateway_ssh_key_derived(IP)
        assert before and after and before != after

    def test_signature_changes_with_rotation(self, stub, temp_db):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        sx.ssh_exec(IP, 'uptime', agent=AGENT)
        first = stub['calls'][0]['command_line']
        set_gateway_token(IP, 'rotated-token')
        sx.ssh_exec(IP, 'uptime', agent=AGENT)
        second = stub['calls'][1]['command_line']
        assert first.split()[1] != second.split()[1]

    def test_key_is_not_in_the_register_response(self, client, gw_headers):
        _gateway()
        r = client.post('/api/register', headers=gw_headers,
                        json={'ip': IP, 'hostname': HOST, 'version': 'v-test'})
        assert r.status_code == 200
        assert 'ssh_key' not in r.text and get_gateway_ssh_key_derived(IP) not in r.text

    def test_backfill_from_a_plaintext_token(self, temp_db):
        # A pre-F12 row: token set directly, no derived key.
        from db.core import db_conn
        register_gateway(IP, HOST, 'v-test')
        with db_conn() as conn:
            conn.execute("UPDATE gateways SET api_token = ?, ssh_key_derived = '' WHERE ip = ?",
                         (TOKEN, IP))
            conn.commit()
        from db.gateways import init_gateways_tables
        with db_conn() as conn:
            init_gateways_tables(conn.cursor())
            conn.commit()
        assert get_gateway_ssh_key_derived(IP) == ssh_crypto.derive_key(TOKEN)


class TestEnvelope:
    def test_one_envelope(self):
        out = sx.parse_envelope(_envelope() + '\n')
        assert out['status'] == 'ran' and out['request_id'] == 7

    @pytest.mark.parametrize('stdout', [
        '',
        'boom\n',
        _envelope() + _envelope(),
        _envelope() + 'extra\n',
        'ESHU-MCP-ENVELOPE not-json\n',
        'ESHU-MCP-ENVELOPE {"status":"ran"}\n',
        'ESHU-MCP-ENVELOPE {"status":"ran","request_id":"1","exit_code":0,'
        '"output_b64":"","truncated":false,"messages":[]}\n',
        'ESHU-MCP-ENVELOPE {"status":"weird","request_id":1,"exit_code":0,'
        '"output_b64":"","truncated":false,"messages":[]}\n',
        'ESHU-MCP-ENVELOPE {"status":"ran","request_id":1,"exit_code":0,'
        '"output_b64":"","truncated":false,"messages":"x"}\n',
    ])
    def test_bad_envelope(self, stdout):
        assert sx.parse_envelope(stdout)['error'] == 'bad-envelope'

    def test_request_id_is_an_integer(self):
        out = sx.parse_envelope(_envelope(request_id=123))
        assert out['request_id'] == 123 and isinstance(out['request_id'], int)

    def test_output_is_capped_and_scrubbed(self):
        big = b'x' * (sx.MAX_OUTPUT + 100)
        out = sx.decode_output({'output_b64': base64.b64encode(big).decode(),
                                'truncated': False})
        assert len(out['output'].encode()) == sx.MAX_OUTPUT
        assert out['truncated'] is True
        secret = b'API_KEY=supersecretvalue'
        out = sx.decode_output({'output_b64': base64.b64encode(secret).decode(),
                                'truncated': False})
        assert 'supersecretvalue' not in out['output']

    def test_bad_base64(self):
        assert sx.decode_output({'output_b64': '***', 'truncated': False})['error'] == 'bad-envelope'


class TestFlow:
    def test_ran_stores_the_f5_result(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['status'] == 'ran' and out['output'] == 'hello\n'
        from db.exec_results import get_result
        row = get_result(7)
        assert row['output'] == 'hello\n' and row['exit_code'] == 0

    def test_nonzero_exit_is_a_field(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        stub['state']['envelope'] = _envelope(exit_code=2, output=b'')
        out = sx.ssh_exec(IP, 'false', agent=AGENT)
        assert out['status'] == 'ran' and out['exit_code'] == 2

    def test_pending(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='approval')
        stub['state']['envelope'] = _envelope(status='pending', request_id=5,
                                              messages=['waiting for a human'])
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['status'] == 'pending' and out['request_id'] == 5
        assert 'waiting' in out['reason']

    def test_pending_wait_times_out_to_pending(self, stub, monkeypatch):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='approval')
        stub['state']['envelope'] = _envelope(status='pending', request_id=5)
        monkeypatch.setattr(sx, 'WAIT_SECONDS', 0.05)
        out = sx.ssh_exec(IP, 'uptime', wait=True, agent=AGENT)
        assert out['status'] == 'pending'

    def test_wait_reruns_once_consumed(self, stub, monkeypatch):
        """M2: `consumed` means the poller took the ticket into the lockbox."""
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='approval')
        stub['state']['envelope'] = _envelope(status='pending', request_id=5)
        monkeypatch.setattr(sx, 'WAIT_SECONDS', 2)
        monkeypatch.setattr(sx, 'POLL_INTERVAL', 0.01)
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'consumed')

        def run(gateway, command_line, known_hosts, key_path, timeout):
            stub['calls'].append({'command_line': command_line})
            if len(stub['calls']) == 1:
                return {'rc': 0, 'stdout': _envelope(status='pending', request_id=req),
                        'stderr': '', 'timed_out': False}
            return {'rc': 0, 'stdout': _envelope(status='ran', request_id=req,
                                                 output=b'up\n'),
                    'stderr': '', 'timed_out': False}
        monkeypatch.setattr(sx, 'run_ssh', run)
        out = sx.ssh_exec(IP, 'uptime', wait=True, agent=AGENT)
        assert out['status'] == 'ran' and out['output'] == 'up\n'
        assert len(stub['calls']) == 2

    def test_wait_does_not_rerun_while_approved(self, stub, monkeypatch):
        """M2: `approved` is not "the ticket is waiting".

        The row is approved when the operator presses the button; the ticket
        reaches the lockbox only on the poller's next poll (which also flips
        the row to `consumed`). Re-running at `approved` races that poll,
        finds no ticket and files a second JIT request — the operator is
        asked twice and the first ticket expires unused.
        """
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='approval')
        stub['state']['envelope'] = _envelope(status='pending', request_id=5)
        monkeypatch.setattr(sx, 'WAIT_SECONDS', 1)
        monkeypatch.setattr(sx, 'POLL_INTERVAL', 0.01)
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'approved')
        out = sx.ssh_exec(IP, 'uptime', wait=True, agent=AGENT)
        assert out['status'] == 'pending'
        assert len(stub['calls']) == 1          # no second signed call
        assert get_request_row(req)['status'] == 'approved'

    def test_refused_returns_the_reason(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        stub['state']['envelope'] = _envelope(
            status='refused', request_id=0, messages=['The fleet is FROZEN'])
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['status'] == 'refused' and 'FROZEN' in out['reason']

    def test_plain_text_refusal_is_not_a_success(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        stub['state']['envelope'] = '[LOCKED] Invalid ESHU_TS\n'
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['status'] == 'refused' and out['reason'] == 'bad-envelope'

    def test_timeout(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        stub['state']['timed_out'] = True
        out = sx.ssh_exec(IP, 'uptime', agent=AGENT)
        assert out['reason'] == 'timeout'

    def test_no_agent(self):
        assert sx.ssh_exec(IP, 'uptime', agent=None)['reason'] == 'no_agent'

    def test_audit_row_hides_the_command(self, stub):
        _gateway()
        set_agent_policy(1, ssh_hosts_allow=[IP], ssh_mode='normal')
        sx.ssh_exec(IP, 'echo topsecret', agent=AGENT)
        from db.audit import get_audit_log
        rows = [r for r in get_audit_log(20) if r['event_type'] == 'ssh_exec']
        assert rows
        assert all('topsecret' not in (r['details'] or '') for r in rows)


    def test_a_gateway_whose_ip_is_not_an_ip_is_refused(self, stub, monkeypatch):
        """S4: build_argv must never see a host that starts with `-`.

        The IP is validated on registration; this is the last check before the
        ssh argv is built, so a hand-edited row cannot inject an option.
        """
        set_agent_policy(1, ssh_hosts_allow=['*'], ssh_mode='normal')
        fake = {'ip': 'gateway.example.test', 'hostname': 'web01',
                'ssh_agent_format': sx.AGENT_FORMAT,
                'ssh_ticket_format': sx.TICKET_FORMAT}
        monkeypatch.setattr(sx, 'resolve_gateway', lambda host: fake)
        monkeypatch.setattr(sx, 'get_gateway_ssh_host_key', lambda ip: HOST_KEY)
        monkeypatch.setattr(sx, 'get_gateway_ssh_key_derived', lambda ip: 'ab' * 32)
        out = sx.ssh_exec('anything', 'uptime', agent=AGENT)
        assert out['status'] == 'refused'
        assert out['reason'] == 'invalid_gateway_ip'
        assert stub['calls'] == []


class TestRunnerArgv:
    def test_argv_is_fixed_and_never_a_shell(self):
        gateway = {'ip': IP}
        argv = sx.build_argv(gateway, 'ESHU_MCP=1 uptime', '/kh', '/key')
        assert argv[0] == 'ssh' and argv[-2] == IP
        assert argv[-1] == 'ESHU_MCP=1 uptime'
        assert '-F' in argv and '/dev/null' in argv
        assert 'StrictHostKeyChecking=yes' in argv
        assert 'GlobalKnownHostsFile=/dev/null' in argv
        assert any(a.startswith('UserKnownHostsFile=') for a in argv)
        assert IP in argv and HOST not in argv

    def test_timeout_kills_the_process_group(self, monkeypatch):
        events = []

        class FakeProc:
            pid = 4242
            returncode = -9

            def __init__(self, argv, **kwargs):
                events.append(('argv', argv, kwargs))

            def communicate(self, timeout=None):
                if timeout is not None and not events.count('timeout'):
                    events.append('timeout')
                    raise __import__('subprocess').TimeoutExpired('ssh', timeout)
                return 'out', 'err'

        monkeypatch.setattr(sx.subprocess, 'Popen', FakeProc)
        killed = []
        monkeypatch.setattr(sx.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
        out = sx.run_ssh({'ip': IP}, 'cmd', '/kh', '/key', 1)
        assert out['timed_out'] is True and out['rc'] == 124
        assert killed and killed[0][0] == 4242
        assert events[0][2].get('shell') is None      # never through a shell
        assert events[0][2]['start_new_session'] is True


class TestForceJitOnTheDashboard:
    """§6: the dashboard half — every auto path is skipped."""

    def _request(self, client, headers, **extra):
        body = {'target_ip': IP, 'encoded_command': base64.b64encode(
            b'uptime').decode(), 'agent': 'agent-a', 'force_jit': True}
        body.update(extra)
        return client.post('/api/request', headers=headers, json=body)

    def test_force_jit_stays_pending_despite_an_allowlist(self, client,
                                                          gw_headers, temp_db):
        from db.policies import update_policy
        update_policy('exact_whitelist', 'uptime')
        r = self._request(client, gw_headers)
        assert r.status_code == 200
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_force_jit_stays_pending_despite_a_host_override(
            self, client, gw_headers, temp_db):
        from db.gateways import set_override
        set_override(IP, int(time.time()) + 600, 'maintenance')
        r = self._request(client, gw_headers)
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_force_jit_stays_pending_despite_a_session_override(
            self, client, gw_headers, temp_db):
        from db.overrides import create_session_override
        create_session_override('sess-1', '', int(time.time()) + 600, 'x')
        r = self._request(client, gw_headers, session_id='sess-1')
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_agent_attribution_is_recorded(self, client, gw_headers, temp_db):
        r = self._request(client, gw_headers)
        assert get_request_row(r.json()['request_id'])['agent'] == 'agent-a'

    def test_invalid_agent_is_rejected(self, client, gw_headers, temp_db):
        body = {'target_ip': IP, 'encoded_command': '',
                'agent': 'bad agent', 'force_jit': True}
        assert client.post('/api/request', headers=gw_headers,
                           json=body).status_code == 400

    def test_an_unknown_agent_fails_closed_to_approval(
            self, client, gw_headers, temp_db):
        """M3: an agent with no policy row cannot be vouched for."""
        from db.policies import update_policy
        update_policy('exact_whitelist', 'uptime')
        body = {'target_ip': IP,
                'encoded_command': base64.b64encode(b'uptime').decode(),
                'agent': 'ghost-agent', 'force_jit': False}
        r = client.post('/api/request', headers=gw_headers, json=body)
        assert r.status_code == 200
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_a_policy_error_fails_closed_to_approval(
            self, client, gw_headers, temp_db, monkeypatch):
        from db.policies import update_policy
        update_policy('exact_whitelist', 'uptime')
        import main as main_module

        def boom(*args, **kwargs):
            raise RuntimeError('policy store unavailable')

        monkeypatch.setattr(main_module, 'get_agent_by_name', boom)
        body = {'target_ip': IP,
                'encoded_command': base64.b64encode(b'uptime').decode(),
                'agent': 'agent-a', 'force_jit': False}
        r = client.post('/api/request', headers=gw_headers, json=body)
        assert r.status_code == 200
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_an_unset_ssh_mode_fails_closed_to_approval(
            self, client, gw_headers, temp_db, monkeypatch):
        from db.agent_tokens import create_agent_token, get_agent_by_name
        from db.policies import update_policy
        update_policy('exact_whitelist', 'uptime')
        create_agent_token('agent-a')
        row = get_agent_by_name('agent-a')
        set_agent_policy(row['id'], ssh_hosts_allow=[IP], ssh_mode='normal')
        import main as main_module
        # A policy row without the field (an old row, a partial mapping).
        monkeypatch.setattr(main_module, 'get_agent_policy',
                            lambda agent_id: {})
        body = {'target_ip': IP,
                'encoded_command': base64.b64encode(b'uptime').decode(),
                'agent': 'agent-a', 'force_jit': False}
        r = client.post('/api/request', headers=gw_headers, json=body)
        assert r.status_code == 200
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'

    def test_approval_mode_forces_jit_from_the_policy(self, client,
                                                      gw_headers, temp_db):
        from db.agent_tokens import create_agent_token
        token = create_agent_token('agent-a')
        maybe = token['token'] if isinstance(token, dict) else token
        from db.agent_tokens import get_agent_by_name
        row = get_agent_by_name('agent-a')
        set_agent_policy(row['id'], ssh_hosts_allow=[IP], ssh_mode='approval')
        body = {'target_ip': IP,
                'encoded_command': base64.b64encode(b'uptime').decode(),
                'agent': 'agent-a', 'force_jit': False}
        r = client.post('/api/request', headers=gw_headers, json=body)
        assert r.status_code == 200
        assert get_request_row(r.json()['request_id'])['status'] == 'pending'
        assert maybe


class TestTickets:
    """G5: the tag, the agent binding and the compatibility rules."""

    def _row(self, req):
        from db.core import db_conn
        with db_conn() as conn:
            return conn.execute('SELECT * FROM requests WHERE id = ?',
                                (req,)).fetchone()

    def test_v2_line_shape(self, temp_db):
        from db.requests import ticket_line
        register_gateway(IP, HOST, 'v-test')
        req = create_request(IP, 'a | b', agent='agent-a', session_id='sess-1')
        line = ticket_line(self._row(req), 'v2')
        assert line.startswith('v2|')
        assert line.endswith('|a | b')
        assert line.split('|')[2] == 'agent-a'

    def test_old_line_shape(self, temp_db):
        from db.requests import ticket_line
        register_gateway(IP, HOST, 'v-test')
        req = create_request(IP, 'a | b')
        line = ticket_line(self._row(req), '')
        assert not line.startswith('v2|')
        ts, command = line.split('|', 1)
        assert ts.isdigit() and command == 'a | b'

    def _old_gateway(self):
        register_gateway(IP, HOST, 'v-test')
        set_gateway_token(IP, TOKEN)

    def test_an_old_gateway_gets_the_old_line_from_both_call_sites(
            self, client, temp_db):
        """M1: an old gateway splits on the first `|` and expects `ts|cmd`."""
        self._old_gateway()
        poll_req = create_request(IP, 'uptime')
        update_request_status(poll_req, 'approved')
        headers = {'X-Gateway-Token': TOKEN}
        ticket = client.get(f'/api/poll/{IP}', headers=headers).json()['ticket']
        assert ticket.split('|', 1)[1] == 'uptime'
        assert ticket.split('|', 1)[0].isdigit()

        req = create_request(IP, 'uptime')
        update_request_status(req, 'approved')
        ticket = client.get(f'/api/ticket/{req}', headers=headers).json()['ticket']
        assert ticket.split('|', 1)[1] == 'uptime'
        assert not ticket.startswith('v2|')

    def test_a_v2_gateway_gets_v2_from_both_call_sites(self, client, temp_db):
        _gateway()
        headers = {'X-Gateway-Token': TOKEN}
        poll_req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(poll_req, 'approved')
        assert client.get(f'/api/poll/{IP}', headers=headers).json()[
            'ticket'].startswith('v2|')
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'approved')
        assert client.get(f'/api/ticket/{req}', headers=headers).json()[
            'ticket'].startswith('v2|')

    def test_an_agent_row_on_an_old_gateway_is_refused(self, client, temp_db):
        """M1: the old format has no agent field, so an unsigned caller could
        claim it. Refuse (and deny) instead of delivering."""
        self._old_gateway()
        headers = {'X-Gateway-Token': TOKEN}
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'approved')
        assert client.get(f'/api/poll/{IP}', headers=headers).json()['ticket'] is None
        assert get_request_row(req)['status'] == 'denied'

    def test_the_ticket_endpoint_refuses_an_agent_row_on_an_old_gateway(
            self, client, temp_db):
        self._old_gateway()
        headers = {'X-Gateway-Token': TOKEN}
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'approved')
        assert client.get(f'/api/ticket/{req}', headers=headers).json()['ticket'] is None
        assert get_request_row(req)['status'] == 'denied'
        from db.core import db_conn
        with db_conn() as conn:
            events = [r['event_type'] for r in
                      conn.execute('SELECT event_type FROM audit_log').fetchall()]
        assert 'jit_refused' in events

    def test_ticket_endpoint_returns_v2(self, client, gw_headers, temp_db):
        req = create_request(IP, 'uptime', session_id='sess-1', agent='agent-a')
        update_request_status(req, 'approved')
        r = client.get(f'/api/ticket/{req}', headers=gw_headers)
        assert r.status_code == 200
        ticket = r.json()['ticket']
        assert ticket.startswith('v2|') and ticket.endswith('|uptime')

    def test_poll_returns_v2(self, client, gw_headers, temp_db):
        req = create_request(IP, 'uptime', agent='agent-a')
        update_request_status(req, 'approved')
        r = client.get(f'/api/poll/{IP}', headers=gw_headers)
        assert r.json()['ticket'].startswith('v2|')


class TestEndpoints:
    def test_ssh_key_is_public(self, client, temp_db, monkeypatch, tmp_path):
        from core import ssh_keys
        monkeypatch.setenv('ESHU_SSH_KEY_DIR', str(tmp_path / 'ssh'))
        monkeypatch.setattr(ssh_keys, 'ensure_key_pair',
                            lambda: ssh_keys.private_key_path())
        (tmp_path / 'ssh').mkdir(mode=0o700)
        (tmp_path / 'ssh' / 'dashboard_ed25519.pub').write_text(HOST_KEY + '\n')
        r = client.get('/api/ssh-key')
        assert r.status_code == 200
        assert r.json()['public_key'].startswith('ssh-ed25519 ')
        assert r.json()['fingerprint'].startswith('SHA256:')

    def test_ssh_mode_endpoint(self, client, auth_client, temp_db):
        _gateway()
        r = auth_client.put(f'/api/gateways/{IP}/ssh-mode', json={'mode': 'mcp_only'})
        assert r.json()['ssh_mode'] == 'mcp_only'
        r = auth_client.put(f'/api/gateways/{IP}/ssh-mode', json={'mode': 'nonsense'})
        assert r.json()['ssh_mode'] == 'direct+mcp'

    def test_repin_is_audited(self, client, auth_client, temp_db):
        _gateway()
        new_key = 'ssh-ed25519 ' + base64.b64encode(b'1' * 32).decode()
        r = auth_client.post(f'/api/gateways/{IP}/ssh-host-key',
                             json={'ssh_host_key': new_key})
        assert r.status_code == 200
        assert get_gateway_ssh_host_key(IP) == new_key
        from db.audit import get_audit_log
        assert any(row['event_type'] == 'ssh_host_key_repinned'
                   for row in get_audit_log(20))

    def test_repin_rejects_garbage(self, client, auth_client, temp_db):
        _gateway()
        r = auth_client.post(f'/api/gateways/{IP}/ssh-host-key',
                             json={'ssh_host_key': 'not a key'})
        assert r.status_code == 400

    def test_register_pins_and_stores_capabilities(self, client, gw_headers,
                                                   temp_db):
        register_gateway(IP, HOST, 'v-test')
        body = {'ip': IP, 'hostname': HOST, 'version': 'v-test',
                'ssh_host_key': HOST_KEY, 'ssh_agent_format': sx.AGENT_FORMAT,
                'ssh_ticket_format': sx.TICKET_FORMAT}
        assert client.post('/api/register', headers=gw_headers,
                           json=body).status_code == 200
        assert get_gateway_ssh_host_key(IP) == HOST_KEY
        assert get_gateway_ssh_capabilities(IP)['ssh_ticket_format'] == 'v2'

    def test_register_never_re_pins_a_changed_key(self, client, gw_headers,
                                                  temp_db):
        _gateway()
        other = 'ssh-ed25519 ' + base64.b64encode(b'2' * 32).decode()
        body = {'ip': IP, 'hostname': HOST, 'version': 'v-test',
                'ssh_host_key': other}
        assert client.post('/api/register', headers=gw_headers,
                           json=body).status_code == 200
        assert get_gateway_ssh_host_key(IP) == HOST_KEY
        from db.audit import get_audit_log
        assert any(row['event_type'] == 'ssh_host_key_changed'
                   for row in get_audit_log(20))

    def test_heartbeat_never_moves_the_pin(self, client, gw_headers, temp_db):
        _gateway()
        other = 'ssh-ed25519 ' + base64.b64encode(b'3' * 32).decode()
        body = {'ip': IP, 'hostname': HOST, 'ssh_host_key': other}
        assert client.post('/api/gateway-heartbeat', headers=gw_headers,
                           json=body).status_code == 200
        assert get_gateway_ssh_host_key(IP) == HOST_KEY


class TestMcpTool:
    def test_registered(self, temp_db):
        import asyncio
        from core.mcp_server import mcp
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        assert 'ssh_exec' in names

    def test_call_returns_json(self, temp_db, monkeypatch):
        import asyncio
        from core import mcp_server
        monkeypatch.setattr('core.ssh_exec.ssh_exec',
                            lambda *a, **k: {'status': 'refused', 'reason': 'no_agent'})
        out = asyncio.run(mcp_server.mcp.call_tool('ssh_exec', {'host': IP,
                                                                'command': 'uptime'}))
        text = out[0][0].text if isinstance(out, tuple) else str(out)
        assert 'no_agent' in text
