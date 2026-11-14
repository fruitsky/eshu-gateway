"""F19: time and output limits for unattended runs.

Two halves, tested where each lives:

* the gateway's limit parsing and the /run directory check, taken from the
  shipped script text (so the code under test is the code that ships);
* the dashboard's unattended approval, the limit it carries on the three
  responses, the poller's exclusion and the operator settings endpoint.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "dashboard" / "eshu-gateway.sh"


def _function_text(name: str) -> str:
    """The exact text of a top-level bash function in the shipped script."""
    text = SCRIPT.read_text()
    start = text.index(name + "() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _bash(body: str, env=None):
    full = dict(os.environ)
    full.update({'LANG': 'C.UTF-8'})
    if env:
        full.update(env)
    return subprocess.run(['bash', '-c', body], capture_output=True, text=True,
                          env=full, timeout=60)


# ── the gateway's parser (L2) ────────────────────────────────────────────────

PARSE = _function_text('parse_run_limit')


def _parse(response: str):
    body = (
        'RUN_LIMIT_SECS=""; RUN_LIMIT_BYTES=""; RUN_LIMIT_STATE="absent"\n'
        + PARSE + '\n'
        'parse_run_limit "$1"; rc=$?\n'
        'printf "%s|%s|%s|%s\\n" "$rc" "$RUN_LIMIT_STATE" '
        '"$RUN_LIMIT_SECS" "$RUN_LIMIT_BYTES"\n')
    return _bash(body, {'RESP': response} if False else None) if False else \
        _bash('set -- "$0" "$1"\n' if False else body.replace(
            'parse_run_limit "$1"; rc=$?', 'parse_run_limit "$BODY"; rc=$?'),
            {'BODY': response})


def test_absent_limit_leaves_the_state_as_it_was():
    out = _parse('{"status":"ok","request_id":1}')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == '0|absent||'


def test_a_valid_limit_is_stored():
    out = _parse('{"run_limit":{"seconds":60,"bytes":1048576}}')
    assert out.stdout.strip() == '0|valid|60|1048576'


def test_a_later_absent_field_does_not_clear_it():
    body = (PARSE + '\n'
            'parse_run_limit \'{"run_limit":{"seconds":60,"bytes":1048576}}\'\n'
            'parse_run_limit \'{"status":"ok"}\'\n'
            'printf "%s|%s\\n" "$RUN_LIMIT_SECS" "$RUN_LIMIT_BYTES"\n')
    out = _bash(body)
    assert out.stdout.strip() == '60|1048576'


def test_two_limits_combine_to_the_smaller():
    body = (PARSE + '\n'
            'parse_run_limit \'{"run_limit":{"seconds":60,"bytes":1048576}}\'\n'
            'parse_run_limit \'{"run_limit":{"seconds":30,"bytes":2097152}}\'\n'
            'printf "%s|%s\\n" "$RUN_LIMIT_SECS" "$RUN_LIMIT_BYTES"\n')
    out = _bash(body)
    assert out.stdout.strip() == '30|1048576'


@pytest.mark.parametrize('response', [
    '{"run_limit":{"seconds":"60","bytes":1048576}}',
    '{"run_limit":{"seconds":true,"bytes":1048576}}',
    '{"run_limit":{"seconds":4,"bytes":1048576}}',
    '{"run_limit":{"seconds":60,"bytes":65535}}',
    '{"run_limit":{"seconds":301,"bytes":1048576}}',
    '{"run_limit":{"seconds":60,"bytes":8388609}}',
    '{"run_limit":[]}',
    'not json',
])
def test_a_present_but_unusable_limit_is_invalid(response):
    out = _parse(response)
    assert out.returncode == 0  # the function reports, it does not exit
    assert out.stdout.strip().startswith('1|invalid'), out.stdout


# ── the /run directory check (W1) ───────────────────────────────────────────

RUN_DIR_SAFE = _function_text('run_dir_safe')


def _dir_safe(path: Path):
    body = (
        'ESHU_RUN_DIR="$DIR"; ESHU_RUN_OWNER="%d"\n' % os.getuid()
        + RUN_DIR_SAFE + '\n'
        'run_dir_safe && echo safe || echo unsafe\n')
    return _bash(body, {'DIR': str(path)}).stdout.strip()


def test_a_private_dir_owned_by_us_is_safe(tmp_path):
    d = tmp_path / 'run'
    d.mkdir(mode=0o700)
    assert _dir_safe(d) == 'safe'


def test_a_world_readable_dir_is_unsafe(tmp_path):
    d = tmp_path / 'run'
    d.mkdir(mode=0o755)
    assert _dir_safe(d) == 'unsafe'


def test_a_symlinked_dir_is_unsafe(tmp_path):
    real = tmp_path / 'real'
    real.mkdir(mode=0o700)
    link = tmp_path / 'run'
    link.symlink_to(real)
    assert _dir_safe(link) == 'unsafe'


def test_a_missing_dir_is_unsafe(tmp_path):
    assert _dir_safe(tmp_path / 'nope') == 'unsafe'


def test_a_foreign_owner_is_unsafe(tmp_path):
    d = tmp_path / 'run'
    d.mkdir(mode=0o700)
    body = ('ESHU_RUN_DIR="$DIR"; ESHU_RUN_OWNER="%d"\n' % (os.getuid() + 1)
            + RUN_DIR_SAFE + '\n'
            'run_dir_safe && echo safe || echo unsafe\n')
    out = _bash(body, {'DIR': str(d)}).stdout.strip()
    assert out == 'unsafe'


# ── the dashboard side ──────────────────────────────────────────────────────

def test_the_defaults_and_a_corrupt_setting():
    from db.misc import get_run_limit_settings, set_meta_json
    from db.misc import RUN_LIMIT_META_KEY

    assert get_run_limit_settings() == {'seconds': 60, 'bytes': 1048576}
    set_meta_json(RUN_LIMIT_META_KEY, {'seconds': 'x', 'bytes': -1})
    assert get_run_limit_settings() == {'seconds': 60, 'bytes': 1048576}
    set_meta_json(RUN_LIMIT_META_KEY, {'seconds': 30, 'bytes': 65536})
    assert get_run_limit_settings() == {'seconds': 30, 'bytes': 65536}


def test_an_unattended_row_is_approved_with_a_limit(temp_db):
    from main import approve_unattended
    from db.requests import get_request_row

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '',
                             '', 'agent-a', 'v1')
    assert out['approved'] is True and out['unattended'] is True
    assert out['run_limit'] == {'seconds': 60, 'bytes': 1048576}
    row = get_request_row(out['request_id'])
    assert row['status'] == 'approved' and row['unattended'] == 1
    assert json.loads(row['run_limit']) == {'seconds': 60, 'bytes': 1048576}


def test_a_gateway_without_the_capability_gets_a_pending_row(temp_db):
    from main import approve_unattended
    from db.audit import get_audit_log
    from db.requests import get_request_row

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '',
                             '', 'agent-a', '')
    assert 'approved' not in out
    row = get_request_row(out['request_id'])
    assert row['status'] == 'pending'
    assert row['reason'] == 'read-lane-no-limits'
    assert row['unattended'] == 0
    assert any(e['event_type'] == 'read_lane_no_limits' for e in get_audit_log())


def test_the_poller_never_receives_an_unattended_row(temp_db):
    from main import approve_unattended
    from db.requests import update_ticket_consumed_by_ip

    approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                       'agent-a', 'v1')
    assert update_ticket_consumed_by_ip('10.0.0.1') is None


def _agent_ticket_gateway():
    from db.gateways import register_gateway, set_gateway_ssh_capabilities
    register_gateway('10.0.0.1', 'test-gateway', 'v15.3')
    set_gateway_ssh_capabilities('10.0.0.1', 'eshu-ssh-agent-v1', 'v2')


def test_the_ticket_carries_the_limit(temp_db):
    from main import approve_unattended
    from db.requests import get_ticket_by_request_id
    _agent_ticket_gateway()

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                             'agent-a', 'v1')
    ticket = get_ticket_by_request_id(out['request_id'])
    assert ticket['unattended'] == 1
    assert json.loads(ticket['run_limit']) == {'seconds': 60, 'bytes': 1048576}


def test_the_status_and_ticket_endpoints_carry_it(temp_db, client, gateway_headers):
    from main import approve_unattended
    _agent_ticket_gateway()

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                             'agent-a', 'v1')
    rid = out['request_id']
    status = client.get(f'/api/request_status/{rid}', headers=gateway_headers)
    assert status.json()['run_limit'] == {'seconds': 60, 'bytes': 1048576}
    assert status.json()['unattended'] is True
    ticket = client.get(f'/api/ticket/{rid}', headers=gateway_headers)
    body = ticket.json()
    assert body['run_limit'] == {'seconds': 60, 'bytes': 1048576}
    assert body['ticket']


def test_an_old_gateway_row_does_not_decide(temp_db):
    """The capability is read from the request, never the stored column."""
    from main import approve_unattended
    from db.gateways import set_gateway_run_limits

    set_gateway_run_limits('10.0.0.1', 'v1')
    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                             'agent-a', '')
    assert 'approved' not in out


def test_the_settings_endpoint_requires_a_session(client):
    assert client.get('/api/settings/run-limit').status_code in (401, 403)
    response = client.put('/api/settings/run-limit',
                          json={'seconds': 60, 'bytes': 1048576})
    assert response.status_code in (401, 403)


def test_the_settings_endpoint_validates_and_audits(auth_client):
    from db.audit import get_audit_log
    from db.misc import get_run_limit_settings

    for bad in ({'seconds': 4, 'bytes': 1048576},
                {'seconds': 301, 'bytes': 1048576},
                {'seconds': 60, 'bytes': 65535},
                {'seconds': 60, 'bytes': 8388609}):
        response = auth_client.put('/api/settings/run-limit', json=bad,
                                   headers={'X-Eshu-CSRF': '1'})
        assert response.status_code == 400, bad
    response = auth_client.put('/api/settings/run-limit',
                               json={'seconds': 30, 'bytes': 65536},
                               headers={'X-Eshu-CSRF': '1'})
    assert response.status_code == 200
    assert get_run_limit_settings() == {'seconds': 30, 'bytes': 65536}
    rows = [e for e in get_audit_log() if e['event_type'] == 'run_limit_changed']
    assert len(rows) == 1 and '30 s / 65536 bytes' in rows[0]['details']


def test_the_exec_result_takes_only_the_three_values(temp_db, client,
                                                     gateway_headers):
    from main import approve_unattended
    import base64

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                             'agent-a', 'v1')
    rid = out['request_id']
    body = {
        'target_ip': '10.0.0.1',
        'encoded_command': base64.b64encode(b'uptime').decode(),
        'path': 'lockbox', 'exit_code': 124, 'duration_ms': 5001,
        'encoded_output': base64.b64encode(b'x').decode(),
        'output_truncated': False, 'request_id': rid, 'limit': 'time',
    }
    assert client.post('/api/exec-result', json=body,
                       headers=gateway_headers).status_code == 200
    from db.exec_results import get_result
    assert get_result(rid)['limit_kind'] == 'time'

    body.pop('request_id')
    body['limit'] = 'sometimes'
    resp = client.post('/api/exec-result', json=body, headers=gateway_headers)
    assert resp.status_code == 400, resp.json()


# ── the gateway, end to end (sandbox) ───────────────────────────────────────

def _gateway(tmp_path, **kw):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_gateway_script import GatewaySandbox
    gw = GatewaySandbox(tmp_path / 'gw', **kw)
    gw.capture('tail')
    return gw


def _envelope(proc):
    for line in proc.stdout.splitlines():
        if line.startswith('ESHU-MCP-ENVELOPE'):
            return json.loads(line.split(' ', 1)[1])
    raise AssertionError('no envelope in:\n' + proc.stdout)


def _result_bodies(gw):
    return [json.loads(raw) for call, raw in zip(gw.curl_calls(), gw.raw_bodies())
            if call['url'].endswith('/api/exec-result')]


def test_an_unattended_mcp_run_is_bounded_in_one_round_trip(tmp_path):
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 60, 'bytes': 65536})
    cmd = gw.command_script(
        'big', "python3 -c 'import sys; sys.stdout.write(\"x\"*200000)'\n")
    proc = gw.run(gw.sign(cmd))
    env = _envelope(proc)
    assert env['status'] == 'ran', env
    assert env['truncated'] is True
    import base64
    assert len(base64.b64decode(env['output_b64'])) <= 65536
    assert 'output cut at 65536 bytes' in ' '.join(env['messages'])
    posted = _result_bodies(gw)
    assert posted and posted[-1]['limit'] == 'bytes'
    # Claimed by id in the same call, never handed to the poller.
    assert any(c['url'].endswith('/api/ticket/123') for c in gw.curl_calls())


def test_the_time_limit_stops_the_command(tmp_path):
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 5, 'bytes': 65536})
    cmd = gw.command_script('slow', 'sleep 10\n')
    started = time.monotonic()
    proc = gw.run(gw.sign(cmd))
    elapsed = time.monotonic() - started
    env = _envelope(proc)
    assert env['exit_code'] == 124, env
    assert 'stopped after 5 s' in ' '.join(env['messages'])
    assert elapsed < 12, elapsed
    posted = _result_bodies(gw)
    assert posted and posted[-1]['limit'] == 'time'


def test_the_time_limit_marker_does_not_ride_on_the_wall_clock(tmp_path):
    """The flake: the "stopped after" marker compared two CLOCK_REALTIME
    stamps, so an NTP step between them dropped the marker although `timeout`
    had fired (rc 124). The measurement is monotonic now; this pins it by
    moving the script's second `date +%s%N` call three seconds back."""
    import shutil
    real_date = shutil.which('date')
    if real_date is None:  # pragma: no cover - Linux runners always have it
        pytest.skip('no date binary to wrap')
    gw = _gateway(tmp_path)
    counter = tmp_path / 'date.calls'
    wrapper = gw.bin / 'date'
    wrapper.write_text(
        '#!/bin/bash\n'
        'if [ "$1" = "+%s%N" ]; then\n'
        '  n=$(cat "' + str(counter) + '" 2>/dev/null || echo 0)\n'
        '  printf "%s\\n" $((n + 1)) > "' + str(counter) + '"\n'
        '  if [ "$n" -ge 1 ]; then exec "' + real_date + '" -d "-3 seconds" +%s%N; fi\n'
        'fi\n'
        'exec "' + real_date + '" "$@"\n')
    wrapper.chmod(0o755)

    gw.unattended({'seconds': 5, 'bytes': 65536})
    cmd = gw.command_script('slow', 'sleep 10\n')
    proc = gw.run(gw.sign(cmd))
    env = _envelope(proc)
    assert env['exit_code'] == 124, env
    assert 'stopped after 5 s' in ' '.join(env['messages']), env
    assert _result_bodies(gw)[-1]['limit'] == 'time'


def test_a_human_approved_run_has_no_limit(tmp_path):
    gw = _gateway(tmp_path)
    cmd = gw.command_script('slowish', 'sleep 2\n')
    gw.exact(cmd)   # a gateway-local, human-approved path: no limit
    proc = gw.run(gw.sign(cmd))
    env = _envelope(proc)
    assert env['exit_code'] == 0
    assert 'ESHU limit' not in ' '.join(env['messages'])
    posted = _result_bodies(gw)
    assert posted and 'limit' not in posted[-1]


def test_an_unusable_limit_refuses_the_call(tmp_path):
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 4, 'bytes': 1048576})
    cmd = gw.command_script('marker', 'touch ran-marker\n')
    proc = gw.run(gw.sign(cmd))
    assert 'Invalid run_limit' in proc.stdout
    assert proc.returncode != 0


def test_an_unsafe_run_dir_refuses_a_limited_run(tmp_path):
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 60, 'bytes': 65536})
    # W1: the same directory is only trusted at 0700 and owned by the run user.
    gw.capture_dir.chmod(0o755)
    cmd = gw.command_script('marker2', 'touch ran-marker\n')
    proc = gw.run(gw.sign(cmd))
    assert 'cannot be enforced' in proc.stdout
    assert not (gw.root / 'ran-marker').exists()


def test_the_read_lane_without_the_capability_keeps_the_row_pending(
        temp_db, client, gateway_headers):
    """L7 end to end: the request's capability decides, not the gateway row."""
    from db.gateways import set_gateway_read_lane, set_gateway_run_limits
    from db.requests import get_all_requests
    import base64

    set_gateway_run_limits('10.0.0.1', 'v1')      # a stale stored value
    set_gateway_read_lane('10.0.0.1', 'on')
    from db.core import db_conn
    with db_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                     "VALUES ('read_lane_on_enabled', '1')")
        conn.commit()
    response = client.post('/api/request', json={
        'target_ip': '10.0.0.1',
        'encoded_command': base64.b64encode(b'uptime').decode(),
        'run_limits': ''}, headers=gateway_headers)
    assert response.status_code == 200
    body = response.json()
    assert body.get('read_lane') is None
    row = next(r for r in get_all_requests() if r['id'] == body['request_id'])
    assert row['status'] == 'pending'
    assert row['reason'] == 'read-lane-no-limits'


def test_a_sigkilled_command_is_still_a_time_limit(tmp_path):
    """AD1: `timeout -k 5` kills a TERM-ignoring command with SIGKILL, and
    timeout then exits 137 instead of 124. Both are the limit firing."""
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 5, 'bytes': 65536})
    cmd = gw.command_script('stubborn', 'trap "" TERM\nsleep 30\n')
    proc = gw.run(gw.sign(cmd))
    env = _envelope(proc)
    assert env['exit_code'] == 137, env
    assert 'stopped after 5 s' in ' '.join(env['messages'])
    assert _result_bodies(gw)[-1]['limit'] == 'time'


def test_a_command_that_exits_137_itself_is_not_a_limit(tmp_path):
    """The elapsed-time guard still applies to 137."""
    gw = _gateway(tmp_path)
    gw.unattended({'seconds': 60, 'bytes': 65536})
    cmd = gw.command_script('self137', 'exit 137\n')
    proc = gw.run(gw.sign(cmd))
    env = _envelope(proc)
    assert env['exit_code'] == 137
    assert 'ESHU limit' not in ' '.join(env['messages'])
    posted = _result_bodies(gw)
    assert posted and 'limit' not in posted[-1]


def test_a_gateway_without_gnu_timeout_refuses_a_limited_run(tmp_path):
    """AD2: uutils/BusyBox `timeout` cannot bound the process group."""
    gw = _gateway(tmp_path, timeout_version='timeout (uutils coreutils) 0.0.1')
    gw.unattended({'seconds': 60, 'bytes': 65536})
    cmd = gw.command_script('marker3', 'touch ran-marker\n')
    proc = gw.run(gw.sign(cmd))
    assert 'cannot be enforced' in proc.stdout
    assert not (gw.root / 'ran-marker').exists()
    assert not _result_bodies(gw)


# ── AF1/AF2: an unattended row must carry a usable limit ────────────────────

@pytest.mark.parametrize('response', [
    '{"unattended":true}',
    '{"unattended":true,"run_limit":null}',
    '{"unattended":true,"run_limit":{}}',
    '{"unattended":true,"run_limit":{"seconds":"60","bytes":1048576}}',
])
def test_an_unattended_row_without_a_usable_limit_is_invalid(response):
    out = _parse(response)
    assert out.stdout.strip().startswith('1|invalid'), out.stdout


def test_an_unattended_row_with_a_limit_is_valid():
    out = _parse('{"unattended":true,"run_limit":{"seconds":60,"bytes":1048576}}')
    assert out.stdout.strip() == '0|valid|60|1048576'


def test_a_human_row_may_omit_the_limit():
    out = _parse('{"run_limit":null}')
    assert out.stdout.strip() == '0|absent||'


@pytest.mark.parametrize('limit', [
    '{"seconds":%d,"bytes":1048576}' % (10 ** 30),
    '{"seconds":60,"bytes":%d}' % (10 ** 30),
])
def test_a_huge_value_is_invalid_in_the_parser(limit):
    """AF2: bash `[ -lt ]` on a value over 2^63 errors, and inside `if` that
    error counts as false, so the range check lives in the Python."""
    out = _parse('{"run_limit":%s}' % limit)
    assert out.stdout.strip().startswith('1|invalid'), out.stdout


def test_the_endpoints_never_omit_the_limit_for_an_unattended_row(
        temp_db, client, gateway_headers):
    """AF1: a corrupt stored limit is sent as {}, never null or absent."""
    from main import approve_unattended
    from db.core import db_conn
    _agent_ticket_gateway()

    out = approve_unattended('10.0.0.1', 'uptime', 'read-lane', '', '', '',
                             'agent-a', 'v1')
    rid = out['request_id']
    with db_conn() as conn:
        conn.execute("UPDATE requests SET run_limit = 'not json' WHERE id = ?",
                     (rid,))
        conn.commit()
    status = client.get(f'/api/request_status/{rid}', headers=gateway_headers)
    assert status.json()['run_limit'] == {}
    assert status.json()['unattended'] is True
    ticket = client.get(f'/api/ticket/{rid}', headers=gateway_headers)
    assert ticket.json()['run_limit'] == {}


def test_an_mcp_run_with_a_null_limit_is_refused(tmp_path):
    gw = _gateway(tmp_path)
    gw.unattended(None)          # "unattended": true, "run_limit": null
    cmd = gw.command_script('marker4', 'touch ran-marker\n')
    proc = gw.run(gw.sign(cmd))
    assert 'Invalid run_limit' in proc.stdout
    assert not (gw.root / 'ran-marker').exists()


def test_the_poll_path_also_refuses_a_null_limit(tmp_path):
    """AF1: the direct-SSH poll loop reads the limit from the claim."""
    gw = _gateway(tmp_path)
    gw.unattended(None)
    gw.jit_status('approved')
    cmd = gw.command_script('marker5', 'touch ran-marker\n')
    proc = gw.run(cmd)           # unsigned, so the ticket is agent-less
    assert 'Invalid run_limit' in proc.stdout
    assert not (gw.root / 'ran-marker').exists()
