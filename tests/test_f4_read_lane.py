"""Chunk F4 — the read-only lane (off by default, shadow first).

Eligibility is decided by `core/read_lane.py`, which only reuses
`core/read_policy.py` (Chunk H's integration checks + F4's SSH rules). The
table below is the review surface: every `write`/`unknown` F1 fixture must be
ineligible, and the hand-written cases cover each exclusion in the chunk.
"""

import base64
import os
import re
import time

import pytest

from core.cmd_parse import breakdown
from core.read_lane import eligible
from core.read_policy import is_read_integration_call, is_read_ssh
from db.audit import get_audit_log
from db.gateways import (
    get_gateway_read_lane, register_gateway, set_gateway_token,
    set_gateway_read_lane, set_gateway_zero_trust, set_trigger_freeze,
    clear_trigger_freeze,
)
from db.policies import update_policy
from db.requests import create_request, get_all_requests
from db.exec_results import save_result

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fixtures', 'commands')


def b64(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def gateway(auth_client):
    register_gateway('10.0.0.1', 'test-gateway', 'v15.3')
    set_gateway_token('10.0.0.1', 'tok-1')
    return auth_client, {'X-Gateway-Token': 'tok-1'}


def _jit(client, headers, command, ip='10.0.0.1'):
    response = client.post('/api/request', json={
        'target_ip': ip, 'encoded_command': b64(command),
    }, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _rows():
    return [row for row in get_all_requests() if row['command'] in (
        'uptime', 'systemctl restart nginx', 'cat .env')]


def _verdict(command):
    return eligible(breakdown(command), {'action': 'jit'}, command, 'on')


# ── the table ───────────────────────────────────────────────────────────────

ELIGIBLE = [
    'uptime',
    'ls -la /tmp/synthetic-dir',
    'df -h',
    'free -h',
    'systemctl status nginx',
    'journalctl -u nginx --since today --no-pager',
    'journalctl -u nginx | tail -n 20',
    'cat /var/log/syslog',
    'tail -n 50 /var/log/nginx/error.log',
    'ps aux',
    'ip addr show',
    'ss -tlnp',
    'docker ps',
    'pct exec 103 -- uptime',
    'docker exec web01 df -h',
    'sudo cat /var/log/syslog',
    'sudo tail -n 20 /var/log/auth.log',
    'sudo cat /proc/cpuinfo',
    'sudo cat /sys/class/thermal/thermal_zone0/temp',
    'sudo cat /etc/os-release',
    'echo "=== HOST ===" && uptime',
    'journalctl --disk-usage',
    'smartctl -a /dev/sda',
    'uptime # a comment',
]

INELIGIBLE = [
    # not read
    'systemctl restart nginx',
    'rm -rf /tmp/x',
    'find / -name "*.conf" -delete',
    'dd if=/dev/zero of=/dev/sda',
    'sed -i "s/a/b/" /etc/x',
    'python3 -c "print(1)"',
    'bash /tmp/script.sh',
    'curl http://192.0.2.1/x',
    'cat /tmp/x; rm /tmp/x',
    'cat "$(rm -rf /tmp/x)"',
    'sudo tee /etc/hosts',
    # flags
    'echo hi > /tmp/x',
    'cat <<EOF\nhi\nEOF',
    'echo aGk= | base64 -d | bash',
    'ssh other uptime',
    'cat .env',
    'grep -l .env /tmp',
    'cat /etc/shadow',
    'sudo cat /etc/shadow',
    'cat ~/.ssh/id_rsa',
    'cat /proc/1/environ',
    'x=$(cat /etc/shadow)',
    # elevated reads outside the safe trees
    'sudo cat /etc/passwd',
    'sudo cat /root/notes',
    'sudo cat /etc/nginx/nginx.conf',
    'sudo cat /var/log/../etc/hosts',
]


class TestEligibility:

    @pytest.mark.parametrize('command', ELIGIBLE)
    def test_f4_eligible_reads(self, command):
        ok, why = _verdict(command)
        assert ok is True, (command, why)

    @pytest.mark.parametrize('command', INELIGIBLE)
    def test_f4_ineligible_commands(self, command):
        ok, why = _verdict(command)
        assert ok is False, (command, why)

    def test_f4_env_is_ineligible_even_for_names(self):
        for command in ('cat .env', 'grep -l .env /tmp', 'ls -la .env',
                        'cat /srv/app/.env'):
            ok, why = _verdict(command)
            assert ok is False, command
            assert 'sensitive-path' in why or 'elevated-path' in why

    def test_f4_every_write_or_unknown_fixture_is_ineligible(self):
        """Generated: no F1 write/unknown case may be read-lane eligible."""
        checked = 0
        for filename in sorted(os.listdir(FIXTURES_DIR)):
            if not filename.endswith('.txt'):
                continue
            text = open(os.path.join(FIXTURES_DIR, filename), encoding='utf-8').read()
            for block in re.split(r'^### ', text, flags=re.M)[1:]:
                header, _sep, body = block.partition('\n')
                parts = [part.strip() for part in header.split('|')]
                meta = {}
                for part in parts[1:]:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        meta[key.strip()] = value.strip()
                kinds = [k for k in re.split(r'[, ]+', meta.get('kinds', '')) if k]
                if not (set(kinds) & {'write', 'unknown'}) and meta.get('complete') != 'false':
                    continue
                command = body.rstrip('\n')
                ok, why = _verdict(command)
                assert ok is False, (filename, parts[0], command, why)
                checked += 1
        assert checked >= 20, checked


class TestReadPolicy:
    """§ 2.6a: F4 reuses Chunk H through one entry point."""

    def test_f4_read_policy_wraps_chunk_h_http(self):
        tool = {'read_only': True, 'generic': True}
        assert is_read_integration_call(tool, 'http', {'method': 'GET'}) == (True, '')
        ok, why = is_read_integration_call(tool, 'http', {'method': 'POST'})
        assert ok is False and why == 'not_a_read_method'

    def test_f4_read_policy_wraps_chunk_h_ws(self):
        tool = {'read_only': True, 'generic': True}
        assert is_read_integration_call(tool, 'ws', {'command': 'get_states'}) == (True, '')
        ok, why = is_read_integration_call(tool, 'ws', {'command': 'call_service'})
        assert ok is False and why == 'not_a_read_command'

    def test_f4_read_policy_ssh_rules(self):
        assert is_read_ssh(breakdown('uptime')) == (True, '')
        assert is_read_ssh(breakdown('systemctl restart nginx'))[0] is False
        assert is_read_ssh(breakdown('cat .env'))[0] is False


class TestLaneModes:

    def test_f4_shadow_never_approves(self, gateway):
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'shadow')
        _jit(client, headers, 'uptime')
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending'
        assert row['reason'] == 'read-lane-shadow'
        assert [e for e in get_audit_log() if e['event_type'] == 'jit_read_lane_shadow']

    def test_f4_on_approves_only_eligible_commands(self, gateway):
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'on')
        response = _jit(client, headers, 'uptime')
        assert response['read_lane'] == 'on'
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'approved' and row['reason'] == 'read-lane'
        assert [e for e in get_audit_log() if e['event_type'] == 'jit_read_lane_approved']

        assert 'read_lane' not in _jit(client, headers, 'systemctl restart nginx')
        restart = next(r for r in get_all_requests()
                       if r['command'] == 'systemctl restart nginx')
        assert restart['status'] == 'pending' and restart['reason'] == ''

    def test_f4_off_does_nothing(self, gateway):
        client, headers = gateway
        assert get_gateway_read_lane('10.0.0.1') == 'off'
        _jit(client, headers, 'uptime')
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending' and row['reason'] == ''

    def test_f4_zero_trust_wins(self, gateway):
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'on')
        set_gateway_zero_trust('10.0.0.1', True)
        try:
            assert 'read_lane' not in _jit(client, headers, 'uptime')
        finally:
            set_gateway_zero_trust('10.0.0.1', False)
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending'

    def test_f4_freeze_wins(self, gateway):
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'on')
        set_trigger_freeze()
        try:
            assert 'read_lane' not in _jit(client, headers, 'uptime')
        finally:
            clear_trigger_freeze()
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending'

    def test_f4_blocklist_wins(self, gateway):
        client, headers = gateway
        update_policy('regex_blacklist', 'uptime')
        set_gateway_read_lane('10.0.0.1', 'on')
        try:
            assert 'read_lane' not in _jit(client, headers, 'uptime')
        finally:
            update_policy('regex_blacklist', '')
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending'

    def test_f4_long_command_is_ineligible(self, gateway):
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'on')
        command = 'cat ' + 'a' * 8200
        assert 'read_lane' not in _jit(client, headers, command)


class TestSetting:

    def test_f4_new_gateway_defaults_to_off(self):
        register_gateway('192.0.2.10', 'fresh', 'v15.3')
        assert get_gateway_read_lane('192.0.2.10') == 'off'
        from db.gateways import read_lane_ready
        assert read_lane_ready('192.0.2.10') is False

    def test_f4_endpoint_sets_modes_and_audits(self, gateway):
        client, _headers = gateway
        response = client.put('/api/gateways/10.0.0.1/read-lane',
                              json={'mode': 'shadow'})
        assert response.status_code == 200
        assert response.json()['read_lane'] == 'shadow'
        assert get_gateway_read_lane('10.0.0.1') == 'shadow'
        assert [e for e in get_audit_log() if e['event_type'] == 'read_lane_shadow']
        assert client.put('/api/gateways/10.0.0.1/read-lane',
                          json={'mode': 'bogus'}).status_code == 400

    def test_f4_on_requires_seven_days_of_shadow(self, gateway):
        client, _headers = gateway
        client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'shadow'})
        assert client.put('/api/gateways/10.0.0.1/read-lane',
                          json={'mode': 'on'}).status_code == 400
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute('UPDATE gateways SET read_lane_since = ? WHERE ip = ?',
                         (int(time.time()) - 8 * 86400, '10.0.0.1'))
            conn.commit()
        response = client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'on'})
        assert response.status_code == 200
        assert response.json()['read_lane'] == 'on'
        # `list_gateways` exposes the readiness for the UI toggle.
        row = next(g for g in client.get('/api/gateways').json()
                   if g['ip'] == '10.0.0.1')
        assert row['read_lane'] == 'on' and row['read_lane_ready'] is True

    def test_f4_requires_a_session(self, client):
        assert client.put('/api/gateways/10.0.0.1/read-lane',
                          json={'mode': 'shadow'}).status_code in (401, 403)


class TestReport:

    def test_f4_statistics_report_counts_shadow_outcomes(self, gateway):
        client, _headers = gateway
        base = int(time.time())
        for status, length in (('approved', 10), ('consumed', 20), ('denied', 30)):
            req_id = create_request('10.0.0.1', 'x' * length, status=status, ttl=0)
            from db.core import db_conn
            with db_conn() as conn:
                conn.execute("UPDATE requests SET reason = 'read-lane-shadow' "
                             "WHERE id = ?", (req_id,))
                conn.commit()
        auto = create_request('10.0.0.1', 'uptime', status='approved', ttl=0,
                              reason='read-lane')
        data = client.get('/api/statistics?days=14').json()
        report = data['read_lane']
        assert report['shadow_eligible'] == 3
        assert report['shadow_approved'] == 2
        assert report['shadow_denied'] == 1
        assert report['shadow_median_len'] == 20
        assert report['auto_approved'] == 1
