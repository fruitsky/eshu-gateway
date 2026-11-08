"""Chunk F7 — inner target attribution.

A `pct exec` / `qm guest exec` / `docker exec` command runs inside a container
or VM; these tests cover the parser addition, the request/log fill-in, the
one-off backfill, the API (endpoint + filter) and the two UI surfaces (the
chip and the host-override warning).
"""

import base64
import json
import os
import shutil
import subprocess
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f7_harness.js')
NODE = shutil.which('node')


def b64(text):
    return base64.b64encode(text.encode()).decode()


def run(payload):
    proc = subprocess.run([NODE, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


def _legacy_row(command, target_ip='10.0.0.1', created_at=None, inner_target=None):
    """A row as it looked before the F7 migration: inner_target NULL."""
    from db.core import db_conn
    now = int(created_at if created_at is not None else time.time())
    with db_conn() as conn:
        if inner_target is None:
            conn.execute(
                "INSERT INTO requests (target_ip, command, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (target_ip, command, 'auto-approved', now, 0))
        else:
            conn.execute(
                "INSERT INTO requests (target_ip, command, status, created_at, expires_at, inner_target) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (target_ip, command, 'auto-approved', now, 0, inner_target))
        conn.commit()


def _meta_value(key):
    from db.core import get_db
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else None
    finally:
        conn.close()


class TestParser:

    def test_f7_qm_guest_exec_inner_target(self):
        from core.cmd_parse import breakdown
        result = breakdown('qm guest exec 201 -- uptime')
        assert result['inner_targets'] == ['qm:201']
        assert result['steps'][0]['kind'] == 'read'
        assert result['steps'][0]['context'] == ['qm 201']
        assert result['steps'][0]['text'] == 'uptime'
        # the F1 wrappers keep working
        assert breakdown('pct exec 103 -- uptime')['inner_targets'] == ['pct:103']
        assert breakdown('docker exec web01 uptime')['inner_targets'] == ['docker:web01']


class TestRequestFill:

    @pytest.mark.parametrize('command,expected', [
        ('pct exec 103 -- uptime', 'pct:103'),
        ('docker exec web01 uptime', 'docker:web01'),
        ('qm guest exec 201 -- uptime', 'qm:201'),
        ('pct exec 103 -- uptime && pct exec 106 -- uptime', 'multi'),
        ('uptime', ''),
    ])
    def test_f7_request_rows_fill_inner_target(self, auth_client, gateway_headers,
                                               command, expected):
        from db.requests import get_all_requests
        response = auth_client.post('/api/request', json={
            'target_ip': '10.0.0.1', 'encoded_command': b64(command),
        }, headers=gateway_headers)
        assert response.status_code == 200
        row = next(r for r in get_all_requests() if r['command'] == command)
        assert row['inner_target'] == expected

    def test_f7_log_rows_fill_inner_target(self, auth_client, gateway_headers):
        from db.requests import get_all_requests
        command = 'qm guest exec 201 -- uptime'
        response = auth_client.post('/api/log', json={
            'target_ip': '10.0.0.1', 'encoded_command': b64(command),
            'status': 'auto-approved',
        }, headers=gateway_headers)
        assert response.status_code == 200
        row = next(r for r in get_all_requests() if r['command'] == command)
        assert row['inner_target'] == 'qm:201'

    def test_f7_encoded_request_still_records_inner_target(self, auth_client,
                                                          gateway_headers):
        """An encoded wrapper has no visible inner target, and that is fine."""
        from db.requests import get_all_requests
        command = 'echo dXB0aW1l | base64 -d | bash'
        auth_client.post('/api/request', json={
            'target_ip': '10.0.0.1', 'encoded_command': b64(command),
        }, headers=gateway_headers)
        row = next(r for r in get_all_requests() if r['command'] == command)
        assert row['inner_target'] == ''


class TestBackfill:

    def test_f7_backfill_fills_old_rows_and_skips_filled(self):
        from core.inner_targets import BACKFILL_META_KEY, backfill_inner_targets
        from db.requests import create_request, get_all_requests

        _legacy_row('pct exec 103 -- uptime')
        _legacy_row('uptime')
        create_request('10.0.0.1', 'docker exec web01 uptime',
                       status='auto-approved', ttl=0, inner_target='docker:web01')

        result = backfill_inner_targets()
        assert result == {'filled': 2, 'skipped': False}

        rows = {r['command']: r['inner_target'] for r in get_all_requests()}
        assert rows['pct exec 103 -- uptime'] == 'pct:103'
        assert rows['uptime'] == ''
        # an already-filled row is never overwritten
        assert rows['docker exec web01 uptime'] == 'docker:web01'
        # the completion key is recorded
        assert _meta_value(BACKFILL_META_KEY)

        # safe to run twice: nothing to do
        assert backfill_inner_targets() == {'filled': 0, 'skipped': True}

    def test_f7_backfill_handles_every_batch(self):
        from core.inner_targets import backfill_inner_targets
        from db.requests import get_all_requests
        for i in range(3):
            _legacy_row('qm guest exec %d -- uptime' % (200 + i))
        result = backfill_inner_targets(batch_size=2)
        assert result['filled'] == 3
        values = sorted(r['inner_target'] for r in get_all_requests())
        assert values == ['qm:200', 'qm:201', 'qm:202']

    def test_f7_startup_backfill_runs_in_the_background(self):
        from core.inner_targets import BACKFILL_META_KEY, start_backfill_thread
        from db.requests import get_all_requests

        _legacy_row('qm guest exec 201 -- uptime')
        thread = start_backfill_thread()
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert [r['inner_target'] for r in get_all_requests()] == ['qm:201']
        assert _meta_value(BACKFILL_META_KEY)


class TestApi:

    def _seed(self):
        from db.requests import create_request
        now = int(time.time())
        rows = [
            ('pct exec 103 -- uptime', 'pct:103'),
            ('pct exec 106 -- uptime', 'pct:106'),
            ('qm guest exec 201 -- uptime', 'qm:201'),
            ('docker exec sonarr uptime', 'docker:sonarr'),
            ('uptime', ''),
            ('pct exec 103 -- uptime && pct exec 106 -- uptime', 'multi'),
        ]
        for command, target in rows:
            create_request('10.0.0.1', command, status='auto-approved', ttl=0,
                           inner_target=target)
        _legacy_row('pct exec 888 -- uptime', created_at=now - 20 * 86400,
                    inner_target='pct:888')
        _legacy_row('pct exec 999 -- uptime', created_at=now - 40 * 86400,
                    inner_target='pct:999')

    def test_f7_inner_targets_endpoint(self, auth_client, gateway_headers):
        self._seed()
        response = auth_client.get('/api/gateways/10.0.0.1/inner-targets?days=30')
        assert response.status_code == 200
        data = response.json()
        assert data['pct'] == ['103', '106', '888']
        assert data['qm'] == ['201']
        assert data['docker'] == ['sonarr']
        assert data['count'] == 5  # 3 pct + 1 qm + 1 docker
        # the days window excludes older rows
        data = auth_client.get('/api/gateways/10.0.0.1/inner-targets?days=7').json()
        assert data['pct'] == ['103', '106']
        # only this gateway's rows count
        data = auth_client.get('/api/gateways/192.0.2.99/inner-targets').json()
        assert data == {'pct': [], 'qm': [], 'docker': [], 'count': 0}

    def test_f7_inner_targets_endpoint_requires_a_session(self, client,
                                                          gateway_headers):
        response = client.get('/api/gateways/10.0.0.1/inner-targets')
        assert response.status_code in (401, 403)

    def test_f7_statistics_inner_targets(self, auth_client, gateway_headers):
        self._seed()
        data = auth_client.get('/api/statistics?days=14').json()
        counts = {row['target']: row['count'] for row in data['inner_targets']}
        assert counts['pct:103'] == 1
        assert counts['qm:201'] == 1
        assert counts['docker:sonarr'] == 1
        # `multi` and empty targets are not attributed to a single target
        assert 'multi' not in counts
        assert '' not in counts

    def test_f7_requests_filter_by_inner_target(self, auth_client, gateway_headers):
        self._seed()
        rows = auth_client.get('/api/requests?inner_target=pct:103').json()
        assert rows
        assert all(row['inner_target'] == 'pct:103' for row in rows)
        # exact match only — a prefix is not a match
        assert auth_client.get('/api/requests?inner_target=pct:10').json() == []
        # the filter composes with the session filter
        rows = auth_client.get('/api/requests?inner_target=multi').json()
        assert [row['inner_target'] for row in rows] == ['multi']


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def test_f7_chip_rendered_and_escaped(self):
        payload = '<img src=x onerror=alert(1)>'
        out = run({'action': 'chip', 'value': payload + ':x'})
        assert payload not in out['chip']
        assert '&lt;img' in out['chip']
        assert payload in out['escapeCalls']
        # a normal chip names the target and offers the history filter
        out = run({'action': 'chip', 'value': 'pct:103'})
        assert 'pct 103' in out['chip']
        assert 'inner-chip-pct' in out['chip']
        assert 'filterByInnerTarget' in out['chip']

    def test_f7_override_warning_only_with_pct_qm(self):
        assert run({'action': 'override', 'data': {
            'pct': [], 'qm': [], 'docker': ['sonarr'], 'count': 1}})['warning'] == ''
        assert run({'action': 'override', 'data': {
            'pct': [], 'qm': [], 'docker': [], 'count': 0}})['warning'] == ''
        out = run({'action': 'override', 'data': {
            'pct': ['103', '106'], 'qm': [], 'docker': ['sonarr'], 'count': 3}})
        assert 'reaches 2 containers/VMs via pct/qm' in out['warning']
        assert 'also covers commands run inside them' in out['warning']
        out = run({'action': 'override', 'data': {
            'pct': [], 'qm': ['201'], 'docker': [], 'count': 1}})
        assert 'reaches 1 container/VM' in out['warning']
