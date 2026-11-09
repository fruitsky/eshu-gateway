"""Chunk F8 — test-traffic hygiene.

Excluding a row is an explicit, audited flag: the rows stay in History, and
every aggregate view (statistics, learning gaps, anomaly profiles) skips them.
Nothing is excluded automatically — the session hint is a suggestion.
"""

import json
import os
import shutil
import subprocess

import pytest

from core.cmd_profiles import compute_profiles
from core.learning import compute_gaps
from db.audit import get_audit_log
from db.requests import create_request, get_all_requests
from db.gateways import register_gateway

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f8_harness.js')
NODE = shutil.which('node')


def _mk(command='uptime', status='approved', session='s8', ip='192.0.2.10',
        inner_target='', reason=''):
    return create_request(ip, command, status=status, ttl=0,
                          session_id=session, inner_target=inner_target,
                          reason=reason)


def _row(req_id):
    from db.core import db_conn
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT * FROM requests WHERE id = ?', (req_id,))
        return dict(cur.fetchone())


def _request_query_blocks(text):
    """Every `cursor.execute(` block in a function body that reads requests."""
    return [block for block in text.split('cursor.execute(')[1:]
            if 'FROM requests' in block]


class TestExcludeEndpoint:

    def test_f8_exclude_by_ids(self, auth_client):
        first = _mk('uptime')
        second = _mk('id')
        response = auth_client.post('/api/requests/exclude',
                                    json={'ids': [first, second]})
        assert response.status_code == 200
        assert response.json()['excluded'] == 2
        assert _row(first)['excluded'] == 1
        assert _row(second)['excluded'] == 1

    def test_f8_exclude_by_session(self, auth_client):
        mine = _mk('uptime', session='sess-test-1')
        other = _mk('uptime', session='sess-real')
        response = auth_client.post('/api/requests/exclude',
                                    json={'session_id': 'sess-test-1'})
        assert response.status_code == 200
        assert response.json()['excluded'] == 1
        assert _row(mine)['excluded'] == 1
        assert _row(other)['excluded'] == 0

    def test_f8_exclude_is_idempotent(self, auth_client):
        req_id = _mk('uptime')
        assert auth_client.post('/api/requests/exclude',
                                json={'ids': [req_id]}).json()['excluded'] == 1
        assert auth_client.post('/api/requests/exclude',
                                json={'ids': [req_id]}).json()['excluded'] == 0

    def test_f8_exclude_requires_a_target(self, auth_client):
        response = auth_client.post('/api/requests/exclude', json={})
        assert response.status_code == 400
        assert 'ids or session_id' in response.json()['detail']

    def test_f8_exclude_requires_a_session(self, client):
        assert client.post('/api/requests/exclude',
                           json={'ids': [1]}).status_code in (401, 403)

    def test_f8_exclude_writes_an_audit_event(self, auth_client):
        req_id = _mk('uptime')
        auth_client.post('/api/requests/exclude', json={'ids': [req_id]})
        events = [e for e in get_audit_log() if e['event_type'] == 'requests_excluded']
        assert events and 'Excluded 1 request' in events[0]['details']

    def test_f8_excluded_rows_stay_in_history(self, auth_client):
        req_id = _mk('uptime')
        auth_client.post('/api/requests/exclude', json={'ids': [req_id]})
        rows = auth_client.get('/api/requests').json()
        assert any(r['id'] == req_id and r['excluded'] == 1 for r in rows)
        assert any(r['id'] == req_id for r in get_all_requests())

    def test_f8_unknown_ids_are_not_an_error(self, auth_client):
        response = auth_client.post('/api/requests/exclude',
                                    json={'ids': [999999]})
        assert response.status_code == 200
        assert response.json()['excluded'] == 0


class TestStatisticsIgnoreExcluded:

    def test_f8_daily_and_gateway_totals_skip_excluded(self, auth_client):
        _mk('uptime')
        excluded = _mk('id')
        auth_client.post('/api/requests/exclude', json={'ids': [excluded]})
        data = auth_client.get('/api/statistics?days=14').json()
        assert data['daily'][-1]['total'] == 1
        assert sum(g['total'] for g in data['per_gateway']) == 1

    def test_f8_top_commands_and_denied_skip_excluded(self, auth_client):
        _mk('uptime')
        _mk('jenkins-build', status='denied')
        hidden_cmd = _mk('some-test-ping')
        hidden_denied = _mk('other-test-ping', status='denied')
        auth_client.post('/api/requests/exclude',
                         json={'ids': [hidden_cmd, hidden_denied]})
        data = auth_client.get('/api/statistics?days=14&extended=1').json()
        top = [c['command'] for c in data['top_commands']]
        assert 'uptime' in top
        assert 'some-test-ping' not in top
        denied = [c['command'] for c in data['top_denied']]
        assert 'jenkins-build' in denied
        assert 'other-test-ping' not in denied

    def test_f8_export_skips_excluded(self, auth_client):
        _mk('uptime')
        excluded = _mk('id')
        auth_client.post('/api/requests/exclude', json={'ids': [excluded]})
        rows = auth_client.get('/api/statistics/export?days=14&format=json').json()
        assert sum(r['total'] for r in rows) == 1

    def test_f8_inner_target_warning_keeps_excluded_rows(self, auth_client):
        """R1: the override warning is a safety prompt, not a statistic."""
        register_gateway('192.0.2.10', 'pve01', 'v15.3')
        _mk('pct exec 103 -- uptime', inner_target='pct:103')
        hidden = _mk('pct exec 106 -- uptime', inner_target='pct:106')
        auth_client.post('/api/requests/exclude', json={'ids': [hidden]})
        warning = auth_client.get(
            '/api/gateways/192.0.2.10/inner-targets').json()
        assert warning['pct'] == ['103', '106']
        assert warning['count'] == 2
        # The Statistics table is a statistic, so it still skips the row.
        stats = auth_client.get('/api/statistics?days=14').json()
        assert [row['target'] for row in stats['inner_targets']] == ['pct:103']


class TestLearningIgnoreExcluded:

    def test_f8_excluded_approvals_do_not_suggest_an_allowlist(self):
        for _ in range(3):
            req = _mk('synthetic-test-cmd')
            from db.requests import mark_requests_excluded
            mark_requests_excluded(ids=[req])
        assert compute_gaps()['total_gaps'] == 0
        for _ in range(3):
            _mk('synthetic-real-cmd')
        gaps = compute_gaps()['gateways'][0]['gaps']
        assert [g['command'] for g in gaps] == ['synthetic-real-cmd']

    def test_f8_excluded_denials_do_not_suggest_a_blocklist(self):
        from db.requests import mark_requests_excluded
        for _ in range(20):
            req = _mk('synthetic-test-cmd', status='denied')
            mark_requests_excluded(ids=[req])
        assert compute_gaps()['total_gaps'] == 0

    def test_f8_anomaly_profiles_ignore_excluded(self):
        from db.requests import mark_requests_excluded
        req = _mk('synthetic-test-cmd')
        mark_requests_excluded(ids=[req])
        assert compute_profiles()['gateways'].get('192.0.2.10', {}).get('count', 0) == 0
        _mk('synthetic-real-cmd')
        profiles = compute_profiles()['gateways']['192.0.2.10']
        assert profiles['count'] == 1
        assert 'synthetic-real-cmd' in profiles['base_commands']


class TestTrafficSuggestions:

    def test_f8_exclusion_is_never_automatic(self, auth_client):
        _mk('uptime', session='sess-smoke-test')
        rows = auth_client.get('/api/requests').json()
        assert all(r['excluded'] == 0 for r in rows)

    @pytest.mark.skipif(NODE is None, reason='node is not installed')
    def test_f8_the_ui_only_suggests_for_test_goals(self):
        proc = subprocess.run([NODE, HARNESS, '{}'], capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out['test'] == [True, False, False, False, True]
        assert 'excluded-chip' in out['excludedChip']
        assert 'excludeRequest(5)' in out['excludeLink']
        assert 'excluded-chip' not in out['excludeLink']


class TestStatisticsFilterScan:
    """S2: a future aggregate cannot forget the exclusion filter."""

    def _main_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'dashboard', 'main.py')
        return open(path, encoding='utf-8').read()

    def test_f8_every_statistics_query_filters_excluded(self):
        src = self._main_source()
        stats = src[src.index('def get_statistics('):
                    src.index('@app.get("/api/statistics/export")')]
        export = src[src.index('def export_statistics('):
                     src.index('# --- Notes ---')]
        checked = 0
        for name, body in (('get_statistics', stats),
                           ('export_statistics', export)):
            blocks = _request_query_blocks(body)
            assert blocks, name
            for block in blocks:
                assert 'excluded' in block, (name, block[:160])
                checked += 1
        assert checked >= 9, checked


class TestExcludedSessionState:
    """S3: an excluded session says so instead of offering the button again."""

    def test_f8_detail_reports_an_excluded_session(self, auth_client):
        sid = 'sess-f8-excluded'
        _mk('uptime', session=sid)
        _mk('id', session=sid)
        auth_client.post('/api/requests/exclude', json={'session_id': sid})
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert data['summary']['excluded'] == 2
        assert data['summary']['all_excluded'] is True
        # Rows added to the session later are not excluded.
        _mk('date', session=sid)
        data = auth_client.get(f'/api/sessions/{sid}/detail').json()
        assert data['summary']['excluded'] == 2
        assert data['summary']['all_excluded'] is False
