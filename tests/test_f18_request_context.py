"""Chunk F18 — request context in the sidebar."""

import hashlib
import json
import os
import shutil
import subprocess
import time

import pytest

from core.fingerprint import command_sha, shape_sha
from db.core import db_conn
from db.gateways import get_gateway, register_gateway, set_gateway_zero_trust

try:  # F4 may not be merged into this branch's base yet
    from db.gateways import set_gateway_read_lane
except ImportError:  # pragma: no cover
    set_gateway_read_lane = None

# The read-lane reasons only exist once F4 is in; skip those cases until then.
F4_READ_LANE = shutil.which('true') is not None and __import__(
    'importlib.util', fromlist=['x']).find_spec('core.read_lane') is not None
needs_f4 = pytest.mark.skipif(not F4_READ_LANE,
                              reason='F4 read lane is not merged into this base')
from db.requests import create_request, get_request_row
from db.sessions import upsert_session

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f18_harness.js')
NODE = shutil.which('node')


def run_node(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


@pytest.fixture
def gateway(auth_client):
    register_gateway('10.0.0.1', 'web01', 'v15.3')
    return auth_client


def _set_lane(mode):
    if set_gateway_read_lane is not None:
        set_gateway_read_lane('10.0.0.1', mode)


def _context(client, req_id):
    response = client.get('/api/requests/%d/context' % req_id)
    assert response.status_code == 200, response.text
    return response.json()


def _legacy_row(command, target_ip='10.0.0.1', created_at=None):
    now = int(created_at if created_at is not None else time.time())
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO requests (target_ip, command, status, created_at, expires_at) "
            "VALUES (?, ?, 'auto-approved', ?, 0)", (target_ip, command, now))
        conn.commit()


class TestSections:

    def test_f18_session_section_caps_and_orders(self, gateway):
        upsert_session('f18-session', goal='fix nginx', agent='hermes')
        ids = [create_request('10.0.0.1', 'uptime %d' % i, status='pending',
                              ttl=90, session_id='f18-session') for i in range(12)]
        data = _context(gateway, ids[-1])
        session = data['session']
        assert session['goal'] == 'fix nginx' and session['agent'] == 'hermes'
        assert len(session['commands']) == 10
        assert [c['id'] for c in session['commands']] == sorted(ids, reverse=True)[:10]

    def test_f18_seen_before_across_hosts(self, gateway):
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        create_request('10.0.0.2', 'uptime', status='approved', ttl=0)
        create_request('10.0.0.2', 'uptime', status='denied', ttl=0)
        from db.exec_results import save_result
        save_result(req_id, 3, 10, '')
        seen = _context(gateway, req_id)['seen']
        assert seen['count'] == 3
        assert seen['operator_approved'] == 1
        assert seen['denied'] == 1
        assert seen['last_exit_code'] == 3

    def test_f18_seen_splits_operator_from_auto(self, gateway):
        """C1: an allowlist hit is not a decision the operator made."""
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        create_request('10.0.0.2', 'uptime', status='approved', ttl=0)
        create_request('10.0.0.2', 'uptime', status='auto-approved', ttl=0)
        create_request('10.0.0.1', 'uptime', status='approved', ttl=0,
                       reason='session-plan:1:2')
        create_request('10.0.0.1', 'uptime', status='consumed', ttl=0,
                       reason='read-lane')
        create_request('10.0.0.1', 'uptime', status='denied', ttl=0)
        seen = _context(gateway, req_id)['seen']
        assert seen['count'] == 6
        assert seen['operator_approved'] == 1
        assert seen['auto'] == 3
        assert seen['denied'] == 1

    def test_f18_override_approvals_are_automatic(self, gateway):
        """C1: a command run under a host or session override was not
        approved by the operator one by one."""
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        create_request('10.0.0.1', 'uptime', status='approved', ttl=0,
                       reason='override')
        create_request('10.0.0.1', 'uptime', status='consumed', ttl=0,
                       reason='session-override:7')
        create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        seen = _context(gateway, req_id)['seen']
        assert seen['operator_approved'] == 1
        assert seen['auto'] == 2

    def test_f18_like_this_ignores_arguments(self, gateway):
        req_id = create_request('10.0.0.1', 'df -h /var', status='pending', ttl=90)
        other = create_request('10.0.0.1', 'df -h /', status='approved', ttl=0)
        create_request('10.0.0.1', 'df -h /tmp', status='denied', ttl=0)
        like = _context(gateway, req_id)['like']
        assert like['count'] == 2
        assert like['operator_approved'] == 1 and like['denied'] == 1
        assert len(like['examples']) <= 3
        assert all(e['id'] != req_id for e in like['examples'])

    def test_f18_generic_shape_has_no_counts(self, gateway):
        """C2: a one-step interpreter shape is too generic to compare."""
        req_id = create_request('10.0.0.1', 'bash /tmp/synthetic.sh',
                                status='pending', ttl=90)
        create_request('10.0.0.1', 'bash /tmp/other.sh', status='approved', ttl=0)
        like = _context(gateway, req_id)['like']
        assert like['generic'] is True
        assert like['count'] == 0
        assert like['examples'] == []

    def test_f18_like_this_counts_in_sql(self, gateway):
        """C2: counts come from SQL and match the stored rows."""
        req_id = create_request('10.0.0.1', 'cat /var/log/a; cat /var/log/b',
                                status='pending', ttl=90)
        create_request('10.0.0.1', 'cat /var/log/c; cat /var/log/d',
                       status='approved', ttl=0)
        create_request('10.0.0.1', 'cat /var/log/e; cat /var/log/f',
                       status='denied', ttl=0)
        create_request('10.0.0.1', 'cat /var/log/g; cat /var/log/h',
                       status='auto-approved', ttl=0)
        like = _context(gateway, req_id)['like']
        assert like['generic'] is False
        assert like['count'] == 3
        assert like['operator_approved'] == 1
        assert like['auto'] == 1
        assert like['denied'] == 1
        assert len(like['examples']) == 3

    def test_f18_host_section(self, gateway):
        set_gateway_zero_trust('10.0.0.1', True)
        if set_gateway_read_lane is not None:
            _set_lane('shadow')
        req_id = create_request('10.0.0.1', 'pct exec 103 -- uptime',
                                status='pending', ttl=90, inner_target='pct:103')
        from db.exec_results import save_result
        failed = create_request('10.0.0.1', 'false', status='consumed', ttl=0)
        save_result(failed, 2, 5, '')
        host = _context(gateway, req_id)['host']
        assert host['hostname'] == 'web01'
        assert host['inner_target'] == 'pct:103'
        assert host['zero_trust'] is True
        assert host['read_lane'] == ('shadow' if set_gateway_read_lane else 'off')
        assert host['failed_24h'] == 1


class TestWaiting:

    def test_f18_waiting_encoded_payload(self, gateway):
        req_id = create_request('10.0.0.1', 'echo aGk= | base64 -d | bash',
                                status='pending', ttl=90)
        assert 'encoded' in _context(gateway, req_id)['waiting']

    def test_f18_waiting_metacharacter(self, gateway):
        req_id = create_request('10.0.0.1', 'echo hi && uptime', status='pending', ttl=90)
        waiting = _context(gateway, req_id)['waiting']
        assert 'metacharacter' in waiting and '&&' in waiting

    def test_f18_waiting_zero_trust(self, gateway):
        set_gateway_zero_trust('10.0.0.1', True)
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        assert 'Zero-Trust' in _context(gateway, req_id)['waiting']

    @needs_f4
    def test_f18_waiting_read_lane_off(self, gateway):
        _set_lane('off')
        req_id = create_request('10.0.0.1', 'uptime',
                                status='pending', ttl=90)
        waiting = _context(gateway, req_id)['waiting']
        assert 'read lane is off' in waiting

    @needs_f4
    def test_f18_waiting_read_lane_shadow(self, gateway):
        _set_lane('shadow')
        req_id = create_request('10.0.0.1', 'uptime',
                                status='pending', ttl=90)
        assert 'shadow' in _context(gateway, req_id)['waiting']

    @needs_f4
    def test_f18_waiting_read_lane_ineligible(self, gateway):
        _set_lane('on')
        req_id = create_request('10.0.0.1', 'less /var/log/syslog',
                                status='pending', ttl=90)
        waiting = _context(gateway, req_id)['waiting']
        # F1 classifies `less` as unknown, so the lane's first refusal is
        # `not-read`. (It used to say `verb:less` before Round 5's SC2.)
        assert 'read lane ineligible' in waiting and 'not-read' in waiting

    def test_f18_waiting_no_allowlist_match(self, gateway):
        _set_lane('off')
        req_id = create_request('10.0.0.1', 'systemctl restart nginx',
                                status='pending', ttl=90)
        assert _context(gateway, req_id)['waiting'] == 'no allowlist match'

    def test_f18_waiting_no_plan_or_override(self, gateway):
        from db.policies import update_policy
        update_policy('exact_whitelist', 'systemctl restart nginx')
        _set_lane('off')
        req_id = create_request('10.0.0.1', 'systemctl restart nginx',
                                status='pending', ttl=90)
        assert 'no plan or session override' in _context(gateway, req_id)['waiting']


class TestBackfill:

    def test_f18_backfill_is_batched_idempotent_and_flagged(self):
        from core.request_fingerprints import (
            BACKFILL_META_KEY, backfill_fingerprints,
        )
        _legacy_row('uptime')
        _legacy_row('pct exec 103 -- df -h')
        result = backfill_fingerprints(batch_size=1)
        assert result == {'filled': 2, 'skipped': False}
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT command, command_sha, shape_sha FROM requests").fetchall()
            meta = conn.execute("SELECT value FROM meta WHERE key = ?",
                                (BACKFILL_META_KEY,)).fetchone()
        assert meta is not None and meta['value']
        for row in rows:
            assert row['command_sha'] == command_sha(row['command'])
            assert row['shape_sha'] == shape_sha(row['command'])
        assert backfill_fingerprints() == {'filled': 0, 'skipped': True}

    def test_f18_new_rows_carry_fingerprints(self):
        req_id = create_request('10.0.0.1', 'pct exec 103 -- df -h',
                                status='pending', ttl=90)
        row = get_request_row(req_id)
        assert row['command_sha'] == command_sha('pct exec 103 -- df -h')
        assert row['shape_sha'] == shape_sha('pct exec 103 -- df -h')


class TestWaitingPendingOnly:

    def test_f18_non_pending_has_no_waiting_reason(self, gateway):
        """C3: the reason is only a guess for a row that is still waiting."""
        req_id = create_request('10.0.0.1', 'uptime', status='approved', ttl=0)
        assert _context(gateway, req_id)['waiting'] is None
        pending = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        assert isinstance(_context(gateway, pending)['waiting'], str)


class TestApi:

    def test_f18_requires_a_session(self, client):
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        assert client.get('/api/requests/%d/context' % req_id).status_code in (401, 403)

    def test_f18_unknown_request_is_404(self, gateway):
        assert gateway.get('/api/requests/999999/context').status_code == 404

    def test_f18_context_is_fast_on_50k_rows(self, gateway):
        base = int(time.time())
        rows = []
        for i in range(50000):
            sha = hashlib.sha256(('synthetic-%d' % i).encode()).hexdigest()
            rows.append(('10.0.0.1', 'synthetic-%d' % i, 'auto-approved',
                         base - i, 0, '', '', '', '', sha, sha))
        with db_conn() as conn:
            conn.executemany(
                "INSERT INTO requests (target_ip, command, status, created_at, "
                "expires_at, reason, session_id, execution_id, inner_target, "
                "command_sha, shape_sha) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
        req_id = create_request('10.0.0.1', 'uptime', status='pending', ttl=90)
        started = time.time()
        response = gateway.get('/api/requests/%d/context' % req_id)
        elapsed_ms = (time.time() - started) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 150, 'context took %.1f ms' % elapsed_ms


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def test_f18_waiting_label_and_generic_shape(self):
        out = run_node({'payload': 'x'})
        assert 'Likely waiting because' in out['waiting']
        assert out['waitingEmpty'] == ''
        assert 'Too generic to compare' in out['generic']

    def test_f18_every_section_escapes_agent_text(self):
        payload = '<img src=x onerror=alert(1)>'
        out = run_node({'payload': payload})
        for section in ('waiting', 'session', 'seen', 'like', 'host'):
            html = out[section]
            assert payload not in html, section
            assert '&lt;img' in html, section
