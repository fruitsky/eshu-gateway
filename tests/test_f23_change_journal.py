"""Chunk F23 — the change journal.

Synthetic data only: `192.0.2.x`, `web01`, `*.example.test`. Pulse is stubbed
at `core.integration_proxy.execute_integration_call` (so `read_policy` still
runs for real), and the three sources are written straight into the temp DB
with explicit timestamps.
"""
import json
import time

import pytest

from core.change_journal import (
    ALERT_CACHE_TTL, AUDIT_CHANGE_EVENTS, CMD_CHARS, DEFAULT_DAYS, MAX_DAYS,
    _alert_parts, attach_ties, changes_for_host, clamp_days,
    host_identities, _alert_start,
    _owned, reset_alert_cache,
)
from db.audit import record_audit_event
from db.core import db_conn
from db.integrations import create_integration, create_tool
from db.inventory import replace_source_items
from core.pulse_seed import seed_pulse_tools

HOST = '192.0.2.10'          # the gateway host under test
OTHER = '192.0.2.11'         # another gateway: never on this page
PULSE_URL = 'https://192.0.2.60/api'

NOW = 1_800_000_000


def _request(target=HOST, command='systemctl restart nginx', status='approved',
             at=None, session='', inner='', agent='', excluded=0):
    at = NOW if at is None else at
    with db_conn() as conn:
        cur = conn.execute(
            'INSERT INTO requests (target_ip, command, status, created_at, '
            'expires_at, reason, session_id, execution_id, inner_target, '
            'agent, excluded) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (target, command, status, at, at + 90, '', session, '', inner,
             agent, excluded))
        conn.commit()
        return cur.lastrowid


def _result(request_id, exit_code=0, at=None):
    at = NOW if at is None else at
    with db_conn() as conn:
        conn.execute(
            'INSERT INTO request_results (request_id, exit_code, duration_ms, '
            'output, truncated, created_at) VALUES (?,?,?,?,?,?)',
            (request_id, exit_code, 12, '', 0, at))
        conn.commit()


def _session(sid, agent='claude', goal='fix nginx'):
    with db_conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO sessions (id, agent, goal, source, '
            'started_at, last_seen) VALUES (?,?,?,?,?,?)',
            (sid, agent, goal, 'agent', NOW - 600, NOW))
        conn.commit()


def _call(integration, tool, method, path, at=None, outcome='ok', host='',
          ips='', status=200, agent='operator'):
    at = NOW if at is None else at
    with db_conn() as conn:
        conn.execute(
            'INSERT INTO integration_calls (integration, tool, agent, method, '
            'path, status_code, latency_ms, response_summary, response_bytes, '
            'truncated, outcome, session_id, execution_id, decided_by, '
            'created_at, host, resolved_ips) VALUES '
            '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (integration, tool, agent, method, path, status, 5, '', 0, 0,
             outcome, '', '', '', at, host, ips))
        conn.commit()


def _audit(event_type, ip, details, at=None):
    at = NOW if at is None else at
    record_audit_event(event_type, ip, 'web01', details)
    with db_conn() as conn:
        conn.execute('UPDATE audit_log SET timestamp = ? WHERE event_type = ? '
                     'AND gateway_ip = ?', (at, event_type, ip))
        conn.commit()


def _pulse(monkeypatch, alerts, error=None):
    """Patch the HTTP round trip only: read_policy still runs for real."""
    _pulse_integration()
    calls = []

    def fake(integration, tool, args, agent='', **_kw):
        calls.append((integration.get('name'), tool.get('name'), dict(args)))
        if error is not None:
            raise error
        return {'body': json.dumps(alerts), 'status_code': 200}

    # `_call_tool` imported the function into its own namespace, so patch it there.
    monkeypatch.setattr('core.inventory.execute_integration_call', fake)
    return calls


def _pulse_integration():
    """One Pulse integration with the curated tools; idempotent per test."""
    from db.integrations import get_integrations
    if not any(i.get('name') == 'pulse' for i in get_integrations()):
        iid = create_integration('pulse', PULSE_URL, 'token', 'x', kind='pulse')
        seed_pulse_tools(iid)


def _alert(aid, start, resource='web01', level='warning', message=None,
           resource_id='pve:pve01:103'):
    return {'id': aid, 'level': level, 'type': 'cpu', 'resourceId': resource_id,
            'resourceName': resource, 'node': 'pve01',
            'message': message or 'CPU high on %s' % resource,
            'startTime': start, 'lastSeen': start, 'acknowledged': False}


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%S.000Z')


def _inventory(ip=HOST, name='web01', vmid='103', node='pve01'):
    replace_source_items('pve', [
        {'kind': 'guest', 'key': vmid, 'name': name, 'ips': [ip],
         'host': node, 'vmid': vmid, 'extra': {}},
    ])


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_alert_cache()
    yield
    reset_alert_cache()


class TestRequestSource:

    def test_f23_write_on_the_host_appears(self, monkeypatch):
        _pulse(monkeypatch, [])
        rid = _request()
        _result(rid, exit_code=0)
        data = changes_for_host(HOST, days=7, now=NOW)
        rows = [c for c in data['changes'] if c['kind'] == 'ssh']
        assert len(rows) == 1
        assert rows[0]['request_id'] == rid
        assert rows[0]['label'] == '#%d' % rid
        assert rows[0]['exit_code'] == 0
        assert rows[0]['status'] == 'approved'
        assert rows[0]['command'] == 'systemctl restart nginx'

    def test_f23_reads_other_hosts_and_unrun_rows_do_not_appear(self, monkeypatch):
        _pulse(monkeypatch, [])
        _request(command='ls -la /var/log')                 # a read
        _request(command='cat /etc/hosts')                  # a read
        _request(target=OTHER)                              # another host
        _request(status='pending')                          # never ran
        _request(status='denied')                           # never ran
        _request(at=NOW - 8 * 86400)                        # outside 7 days
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['changes'] == []

    def test_f23_unknown_step_is_a_change(self, monkeypatch):
        _pulse(monkeypatch, [])
        _request(command='mysterytool --go /etc/x')
        data = changes_for_host(HOST, days=7, now=NOW)
        assert [c['kind'] for c in data['changes']] == ['ssh']

    def test_f23_inner_target_names_the_guest(self, monkeypatch):
        _pulse(monkeypatch, [])
        _request(command='pct exec 101 -- systemctl restart nginx',
                 inner='101', session='sess-1')
        _session('sess-1')
        data = changes_for_host(HOST, days=7, now=NOW)
        row = data['changes'][0]
        assert row['inner_target'] == '101'
        assert row['session_id'] == 'sess-1'
        assert row['agent'] == 'claude'          # from the session when the row has none
        assert row['session_goal'] == 'fix nginx'

    def test_f23_command_is_scrubbed_and_capped(self, monkeypatch):
        _pulse(monkeypatch, [])
        secret = 'synthetic-pass-123'
        long_tail = 'x' * 300
        _request(command='systemctl restart nginx --password=%s %s'
                 % (secret, long_tail))
        data = changes_for_host(HOST, days=7, now=NOW)
        row = data['changes'][0]
        assert secret not in row['command']
        assert secret not in row['command_full']
        assert len(row['command']) <= CMD_CHARS
        assert 'systemctl restart nginx' in row['command']

    def test_f23_newest_first(self, monkeypatch):
        _pulse(monkeypatch, [])
        _request(at=NOW - 300)
        _request(at=NOW - 60)
        data = changes_for_host(HOST, days=7, now=NOW)
        assert [c['at'] for c in data['changes']] == [NOW - 60, NOW - 300]


class TestIntegrationSource:

    def test_f23_integration_write_on_the_host_appears(self, monkeypatch):
        _pulse(monkeypatch, [])
        create_integration('pve', 'https://%s:8006/api2/json' % HOST, 'token', 'x',
                           kind='proxmox')
        _call('pve', 'start_vm', 'POST', '/nodes/pve01/qemu/104/status/start')
        data = changes_for_host(HOST, days=7, now=NOW)
        rows = [c for c in data['changes'] if c['kind'] == 'integration']
        assert len(rows) == 1
        assert rows[0]['integration'] == 'pve'
        assert rows[0]['method'] == 'POST'
        assert rows[0]['label'] == 'pve.start_vm'

    def test_f23_integration_reads_and_failures_do_not_appear(self, monkeypatch):
        _pulse(monkeypatch, [])
        create_integration('pve', 'https://%s:8006/api2/json' % HOST, 'token', 'x',
                           kind='proxmox')
        _call('pve', 'cluster_resources', 'GET', '/cluster/resources')
        _call('pve', 'start_vm', 'POST', '/x', outcome='error')
        _call('pve', 'start_vm', 'POST', '/x', outcome='refused')
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['changes'] == []

    def test_f23_a_write_for_another_host_is_not_shown(self, monkeypatch):
        _pulse(monkeypatch, [])
        create_integration('pve2', 'https://%s:8006/api2/json' % OTHER, 'token', 'x',
                           kind='proxmox')
        _call('pve2', 'start_vm', 'POST', '/x')
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['changes'] == []

    def test_f23_ws_write_counts_but_a_read_only_tool_does_not(self, monkeypatch):
        _pulse(monkeypatch, [])
        iid = create_integration('ha', 'https://%s:8123' % HOST, 'token', 'x',
                                 kind='ha')
        create_tool(iid, 'ha_core_restart', 'Restart', 'WS', 'supervisor/api',
                    [], '{}', read_only=False)
        create_tool(iid, 'ha_states', 'States', 'WS', 'get_states',
                    [], '{}', read_only=True)
        _call('ha', 'ha_core_restart', 'WS', 'supervisor/api')
        _call('ha', 'ha_states', 'WS', 'get_states')
        data = changes_for_host(HOST, days=7, now=NOW)
        rows = [c for c in data['changes'] if c['kind'] == 'integration']
        assert [r['tool'] for r in rows] == ['ha_core_restart']

    def test_f23_gateway_update_comes_from_the_audit_log(self, monkeypatch):
        _pulse(monkeypatch, [])
        assert 'version_updated' in AUDIT_CHANGE_EVENTS
        _audit('version_updated', HOST, 'v15.2 → v15.3')
        _audit('jit_approved', HOST, 'JIT #1 approved')     # not a host change
        data = changes_for_host(HOST, days=7, now=NOW)
        rows = [c for c in data['changes'] if c['kind'] == 'audit']
        assert len(rows) == 1
        assert 'v15.3' in rows[0]['command']


class TestAlertTieIn:

    def _setup(self, monkeypatch, alerts):
        _inventory()                     # F14: the alert's names map to the host
        _pulse(monkeypatch, alerts)
        _request(at=NOW - 1800)          # the change

    def test_f23_alert_four_minutes_after_a_change_gets_the_badge(self, monkeypatch):
        self._setup(monkeypatch, [_alert('a1', _iso(NOW - 1800 + 240))])
        data = changes_for_host(HOST, days=7, now=NOW)
        tie = data['alerts'][0]['tie']
        assert tie is not None
        assert tie['gap_min'] == 4
        assert tie['text'] == '+4 min after #%d' % data['changes'][0]['request_id']
        assert data['counts']['ties'] == 1

    def test_f23_alert_40_minutes_after_does_not(self, monkeypatch):
        _inventory()
        _pulse(monkeypatch, [_alert('a1', _iso(NOW - 1200))])
        _request(at=NOW - 3600)          # 40 minutes before the alert
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['alerts'][0]['tie'] is None

    def test_f23_alert_before_the_change_does_not(self, monkeypatch):
        self._setup(monkeypatch, [_alert('a1', _iso(NOW - 2400))])
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['alerts'][0]['tie'] is None

    def test_f23_nearest_change_is_named_and_the_others_counted(self, monkeypatch):
        _inventory()
        _pulse(monkeypatch, [_alert('a1', _iso(NOW - 600))])
        _request(at=NOW - 1800)     # #1
        _request(at=NOW - 1200)     # #2
        _request(at=NOW - 900)      # #3, the nearest
        data = changes_for_host(HOST, days=7, now=NOW)
        tie = data['alerts'][0]['tie']
        assert tie['others'] == 2
        assert tie['gap_min'] == 5
        assert tie['label'] == '#%d' % data['changes'][0]['request_id']

    def test_f23_alert_on_another_host_is_not_shown(self, monkeypatch):
        _inventory()
        _pulse(monkeypatch, [_alert('a1', _iso(NOW - 60), resource='nas-other',
                                    resource_id='pve:pve01:200')])
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['alerts'] == []

    def test_f23_attach_ties_is_pure_enough_to_call_alone(self):
        changes = [{'at': 100, 'id': 'request:7', 'request_id': 7, 'label': '#7'}]
        alerts = [{'at': 340, 'id': 'a'}]
        attach_ties(changes, alerts)
        assert alerts[0]['tie']['gap_min'] == 4
        assert alerts[0]['tie']['request_id'] == 7

    def test_f23_alert_start_time_forms(self):
        assert _alert_start(1700000000) == 1700000000
        assert _alert_start('1700000000') == 1700000000
        assert _alert_start('2026-08-02T12:07:00.622Z') == 1785672420
        assert _alert_start('') == 0
        assert _alert_start('not a date') == 0


class TestFalseTies:

    def test_f23_a_short_name_does_not_own_a_longer_one(self, monkeypatch):
        """AJ1 end to end: a guest named `web` must not own `webhook`'s alert,
        which is what made a false "+N min after" tie possible."""
        replace_source_items('pve', [
            {'kind': 'guest', 'key': '103', 'name': 'web', 'ips': [HOST],
             'host': 'pve01', 'vmid': '103', 'extra': {}},
        ])
        _pulse(monkeypatch, [
            _alert('sibling', _iso(NOW - 60), resource='webhook.example.net',
                   resource_id='pve:pve01:200'),
            _alert('own', _iso(NOW - 120), resource='web',
                   resource_id='pve:pve01:103'),
        ])
        data = changes_for_host(HOST, days=7, now=NOW)
        assert [a['id'] for a in data['alerts']] == ['own']

    def test_f23_the_cluster_prefix_is_not_a_host_part(self):
        # `pve:pve3:104` is pve3's alert, not the `pve` host's.
        sibling = _alert('s', _iso(NOW), resource='pve3',
                         resource_id='pve:pve3:104')
        assert 'pve' not in _alert_parts(sibling)
        assert 'pve3' in _alert_parts(sibling)
        own = _alert('o', _iso(NOW), resource='pve', resource_id='pve:pve:104')
        assert 'pve' in _alert_parts(own)

    def test_f23_a_pve_write_on_pve3_is_not_shown_on_pve(self, monkeypatch):
        create_integration('pve', 'https://%s:8006/api2/json' % HOST, 'token', 'x',
                           kind='proxmox')
        _pulse(monkeypatch, [])
        # A call whose host is `pve3`: the same base URL, a different node column.
        _call('pve', 'start_vm', 'POST', '/x', host='pve3')
        assert changes_for_host(HOST, days=7, now=NOW)['changes'] == []


class TestPulseAvailability:

    def test_f23_pulse_down_still_shows_changes(self, monkeypatch):
        _pulse_integration()
        _pulse(monkeypatch, None, error=RuntimeError('boom'))
        _request()
        data = changes_for_host(HOST, days=7, now=NOW)
        assert len(data['changes']) == 1
        assert data['alerts'] == []
        assert data['alerts_error'].startswith('alerts unavailable')

    def test_f23_no_pulse_integration(self, monkeypatch):
        _request()
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['alerts_error'] == 'alerts unavailable (no Pulse integration)'
        assert len(data['changes']) == 1

    def test_f23_a_non_read_pulse_tool_is_refused(self, monkeypatch):
        _pulse_integration()
        calls = _pulse(monkeypatch, [_alert('a1', _iso(NOW - 60))])
        with db_conn() as conn:
            conn.execute("UPDATE integration_tools SET read_only = 0 "
                         "WHERE name = 'list_alerts'")
            conn.commit()
        data = changes_for_host(HOST, days=7, now=NOW)
        assert data['alerts'] == []
        assert 'not_a_read' in data['alerts_error']
        assert calls == []          # refused before anything was sent

    def test_f23_pulse_is_read_once_and_cached(self, monkeypatch):
        _pulse_integration()
        calls = _pulse(monkeypatch, [_alert('a1', _iso(NOW - 60))])
        changes_for_host(HOST, days=7, now=NOW)
        changes_for_host(HOST, days=7, now=NOW + ALERT_CACHE_TTL - 1)
        assert len(calls) == 1
        changes_for_host(HOST, days=7, now=NOW + ALERT_CACHE_TTL + 1)
        assert len(calls) == 2


class TestArguments:

    def test_f23_days_are_clamped(self):
        assert clamp_days(7) == 7
        assert clamp_days(0) == 1
        assert clamp_days(99) == MAX_DAYS
        assert clamp_days(None) == DEFAULT_DAYS
        assert clamp_days('x') == DEFAULT_DAYS

    def test_f23_bad_host_refused(self):
        with pytest.raises(ValueError):
            changes_for_host('not-an-ip')

    def test_f23_unknown_host_is_empty_not_an_error(self, monkeypatch):
        _pulse(monkeypatch, [])
        data = changes_for_host('192.0.2.99', days=7, now=NOW)
        assert data['changes'] == [] and data['alerts'] == []

    def test_f23_identity_covers_the_ip_and_its_guests(self):
        _inventory()
        keys = host_identities(HOST)
        assert HOST in keys and 'web01' in keys and '103' in keys
        assert _owned('https://192.0.2.10:8006/api2/json', keys) is True
        assert _owned('https://192.0.2.100/api', keys) is False
        assert _owned('pve:pve01:103', keys) is True
        assert _owned('pve:pve01:1030', keys) is False

    def test_f23_a_name_matches_only_as_a_whole_label(self):
        # AJ1: `pve` owns a label of its own, not every name that starts with it.
        pve = {'pve'}
        assert _owned('pve3', pve) is False
        assert _owned('pve-backup', pve) is False
        assert _owned('pve-backup.example.net', pve) is False
        assert _owned('pve.example.net', pve) is True
        assert _owned('https://pve.example.net:8006/', pve) is True
        assert _owned('pve:pve:104', pve) is True
        assert _owned('node pve is online', pve) is True
        web = {'web'}
        assert _owned('webhook.example.net', web) is False
        assert _owned('web.example.net', web) is True
        # The IP and vmid rule is unchanged.
        assert _owned('192.0.2.100', {'192.0.2.10'}) is False
        assert _owned('1040', {'104'}) is False
        assert _owned('qemu/104', {'104'}) is True


class TestApi:

    def test_f23_api_requires_the_operator(self, client, monkeypatch):
        _pulse(monkeypatch, [])
        res = client.get('/api/hosts/%s/changes' % HOST)
        assert res.status_code == 401

    def test_f23_api_returns_the_timeline(self, auth_client, monkeypatch):
        now = int(time.time())
        _inventory()
        _pulse(monkeypatch, [_alert('a1', _iso(now - 60))])
        rid = _request(at=now - 120)
        res = auth_client.get('/api/hosts/%s/changes?days=7' % HOST)
        assert res.status_code == 200
        body = res.json()
        assert body['host'] == HOST and body['days'] == 7
        assert body['changes'][0]['request_id'] == rid
        assert body['alerts'][0]['id'] == 'a1'
        assert body['counts']['changes'] == 1

    def test_f23_api_clamps_and_validates(self, auth_client, monkeypatch):
        _pulse(monkeypatch, [])
        assert auth_client.get(
            '/api/hosts/%s/changes?days=999' % HOST).json()['days'] == MAX_DAYS
        assert auth_client.get(
            '/api/hosts/%s/changes?days=0' % HOST).json()['days'] == 1
        assert auth_client.get(
            '/api/hosts/not-an-ip/changes').status_code == 400
