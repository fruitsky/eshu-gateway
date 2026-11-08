"""Chunk F5 — execution results (exit code, duration, sanitized output tail).

Gateway side: the capture/post path in `eshu-gateway.sh` (FIFO + tail, the
`tee -p` dead-reader guard, the exit-only/off modes, the sentinel staying out
of the capture). Dashboard side: the endpoint, the path/status matching, the
orphan buffer for the `/api/log` race, redaction, caps, retention and the UI
rendering.
"""

import base64
import json
import os
import subprocess
import time

import pytest

from db.exec_results import (
    add_orphan, attach_orphan, cleanup_exec_results, get_result, save_result,
)
from db.requests import create_request, get_all_requests, get_request_row
from db.sessions import get_session_summary, upsert_session

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'fixtures', 'f5_harness.js')
NODE = __import__('shutil').which('node')


def b64(text):
    return base64.b64encode(text.encode()).decode()


def unb64(text):
    return base64.b64decode(text or '').decode('utf-8', 'replace')


def run_node(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


@pytest.fixture
def gw(tmp_path):
    from test_gateway_script import GatewaySandbox
    return GatewaySandbox(tmp_path / 'gw')


def _exec_calls(sandbox):
    return [call for call in sandbox.curl_calls()
            if call['url'].endswith('/api/exec-result')]


def _result_body(sandbox):
    calls = _exec_calls(sandbox)
    assert len(calls) == 1, calls
    return calls[0]['data']


def _wait_result(sandbox):
    assert sandbox.wait_for(lambda calls: any(
        c['url'].endswith('/api/exec-result') for c in calls)), sandbox.curl_calls()


# ── Gateway ─────────────────────────────────────────────────────────────────

class TestGateway:

    def test_f5_gateway_exit_zero_posts_result(self, gw):
        gw.exact('echo hello')
        gw.capture('tail')
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        _wait_result(gw)
        body = _result_body(gw)
        assert body['path'] == 'allowlist'
        assert body['exit_code'] == 0
        assert isinstance(body['duration_ms'], int) and body['duration_ms'] >= 0
        assert unb64(body['encoded_output']).strip() == 'hello'
        assert body['output_truncated'] is False
        assert 'request_id' not in body

    def test_f5_gateway_nonzero_exit_posts_result(self, gw):
        gw.exact('false')
        gw.capture('tail')
        proc = gw.run('false')
        assert proc.returncode == 1
        assert '[ESHU exit=1]' in proc.stdout
        _wait_result(gw)
        body = _result_body(gw)
        assert body['exit_code'] == 1
        # The sentinel is for the agent, not part of the stored output.
        assert '[ESHU exit=1]' not in unb64(body['encoded_output'])

    def test_f5_gateway_tail_is_last_16k(self, gw):
        gw.exact('./bigout')
        gw.capture('tail')
        gw.command_script('bigout', "python3 -c \"print('A' * 20000)\"\n")
        proc = gw.run('./bigout')
        assert proc.returncode == 0
        _wait_result(gw)
        body = _result_body(gw)
        output = unb64(body['encoded_output'])
        assert len(output) == 16384
        assert output.endswith('\n')
        assert set(output.rstrip('\n')) == {'A'}
        assert body['output_truncated'] is True

    def test_f5_gateway_capture_off_posts_nothing(self, gw):
        gw.exact('echo hello')
        gw.capture('off')
        proc = gw.run('echo hello')
        assert proc.stdout.strip() == 'hello'
        time.sleep(0.4)
        assert _exec_calls(gw) == []

    def test_f5_gateway_exit_only_has_no_output(self, gw):
        gw.exact('echo hello')
        gw.capture('exit-only')
        gw.run('echo hello')
        _wait_result(gw)
        body = _result_body(gw)
        assert body['encoded_output'] == ''
        assert body['output_truncated'] is False
        assert body['exit_code'] == 0

    def test_f5_gateway_agent_output_unchanged(self, gw):
        gw.exact('./mixed')
        gw.command_script('mixed', "echo one\necho two >&2\nexit 4\n")
        gw.capture('off')
        off = gw.run('./mixed')
        gw.capture('tail')
        on = gw.run('./mixed')
        assert off.returncode == on.returncode == 4
        assert off.stdout == on.stdout
        assert off.stderr == on.stderr

    def test_f5_gateway_jit_result_carries_request_id(self, gw):
        gw.jit_status('approved')
        gw.capture('exit-only')
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        _wait_result(gw)
        body = _result_body(gw)
        assert body['path'] == 'jit'
        assert body['request_id'] == 123
        assert body['exit_code'] == 0

    def test_f5_gateway_lockbox_result_has_no_request_id(self, gw):
        gw.capture('exit-only')
        gw.tickets('%d|echo hello' % int(time.time()))
        gw.run('echo hello')
        _wait_result(gw)
        body = _result_body(gw)
        assert body['path'] == 'lockbox'
        assert 'request_id' not in body

    def test_f5_gateway_sanitize_before_capture(self, gw):
        gw.exact('echo api_key=SECRETVALUE123')
        gw.capture('tail')
        gw.run('echo api_key=SECRETVALUE123')
        _wait_result(gw)
        output = unb64(_result_body(gw)['encoded_output'])
        assert 'SECRETVALUE123' not in output
        assert '[redacted]' in output.lower()

    def test_f5_gateway_temp_files_removed(self, gw):
        gw.exact('echo hello')
        gw.capture('tail')
        gw.run('echo hello')
        left = [p.name for p in gw.capture_dir.iterdir()]
        assert left == []

    def test_f5_gateway_unsafe_run_dir_disables_capture(self, gw):
        gw.exact('echo hello')
        gw.capture('tail')
        os.chmod(gw.capture_dir, 0o755)
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        assert proc.stdout.strip() == 'hello'
        _wait_result(gw)
        body = _result_body(gw)
        assert body['encoded_output'] == ''
        assert body['exit_code'] == 0

    def test_f5_gateway_bad_capture_value_is_exit_only(self, gw):
        gw.exact('echo hello')
        gw.capture('garbage')
        gw.run('echo hello')
        _wait_result(gw)
        body = _result_body(gw)
        assert body['encoded_output'] == ''
        assert body['exit_code'] == 0

    def test_f5_gateway_dead_reader_keeps_stdout(self, gw):
        """D1: a dead FIFO reader must not truncate the agent's stream."""
        gw.exact('./slowout')
        gw.capture('tail')
        gw.command_script('slowout', "echo one\nsleep 0.4\necho two\nexit 7\n")
        stub = gw.bin / 'tail'
        stub.write_text('#!/bin/bash\n'
                        'case "$*" in\n'
                        '  *16385*) exit 0 ;;\n'
                        'esac\n'
                        'exec /usr/bin/tail "$@"\n')
        stub.chmod(0o755)
        try:
            proc = gw.run('./slowout')
        finally:
            stub.unlink()
        assert proc.returncode == 7
        assert proc.stdout == 'one\ntwo\n[ESHU exit=7]\n'

    def test_f5_gateway_hostile_prefix_posts_nothing(self, gw):
        gw.exact('echo hello')
        gw.capture('tail')
        proc = gw.run('ESHU_SESSION_ID=bad{id} echo hello')
        assert 'LOCKED' in proc.stdout
        time.sleep(0.3)
        assert _exec_calls(gw) == []

    def test_f5_gateway_result_post_failure_is_harmless(self, gw):
        """New gateway against a dashboard without the endpoint: 404/refused."""
        gw.exact('echo hello')
        gw.capture('tail')
        gw.exec_fail(True)
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        assert proc.stdout.strip() == 'hello'
        assert _exec_calls(gw), 'the post is still attempted'


# ── Dashboard ───────────────────────────────────────────────────────────────

def _post(client, headers, command='uptime', **kw):
    body = {
        'target_ip': '10.0.0.1',
        'encoded_command': b64(command),
        'path': 'allowlist',
        'exit_code': 0,
        'duration_ms': 12,
        'encoded_output': b64('ok\n'),
        'output_truncated': False,
    }
    body.update(kw)
    return client.post('/api/exec-result', json=body, headers=headers)


class TestMatching:

    def test_f5_match_allowlist_by_sha(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        response = _post(auth_client, gateway_headers, 'uptime')
        assert response.status_code == 200
        data = response.json()
        assert data['attached'] is True and data['request_id'] == req_id
        assert get_result(req_id)['output'] == 'ok\n'

    def test_f5_match_window_row(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'df -h', status='window-approved', ttl=0)
        _post(auth_client, gateway_headers, 'df -h', path='window')
        assert get_result(req_id) is not None

    def test_f5_lockbox_result_attaches_to_approved(self, auth_client, gateway_headers):
        """D3: the claim-and-burn path runs approved/consumed rows."""
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        response = _post(auth_client, gateway_headers, 'uptime', path='lockbox')
        assert response.status_code == 200
        assert response.json()['attached'] is True
        assert get_result(req_id) is not None

    def test_f5_no_double_attach(self, auth_client, gateway_headers):
        first = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        second = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        assert _post(auth_client, gateway_headers, 'uptime').json()['request_id'] == second
        assert _post(auth_client, gateway_headers, 'uptime').json()['request_id'] == first
        assert get_result(first) and get_result(second)

    def test_f5_unknown_request_id_rejected(self, auth_client, gateway_headers):
        response = _post(auth_client, gateway_headers, request_id=999999)
        assert response.status_code == 400
        assert _post(auth_client, gateway_headers,
                     request_id=999999).json()['detail'] == 'Unknown request'

    def test_f5_foreign_request_id_rejected(self, auth_client, gateway_headers):
        foreign = create_request('10.0.0.2', 'uptime', status='auto-approved', ttl=0)
        assert _post(auth_client, gateway_headers, request_id=foreign).status_code == 400
        assert get_result(foreign) is None

    def test_f5_request_id_must_be_executable(self, auth_client, gateway_headers):
        """D5: a result can never make a denied/pending row look executed."""
        pending = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        response = _post(auth_client, gateway_headers, request_id=pending)
        assert response.status_code == 400
        assert response.json()['detail'] == 'Request is not executable'
        denied = create_request('10.0.0.1', 'uptime', status='denied', ttl=0)
        assert _post(auth_client, gateway_headers, request_id=denied).status_code == 400
        assert get_result(pending) is None and get_result(denied) is None

    def test_f5_duplicate_result_is_idempotent(self, auth_client, gateway_headers):
        """D4: the first stored result wins."""
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        first = _post(auth_client, gateway_headers, request_id=req_id,
                      encoded_output=b64('first\n'))
        assert first.json()['attached'] is True
        second = _post(auth_client, gateway_headers, request_id=req_id,
                       encoded_output=b64('second\n'))
        assert second.status_code == 200
        assert second.json()['attached'] is False
        assert second.json()['reason'] == 'already_has_result'
        assert get_result(req_id)['output'] == 'first\n'

    def test_f5_unknown_path_rejected(self, auth_client, gateway_headers):
        for path in ('bogus', '', 'ALLOWLIST'):
            assert _post(auth_client, gateway_headers, path=path).status_code == 400

    def test_f5_result_before_log_is_orphaned_then_attached(self, auth_client,
                                                           gateway_headers):
        response = _post(auth_client, gateway_headers, 'uptime')
        assert response.status_code == 200
        assert response.json()['attached'] is False
        # /api/log creates the row afterwards and attaches the waiting orphan.
        logged = auth_client.post('/api/log', json={
            'target_ip': '10.0.0.1', 'encoded_command': b64('uptime'),
            'status': 'auto-approved',
        }, headers=gateway_headers)
        assert logged.status_code == 200
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert get_result(row['id'])['exit_code'] == 0

    def test_f5_orphan_cap_per_gateway(self, auth_client, gateway_headers):
        """D6: the orphan table cannot grow without bound."""
        for i in range(60):
            add_orphan('10.0.0.1', 'cmd-%d' % i, 0, 1, 'x')
        from db.core import db_conn
        with db_conn() as conn:
            count = conn.execute(
                'SELECT COUNT(*) AS n FROM exec_result_orphans WHERE target_ip = ?',
                ('10.0.0.1',)).fetchone()['n']
        assert count == 50


class TestStorage:

    def test_f5_scrub_applied(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        _post(auth_client, gateway_headers, request_id=req_id,
              encoded_output=b64('export api_key=SECRETVALUE123\n'))
        stored = get_result(req_id)['output']
        assert 'SECRETVALUE123' not in stored
        assert 'redacted' in stored.lower()

    def test_f5_output_cap_server_side(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        _post(auth_client, gateway_headers, request_id=req_id,
              encoded_output=b64('x' * 20000))
        result = get_result(req_id)
        assert len(result['output']) == 16384
        assert result['truncated'] == 1

    def test_f5_retention_cleanup(self, auth_client, gateway_headers):
        old = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        fresh = create_request('10.0.0.1', 'df -h', status='auto-approved', ttl=0)
        save_result(old, 0, 1, 'old', now=int(time.time()) - 40 * 86400)
        save_result(fresh, 0, 1, 'fresh', now=int(time.time()))
        assert cleanup_exec_results(30) == 1
        assert get_result(old) is None
        assert get_result(fresh) is not None
        # The request rows survive.
        assert get_request_row(old) is not None

    def test_f5_session_summary_failed(self, auth_client, gateway_headers):
        upsert_session('f5-failed', goal='f5')
        ok = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0,
                            session_id='f5-failed')
        bad = create_request('10.0.0.1', 'false', status='auto-approved', ttl=0,
                             session_id='f5-failed')
        save_result(ok, 0, 1, '')
        save_result(bad, 2, 1, '')
        assert get_session_summary('f5-failed')['failed'] == 1


class TestApi:

    def test_f5_output_endpoint_requires_session(self, client):
        req_id = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        save_result(req_id, 0, 5, 'hello\n')
        assert client.get('/api/requests/%d/output' % req_id).status_code in (401, 403)

    def test_f5_output_endpoint_returns_output(self, auth_client):
        req_id = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        save_result(req_id, 0, 5, 'hello\n')
        response = auth_client.get('/api/requests/%d/output' % req_id)
        assert response.status_code == 200
        data = response.json()
        assert data['output'] == 'hello\n' and data['exit_code'] == 0
        other = create_request('10.0.0.1', 'df -h', status='auto-approved', ttl=0)
        assert auth_client.get('/api/requests/%d/output' % other).status_code == 404

    def test_f5_dashboard_session_cannot_post(self, auth_client):
        """D6: the endpoint is gateway-token only."""
        response = auth_client.post('/api/exec-result', json={
            'target_ip': '10.0.0.1', 'encoded_command': b64('uptime'),
            'path': 'allowlist', 'exit_code': 0, 'duration_ms': 1,
        })
        assert response.status_code == 401

    def test_f5_requests_rows_carry_the_result_summary(self, auth_client,
                                                       gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        save_result(req_id, 3, 250, 'out\n', truncated=True)
        row = next(r for r in auth_client.get('/api/requests').json()
                   if r['id'] == req_id)
        assert row['exit_code'] == 3
        assert row['duration_ms'] == 250
        assert row['output_truncated'] == 1

    def test_f5_old_gateway_still_works(self, auth_client, gateway_headers):
        """A dashboard with no results serves the views unchanged."""
        req_id = create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        row = next(r for r in auth_client.get('/api/requests').json()
                   if r['id'] == req_id)
        assert row['exit_code'] is None
        assert auth_client.get('/api/requests/%d/output' % req_id).status_code == 404


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def test_f5_exit_badge_rendered(self):
        ok = run_node({'action': 'badge', 'exit_code': 0, 'duration_ms': 250})
        assert 'exit 0' in ok['html'] and 'exec-ok' in ok['html']
        assert '250 ms' in ok['html']
        bad = run_node({'action': 'badge', 'exit_code': 7, 'duration_ms': 1500})
        assert 'exit 7' in bad['html'] and 'exec-fail' in bad['html']
        assert '1.5 s' in bad['html']
        none = run_node({'action': 'badge', 'exit_code': None})
        assert none['html'] == ''

    def test_f5_output_escaped(self):
        payload = '<img src=x onerror=alert(1)>'
        out = run_node({'action': 'output', 'output': payload, 'truncated': True})
        assert payload not in out['html']
        assert '&lt;img' in out['html']
        assert 'tail of the last 16 KiB' in out['html']
