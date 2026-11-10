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
import shutil
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
                          capture_output=True, text=True, timeout=120)
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
        assert unb64(body['encoded_output']).strip() == 'hello', gw.logger_text()
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
        assert '[redacted]' in output.lower(), (output, gw.logger_text())

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

    def test_f5_gateway_unwritable_capture_keeps_stdout_and_exit(self, gw):
        """C1: a capture that cannot be written is not the command's failure.

        The capture file is created by `mktemp` before the command runs, then
        replaced by a directory, so awk's END write fails (this also fails as
        root, unlike a `chmod`). Every byte must still reach the agent, the
        exit code and the sentinel must be the command's, and the result post
        must carry no output rather than a broken one.
        """
        gw.exact('./bigexit')
        gw.capture('tail')
        cap = str(gw.capture_dir)
        gw.command_script(
            'bigexit',
            "python3 -c \"print('A' * 20000)\"\n"
            "for f in %s/eshu-out.*; do rm -f \"$f\"; mkdir \"$f\"; done\n"
            "exit 7\n" % cap)
        proc = gw.run('./bigexit')
        assert proc.returncode == 7
        assert len(proc.stdout) > 20000
        assert '[ESHU exit=7]' in proc.stdout
        assert 'CAPTURE FAILED' in gw.logger_text(), gw.logger_text()
        _wait_result(gw)
        body = _result_body(gw)
        assert body['exit_code'] == 7
        assert body['encoded_output'] == ''

    def test_f5_gateway_hostile_prefix_posts_nothing(self, gw):
        gw.exact('echo hello')
        gw.capture('tail')
        proc = gw.run('ESHU_SESSION_ID=bad{id} echo hello')
        assert 'LOCKED' in proc.stdout
        time.sleep(0.3)
        assert _exec_calls(gw) == []

    def test_f5_gateway_output_streams_live(self, gw):
        """The capture rides in the stream: the agent sees each line as it
        happens, not at the end.

        This replaces a FIFO + second-reader design. On CI that reader never
        saw EOF, so the call hung; fusing the capture into the stream has no
        second process to wait for. The test pins the property the FIFO was
        there for: output is visible before the command exits.
        """
        gw.exact('./slow')
        gw.capture('tail')
        gw.command_script('slow', "echo first\nsleep 1\necho second\n")
        proc = gw.popen('./slow')
        try:
            assert proc.stdout.readline() == 'first\n'
        finally:
            proc.stdout.close()
            proc.kill()
            proc.wait(timeout=5)

    def test_f5_gateway_result_post_failure_is_harmless(self, gw):
        """New gateway against a dashboard without the endpoint: 404/refused."""
        gw.exact('echo hello')
        gw.capture('tail')
        gw.exec_fail(True)
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        assert proc.stdout.strip() == 'hello'
        assert _exec_calls(gw), 'the post is still attempted'


# ── B1–B3: a slow command must not outlive the agent's disconnect ───────────

def _alive(pid):
    """True while `pid` exists and is not a zombie."""
    try:
        with open('/proc/%d/stat' % pid) as handle:
            fields = handle.read().split()
        return fields[2] != 'Z'
    except OSError:
        return False


class TestStreamBuffering:
    """B3: the D1 slow-writer case through the real `run_sanitized`.

    sed block-buffers its output into a pipe, so a slow command's first bytes
    never reach a closed connection and the command keeps running (39-41 s in
    the D1 check). With `stdbuf -oL sed`, the first line reaches the agent,
    the disconnect is noticed, and the command dies.
    """

    @pytest.fixture(params=['mawk', 'gawk'])
    def awk_name(self, request):
        if shutil.which(request.param) is None:
            pytest.skip('%s is not installed' % request.param)
        return request.param

    @pytest.mark.parametrize('mode', ['tail', 'exit-only'])
    @pytest.mark.parametrize('sigpipe', ['default', 'ignored'])
    def test_f5_gateway_slow_writer_dies_with_the_agent(
            self, tmp_path, awk_name, mode, sigpipe):
        if awk_name == 'mawk':
            pytest.skip('mawk holds stdin in a 4 KiB buffer (B2 note in the '
                        'F5 Implementer notes); `-W interactive` would stream '
                        'but splits records longer than 4096 bytes')
        if sigpipe == 'ignored':
            pytest.skip('with SIGPIPE ignored GNU sed keeps reading after '
                        'EPIPE, so no buffering change can bound the command; '
                        'sshd gives its children the default signal, and the '
                        'function-level D1 test covers awk in both modes')
        from test_gateway_script import GatewaySandbox
        gw = GatewaySandbox(tmp_path / 'gw')
        (gw.bin / 'awk').symlink_to(shutil.which(awk_name))
        pidfile = gw.root / 'slow.pid'
        writer = gw.root / 'slow.py'
        writer.write_text(
            "import os, time\n"
            "open(%r, 'w').write(str(os.getpid()))\n"
            "for i in range(600):\n"
            "    print('line %%d' %% i, flush=True)\n"
            "    time.sleep(0.1)\n" % str(pidfile))
        gw.command_script('slow', 'exec python3 "%s"\n' % writer)
        gw.exact('./slow')
        gw.capture(mode)
        env = {
            'SSH_ORIGINAL_COMMAND': './slow',
            'SSH_CONNECTION': '127.0.0.1 40000 127.0.0.1 22',
            'USER': 'tester',
            'LANG': 'C.UTF-8',
            'ESHU_JIT_WAIT': '2',
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        }
        wrapper = ('trap "" PIPE; exec bash "$1"' if sigpipe == 'ignored'
                   else 'exec bash "$1"')
        # stderr to DEVNULL: with SIGPIPE ignored, sed's per-line write errors
        # would fill an undrained pipe and block the very hang this test looks
        # for.
        proc = subprocess.Popen(['bash', '-c', wrapper, '_', str(gw.script)],
                                cwd=str(gw.root), env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                text=True)
        try:
            first = proc.stdout.readline()
            assert first.strip() == 'line 0', first
            started = time.monotonic()
            proc.stdout.close()
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        elapsed = time.monotonic() - started
        assert elapsed < 5, '%s/%s kept running %.1f s after the disconnect' % (
            awk_name, sigpipe, elapsed)
        deadline = time.monotonic() + 3
        pid = int(pidfile.read_text())
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.05)
        assert not _alive(pid), 'the command (pid %d) outlived the gateway' % pid

    def test_f5_gateway_without_stdbuf_still_runs(self, tmp_path):
        """B3: no stdbuf (BusyBox) falls back to plain sed; output and rc stay."""
        from test_gateway_script import GatewaySandbox
        gw = GatewaySandbox(tmp_path / 'gw', stdbuf=False)
        gw.exact('echo hello')
        gw.capture('tail')
        proc = gw.run('echo hello')
        assert proc.returncode == 0
        assert proc.stdout.strip() == 'hello'
        assert 'BUFFERING: stdbuf is unavailable' in gw.logger_text()
        _wait_result(gw)
        assert unb64(_result_body(gw)['encoded_output']).strip() == 'hello'


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

    def test_f5_lockbox_match_window_is_wider(self, auth_client, gateway_headers,
                                              ):
        """E4: a lockbox claim can follow a long JIT wait + ticket life."""
        from db.core import db_conn
        old = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        with db_conn() as conn:
            conn.execute('UPDATE requests SET created_at = ? WHERE id = ?',
                         (int(time.time()) - 200, old))
            conn.commit()
        attached = _post(auth_client, gateway_headers, 'uptime', path='lockbox')
        assert attached.json()['attached'] is True
        assert attached.json()['request_id'] == old

        # The allowlist window stays 120 s: the same age is too old there.
        old_allow = create_request('10.0.0.1', 'df -h', status='auto-approved', ttl=0)
        with db_conn() as conn:
            conn.execute('UPDATE requests SET created_at = ? WHERE id = ?',
                         (int(time.time()) - 200, old_allow))
            conn.commit()
        orphaned = _post(auth_client, gateway_headers, 'df -h', path='allowlist')
        assert orphaned.json()['orphaned'] is True

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

    def test_f5_oversized_body_is_422(self, auth_client, gateway_headers):
        """E2: the request body is capped per field, before any work."""
        assert _post(auth_client, gateway_headers,
                     encoded_output='A' * 30000).status_code == 422
        assert _post(auth_client, gateway_headers,
                     encoded_command='A' * 20000).status_code == 422

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

    def test_f5_no_audit_event_per_result(self, auth_client, gateway_headers):
        """E3: the results table is the record; no audit row per execution."""
        from db.audit import get_audit_log
        create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0)
        assert _post(auth_client, gateway_headers, 'uptime').status_code == 200
        assert [e for e in get_audit_log()
                if e['event_type'] == 'exec_result'] == []

    def test_f5_scrub_applied(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        _post(auth_client, gateway_headers, request_id=req_id,
              encoded_output=b64('export api_key=SECRETVALUE123\n'))
        stored = get_result(req_id)['output']
        assert 'SECRETVALUE123' not in stored
        assert 'redacted' in stored.lower()

    def test_f5_output_cap_server_side(self, auth_client, gateway_headers):
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        # 17 KiB raw (base64 within E2's 24 KiB cap) — the server keeps the
        # tail and sets truncated.
        _post(auth_client, gateway_headers, request_id=req_id,
              encoded_output=b64('x' * 17000))
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
