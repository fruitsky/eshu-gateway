"""Chunk F6 — session wrap-up and "how approved" stats.

One mapping (`core/approval_buckets.py`) feeds the session card, the
statistics series and the MCP `session_summary`, so the tests drive it from
every angle: the table itself, the code's reason literals, the session
summary, and the two endpoints.
"""

import json
import os
import re
import shutil
import subprocess
import time

import pytest

from core.approval_buckets import (
    APPROVED_STATUSES, BUCKET_KEYS, BUCKETS, KNOWN_REASON_PREFIXES,
    NOT_AN_APPROVAL, OVERRIDE_BUCKETS, bucket_for, empty_buckets,
)
from db.requests import (
    create_request, expire_stale_requests, update_request_status,
    update_request_status_if_pending,
)
from db.sessions import get_session_summary


def _create(**kwargs):
    kwargs.setdefault('ttl', 0)
    return create_request(**kwargs)


def _row(req_id):
    from db.core import db_conn
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM requests WHERE id = ?', (req_id,))
        return dict(cur.fetchone())


def _decide(req_id, status, ago=30):
    """Move a request to a terminal state `ago` seconds after creation."""
    from db.core import db_conn
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT created_at FROM requests WHERE id = ?', (req_id,))
        created = cur.fetchone()['created_at']
        cur.execute('UPDATE requests SET status = ?, decided_at = ? WHERE id = ?',
                    (status, created + ago, req_id))
        conn.commit()


def _scan_reason_prefixes():
    """Every reason literal passed to create_request in dashboard/."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'dashboard')
    prefixes = set()
    for base, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith('.py'):
                continue
            text = open(os.path.join(base, name), encoding='utf-8').read()
            for match in re.finditer(r'create_request\((.*?)\)', text, re.S):
                for reason in re.finditer(
                        r'reason\s*=\s*(?:f)?["\']([^"\']*)["\']',
                        match.group(1)):
                    prefix = re.split(r'[:%{}]', reason.group(1))[0]
                    if prefix:
                        prefixes.add(prefix)
    return prefixes


def _unmapped_prefixes(prefixes):
    return sorted(p for p in prefixes if p not in KNOWN_REASON_PREFIXES)


class TestBucketMapping:

    @pytest.mark.parametrize('status,reason,bucket', [
        ('auto-approved', '', 'allowlist'),
        ('auto-approved', 'exact allowlist: uptime', 'allowlist'),
        ('approved', '', 'human'),
        ('approved', 'read-lane-shadow', 'human'),
        ('consumed', '', 'human'),
        ('abandoned', '', 'human'),
        ('approved', 'session-plan:12:3', 'plan'),
        ('consumed', 'session-plan:12:3', 'plan'),
        ('approved', 'session-override:4', 'session-override'),
        ('consumed', 'session-override:4', 'session-override'),
        ('approved', 'override', 'host-override'),
        ('consumed', 'override', 'host-override'),
        ('approved', 'read-lane', 'read-lane'),
        ('consumed', 'read-lane', 'read-lane'),
        ('window-approved', '', 'window'),
        ('fleet-run', 'operator asked for it', 'fleet'),
    ])
    def test_f6_bucket_mapping(self, status, reason, bucket):
        assert bucket_for(status, reason) == bucket

    @pytest.mark.parametrize('status', [
        'pending', 'denied', 'blocked', 'expired', 'frozen',
        'session-required', 'window-rejected', 'window-requested',
        'integration-approved', 'integration-denied', '',
    ])
    def test_f6_refusals_are_not_approvals(self, status):
        assert bucket_for(status, '') is None
        assert status in NOT_AN_APPROVAL

    def test_f6_a_status_alone_wins_over_a_free_text_reason(self):
        """Allowlist rows carry the gateway's reason; it must not move them."""
        for reason in ('read-lane', 'session-plan:1:2', 'override'):
            assert bucket_for('auto-approved', reason) == 'allowlist'

    def test_f6_unknown_reason_on_an_approval_is_human(self):
        assert bucket_for('approved', 'some new reason') == 'human'

    def test_f6_every_bucket_is_reachable(self):
        seen = set()
        for status in sorted(APPROVED_STATUSES):
            seen.add(bucket_for(status, ''))
        for reason in ('session-plan:1:1', 'session-override:1', 'override',
                       'read-lane'):
            seen.add(bucket_for('approved', reason))
        assert set(BUCKET_KEYS) <= seen

    def test_f6_empty_buckets_has_every_key_at_zero(self):
        buckets = empty_buckets()
        assert tuple(buckets) == BUCKET_KEYS
        assert all(value == 0 for value in buckets.values())
        for key in OVERRIDE_BUCKETS:
            assert key in BUCKET_KEYS

    def test_f6_every_code_reason_prefix_is_known(self):
        """Generated: a new request reason cannot slip in without the table."""
        prefixes = _scan_reason_prefixes()
        assert 'session-plan' in prefixes
        assert 'session-override' in prefixes
        assert 'override' in prefixes
        # `read-lane` comes from F4, which is not on this branch; the table
        # already carries it, so the scan picks the literal up after merge.
        assert _unmapped_prefixes(prefixes) == []

    def test_f6_the_reason_check_can_fail(self):
        """R3: run the check on a list, so a synthetic new prefix fails it."""
        assert _unmapped_prefixes(['brand-new-prefix']) == ['brand-new-prefix']
        assert _unmapped_prefixes(['session-plan', 'override']) == []

    def test_f6_the_table_agrees_with_bucket_for(self):
        for prefix, bucket in KNOWN_REASON_PREFIXES.items():
            sample = prefix + ':synthetic' if prefix in (
                'session-plan', 'session-override', 'encoded-payload') else prefix
            assert bucket_for('approved', sample) == bucket, prefix


class TestSessionSummary:

    def test_f6_by_approval_counts_each_bucket(self):
        sid = 'sess-f6-buckets'
        _create(target_ip='192.0.2.10', command='uptime', status='auto-approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='id', status='approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='date', status='approved',
                reason='session-plan:5:1', session_id=sid)
        _create(target_ip='192.0.2.10', command='df -h', status='approved',
                reason='read-lane', session_id=sid)
        _create(target_ip='192.0.2.10', command='free -h', status='approved',
                reason='override', session_id=sid)
        _create(target_ip='192.0.2.10', command='ps', status='approved',
                reason='session-override:7', session_id=sid)
        _create(target_ip='192.0.2.10', command='ss -tlnp', status='window-approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='uname -a', status='fleet-run', session_id=sid)
        _create(target_ip='192.0.2.10', command='rm -rf /tmp/x', status='denied', session_id=sid)
        summary = get_session_summary(sid)
        assert summary['by_approval'] == {
            'allowlist': 1, 'human': 1, 'plan': 1, 'read-lane': 1,
            'session-override': 1, 'host-override': 1, 'window': 1, 'fleet': 1,
        }

    def test_f6_summary_is_empty_for_a_blank_session(self):
        summary = get_session_summary('')
        assert summary['by_approval'] == empty_buckets()
        assert summary['waited_seconds'] == 0

    def test_f6_waited_seconds_sums_decision_times(self):
        sid = 'sess-f6-wait'
        first = _create(target_ip='192.0.2.10', command='uptime', session_id=sid)
        second = _create(target_ip='192.0.2.10', command='id', session_id=sid)
        _decide(first, 'approved', ago=20)
        _decide(second, 'denied', ago=40)
        summary = get_session_summary(sid)
        assert summary['waited_seconds'] == 60
        assert summary['decided'] == 2

    def test_f6_mcp_session_summary_exposes_by_approval(self):
        from core.mcp_server import session_summary_fn
        sid = 'sess-f6-mcp'
        _create(target_ip='192.0.2.10', command='uptime', status='auto-approved',
                session_id=sid)
        data = json.loads(session_summary_fn()(sid))
        assert data['by_approval']['allowlist'] == 1
        assert data['by_approval']['host-override'] == 0
        assert 'waited_seconds' in data

    def test_f6_rows_without_a_decision_add_no_wait(self):
        sid = 'sess-f6-pending'
        _create(target_ip='192.0.2.10', command='uptime', session_id=sid)
        summary = get_session_summary(sid)
        assert summary['waited_seconds'] == 0
        assert summary['decided'] == 0


class TestDecidedAt:

    def test_f6_approve_stamps_the_decision_time(self, auth_client):
        req_id = _create(target_ip='192.0.2.10', command='uptime')
        assert auth_client.post(f'/api/approve/{req_id}').status_code == 200
        row = _row(req_id)
        assert row['status'] == 'approved'
        assert row['decided_at'] and row['decided_at'] >= row['created_at']

    def test_f6_deny_stamps_the_decision_time(self, auth_client):
        req_id = _create(target_ip='192.0.2.10', command='id')
        assert auth_client.post(f'/api/deny/{req_id}').status_code == 200
        row = _row(req_id)
        assert row['status'] == 'denied'
        assert row['decided_at']

    def test_f6_a_later_status_change_keeps_the_first_decision(self):
        req_id = _create(target_ip='192.0.2.10', command='uptime')
        assert update_request_status_if_pending(req_id, 'approved') is True
        decided = _row(req_id)['decided_at']
        update_request_status(req_id, 'consumed')
        row = _row(req_id)
        assert row['status'] == 'consumed'
        assert row['decided_at'] == decided

    def test_f6_expiry_stamps_the_decision_time(self, temp_db):
        req_id = create_request('192.0.2.10', 'uptime', status='pending', ttl=1)
        expired, _abandoned = expire_stale_requests(int(time.time()) + 10)
        assert req_id in expired
        row = _row(req_id)
        assert row['status'] == 'expired'
        assert row['decided_at']

    def test_f6_pending_again_clears_the_decision(self):
        req_id = _create(target_ip='192.0.2.10', command='uptime')
        update_request_status(req_id, 'approved')
        update_request_status(req_id, 'pending')
        assert _row(req_id)['decided_at'] is None


class TestRowsDecidedAtCreation:
    """R1: a row created already approved was never waiting for a human."""

    @pytest.mark.parametrize('status,reason', [
        ('approved', 'override'),
        ('approved', 'session-override:4'),
        ('approved', 'session-plan:7:1'),
        ('auto-approved', ''),
        ('window-approved', ''),
        ('fleet-run', 'operator asked'),
    ])
    def test_f6_a_decided_row_starts_at_its_creation(self, status, reason):
        req_id = _create(target_ip='192.0.2.10', command='uptime',
                         status=status, reason=reason)
        row = _row(req_id)
        assert row['decided_at'] == row['created_at'], (status, reason)

    def test_f6_a_pending_row_has_no_decision(self):
        req_id = _create(target_ip='192.0.2.10', command='uptime')
        assert _row(req_id)['decided_at'] is None

    def test_f6_an_unclaimed_override_waits_zero(self):
        sid = 'sess-r1-abandoned'
        for reason in ('override', 'session-override:1', 'session-plan:2:1'):
            create_request('192.0.2.10', 'uptime', status='approved', ttl=1,
                           reason=reason, session_id=sid)
        expire_stale_requests(int(time.time()) + 600)
        summary = get_session_summary(sid)
        assert summary['waited_seconds'] == 0
        assert summary['decided'] == 3
        for reason in ('override', 'session-override:1', 'session-plan:2:1'):
            assert bucket_for('abandoned', reason) is not None

    def test_f6_an_auto_approved_log_row_waits_zero(self, client, gateway_headers):
        import base64
        response = client.post('/api/log', json={
            'target_ip': '10.0.0.1',
            'encoded_command': base64.b64encode(b'uptime').decode(),
            'status': 'auto-approved',
        }, headers=gateway_headers)
        assert response.status_code == 200, response.text
        from db.requests import get_all_requests
        row = max(get_all_requests(), key=lambda r: r['id'])
        assert row['status'] == 'auto-approved'
        assert row['decided_at'] == row['created_at']


class TestSessionDetail:

    def test_f6_detail_carries_the_wrapup_summary(self, auth_client):
        sid = 'sess-f6-detail'
        approved = _create(target_ip='192.0.2.10', command='uptime',
                           status='approved', session_id=sid)
        _decide(approved, 'consumed', ago=15)
        denied = _create(target_ip='192.0.2.10', command='rm /tmp/x',
                         session_id=sid)
        _decide(denied, 'denied', ago=5)
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        summary = data['summary']
        assert summary['session_id'] == sid
        assert summary['commands'] == 2
        assert summary['by_approval']['human'] == 1
        assert summary['waited_seconds'] >= 15
        assert summary['decided'] == 2

    def test_f6_detail_counts_writes_per_host(self, auth_client):
        sid = 'sess-f6-writes'
        _create(target_ip='192.0.2.10', command='mv /tmp/a /tmp/b',
                status='approved', session_id=sid)
        _create(target_ip='192.0.2.20', command='systemctl restart nginx',
                status='approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='uptime',
                status='auto-approved', session_id=sid)
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        summary = data['summary']
        assert summary['writes'] == 2
        assert summary['writes_by_host'] == {'192.0.2.10': 1, '192.0.2.20': 1}

    def test_f6_writes_count_only_decided_rows(self, auth_client):
        """R2: denied, blocked and expired commands are not changes."""
        sid = 'sess-r2-writes'
        _create(target_ip='192.0.2.10', command='mv /tmp/a /tmp/b',
                status='approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='rm -rf /tmp/x',
                status='denied', session_id=sid)
        _create(target_ip='192.0.2.10', command='systemctl restart nginx',
                status='blocked', session_id=sid)
        _create(target_ip='192.0.2.10', command='tar -c /tmp/x',
                status='expired', session_id=sid)
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert data['summary']['writes'] == 1
        assert data['summary']['writes_by_host'] == {'192.0.2.10': 1}

    def test_f6_detail_reports_failures(self, auth_client):
        from db.exec_results import save_result
        sid = 'sess-f6-failed'
        req_id = _create(target_ip='192.0.2.10', command='uptime',
                         status='consumed', session_id=sid)
        save_result(req_id, 1, 120, 'boom', False)
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert data['summary']['failed'] == 1

    def test_f6_detail_requires_a_session(self, client):
        assert client.get('/api/sessions/sess-x/detail').status_code in (401, 403)


class TestStatisticsApprovals:

    def _make(self):
        sid = 'sess-f6-stats'
        _create(target_ip='192.0.2.10', command='uptime', status='auto-approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='id', status='approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='date', status='approved',
                reason='session-plan:1:1', session_id=sid)
        _create(target_ip='192.0.2.10', command='df -h', status='approved',
                reason='read-lane', session_id=sid)
        _create(target_ip='192.0.2.10', command='free -h', status='approved',
                reason='session-override:1', session_id=sid)
        _create(target_ip='192.0.2.10', command='ps', status='approved',
                reason='override', session_id=sid)
        _create(target_ip='192.0.2.10', command='ss -tlnp', status='window-approved', session_id=sid)
        _create(target_ip='192.0.2.10', command='uname -a', status='fleet-run', session_id=sid)
        _create(target_ip='192.0.2.10', command='rm /tmp/x', status='denied', session_id=sid)

    def test_f6_statistics_series_totals_and_share(self, auth_client):
        self._make()
        data = auth_client.get('/api/statistics?days=14').json()
        block = data['approval_breakdown']
        assert block['days'] == 14
        assert len(block['series']) == 14
        assert block['totals'] == {
            'allowlist': 1, 'human': 1, 'plan': 1, 'read-lane': 1,
            'session-override': 1, 'host-override': 1, 'window': 1, 'fleet': 1,
        }
        assert block['approved'] == 8
        assert block['override_share'] == 25.0
        assert [b['key'] for b in block['buckets']] == list(BUCKET_KEYS)
        today = block['series'][-1]
        assert today['allowlist'] == 1 and today['human'] == 1

    def test_f6_approval_breakdown_skips_excluded_rows(self, auth_client):
        """F8 merge note: the series is a statistic, so excluded rows go."""
        from db.requests import mark_requests_excluded
        sid = 'sess-f6-excluded'
        _create(target_ip='192.0.2.10', command='uptime', status='approved',
                session_id=sid)
        hidden = _create(target_ip='192.0.2.10', command='id', status='approved',
                         session_id=sid)
        mark_requests_excluded(ids=[hidden])
        data = auth_client.get('/api/statistics?days=14').json()
        block = data['approval_breakdown']
        assert block['totals']['human'] == 1
        assert block['approved'] == 1

    def test_f6_statistics_gateway_filter(self, auth_client):
        self._make()
        _create(target_ip='192.0.2.99', command='uptime',
                status='auto-approved', session_id='sess-other')
        data = auth_client.get('/api/statistics?days=14&gateway_ips=192.0.2.10').json()
        assert data['approval_breakdown']['approved'] == 8
        data = auth_client.get('/api/statistics?days=14&gateway_ips=192.0.2.99').json()
        assert data['approval_breakdown']['approved'] == 1
        assert data['approval_breakdown']['override_share'] == 0

    def test_f6_statistics_with_no_decisions(self, auth_client):
        data = auth_client.get('/api/statistics?days=7').json()
        block = data['approval_breakdown']
        assert block['approved'] == 0
        assert block['override_share'] == 0
        assert all(day['human'] == 0 for day in block['series'])


HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f6_harness.js')
NODE = shutil.which('node')


def run_node(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def test_f6_session_card_shows_buckets_writes_and_wait(self):
        out = run_node({'payload': 'web01'})
        html = out['summary']
        for label in ('allowlist', 'human', 'plan', 'read lane',
                      'session override', 'host override', 'window',
                      'fleet run'):
            assert label in html, label
        assert '3 commands' in html
        assert '1h 2m long' in html
        assert '1 failed' in html
        assert 'waited 1m 15s for a human' in html
        assert 'writes' in html and 'web01' in html
        # S1: the goal stays visible when the session has a name.
        assert 'goal' in html and 'web01' in html
        assert out['summaryNoCommands'] == ''
        assert 'wait not recorded' in out['summaryNoWait']

    def test_f6_card_escapes_hostnames(self):
        payload = '<img src=x onerror=alert(1)>'
        out = run_node({'payload': payload})
        assert payload not in out['summary']
        assert '&lt;img' in out['summary']
        assert payload not in out['approvals']
        assert '&lt;img' in out['approvals']

    def test_f6_stats_widget_stacks_days_and_greys_empty_buckets(self):
        out = run_node({'payload': '2026-11-07'})
        html = out['approvals']
        assert html.count('class="appr-day"') == 2
        assert 'appr-seg appr-human' in html
        assert 'appr-legend-item zero' in html      # override has a day of 0
        assert 'No decisions in this window.' in out['approvalsEmpty']

    def test_f6_duration_formatting(self):
        assert run_node({'payload': 'x'})['durations'] == [
            '0s', '45s', '2m 5s', '1h 2m', '0s']
