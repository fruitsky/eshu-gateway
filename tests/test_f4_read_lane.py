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
from core.read_policy import (
    READ_HARMLESS_FLAGS, READ_LANE_VERBS, is_read_integration_call, is_read_ssh,
)
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
    # status verbs (no file operands)
    'uptime',
    'df -h',
    'free -h',
    'systemctl status nginx',
    'systemctl is-active nginx',
    'systemctl is-enabled nginx',
    'systemctl list-units --type=service',
    'ip addr show',
    'ip route',
    'ss -tlnp',
    'ps aux',
    'uname -a',
    'hostname',
    'lsblk',
    'lscpu',
    'whoami',
    'id',
    'date',
    'nproc',
    'journalctl -u nginx -n 200 --no-pager',
    'journalctl -u nginx -n 1000 --no-pager',
    'journalctl --disk-usage',
    'docker ps',
    'docker stats --no-stream',
    'docker images',
    'pct list',
    'qm list',
    'zpool status',
    'zfs list',
    # file readers, only inside the safe trees
    'cat /var/log/syslog',
    'head -n 50 /var/log/nginx/error.log',
    'tail -n 50 /var/log/syslog',
    'grep -e error /var/log/syslog',
    'grep -r -e error /var/log',
    'ls -R /var/log',
    'ps -ef',
    'head -c 1048576 /var/log/syslog',
    'wc -l /var/log/syslog',
    'ls -la /var/log',
    'stat /var/log/syslog',
    'du -sh /var/log',
    'cat /proc/cpuinfo',
    'cat /sys/class/thermal/thermal_zone0/temp',
    'cat /etc/os-release',
    'cat /etc/hostname',
    'cat /etc/hosts',
    'cat /etc/resolv.conf',
    'sudo cat /var/log/syslog',
]

INELIGIBLE = [
    # probe class: credential files F1 does not know, bare and relative
    'cat config.yml',
    'cat /srv/app/config.yml',
    'cat /home/hermes/.bash_history',
    'cat /tmp/synthetic-x',
    'ls -la /tmp',
    'du -sh /home',
    # probe class: raw block devices and /dev
    'head -c 512 /dev/sda',
    'cat /dev/zero',
    # probe class: /proc links into the real filesystem and memory
    'cat /proc/1234/root/etc/passwd',
    'cat /proc/1234/cwd/config.yml',
    'cat /proc/1234/fd/0',
    'cat /proc/1234/map_files/x',
    'cat /proc/1234/mem',
    'cat /proc/self/environ',
    'cat /proc/kcore',
    'cat /proc/kmsg',
    # probe class: globs and `..` in a path operand
    'cat /var/log/*/../../etc/hosts',
    'cat /var/log/../etc/hosts',
    'cat /var/log/*.log',
    'cat /var/log/{syslog,auth.log}',
    # probe class: status verbs that print configuration or write
    'systemctl show nginx',
    'systemctl cat nginx',
    'systemctl start nginx',
    'docker inspect web01',
    'docker stats',
    'journalctl -f',
    'journalctl -u nginx --no-pager',
    'journalctl -u nginx -n 5000 --no-pager',
    'journalctl --file /var/log/syslog -n 10',
    'tail -f /var/log/syslog',
    'tail -F /var/log/syslog',
    'tail -n 5000 /var/log/syslog',
    # probe class: verbs outside the allowlist
    'crontab -l',
    'git -C /srv/app log',
    'less /var/log/syslog',
    'tar -c /var/log',
    'find /var/log -name synthetic',
    'xargs cat',
    'awk "BEGIN{print 1}"',
    'sed -n "1p" /var/log/syslog',
    'dd if=/dev/zero of=/tmp/synthetic-x',
    'curl http://192.0.2.10/x',
    'wget http://192.0.2.10/x',
    'sleep 100000',
    'python3 -c "print(1)"',
    'bash /tmp/synthetic.sh',
    'date -s 2026-01-01',
    'hostname web02',
    'ip netns exec ns1 uptime',
    'env A=1 uptime',
    'timeout 5 uptime',
    'runuser -u root -- uptime',
    # existing exclusions
    'systemctl restart nginx',
    'rm -rf /tmp/synthetic-x',
    'echo hi > /tmp/synthetic-x',
    'cat <<EOF\nhi\nEOF',
    'echo aGk= | base64 -d | bash',
    'ssh 192.0.2.10 uptime',
    'cat .env',
    'grep -l .env /var/log',
    'cat /etc/shadow',
    'cat /root/notes',
    'cat ~/.ssh/id_rsa',
    'cat --file=/etc/shadow',
    'cat /var/log/syslog; rm /var/log/synthetic',
    'cat "$(rm -rf /tmp/synthetic-x)"',
    # R5: recursion under /proc or /sys, and -R / -d recurse anywhere
    'grep -R -e error /var/log',
    'grep -R -e error /proc',
    'grep -r -e error /proc',
    'grep -r -e error /sys',
    'grep --recursive -e error /proc',
    'grep -d recurse -e error /var/log',
    'ls -R /proc',
    'ls -R /sys',
    'du -sh /proc',
    'du -sh /sys',
    # R6: ps environment output
    'ps e',
    'ps eww',
    'ps auxe',
    'ps -eo pid,environ',
    'ps --format=environ',
    # R7: endless or interval forms
    'free -s 1',
    'free -c 5',
    'zpool status 5',
    'zpool list 10',
    'zpool status -T',
    'ss -E',
    'ss --events',
    # R8: journal sources outside the default
    'journalctl --file=/var/log/syslog -n 10',
    'journalctl --directory=/var/log/journal -n 10',
    'journalctl --root=/ -n 10',
    'journalctl --image=synthetic -n 10',
    'journalctl -m -n 10',
    # S2: head/tail byte cap
    'head -c 2000000 /var/log/syslog',
    'tail -c 2000000 /var/log/syslog',
    # S3: /sys/firmware
    'cat /sys/firmware/efi/efivars/synthetic',
    # R4: the guest never opted in
    'pct exec 103 -- uptime',
    'docker exec web01 df -h',
    'qm guest exec 201 -- uptime',
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

    def test_f4_every_eligible_verb_is_on_the_allowlist(self):
        """R1: every step of an eligible command starts with an allowed verb."""
        allowed = set(READ_LANE_VERBS['status']) | set(READ_LANE_VERBS['file'])
        for command in ELIGIBLE:
            for step in breakdown(command)['steps']:
                verb = (step.get('text') or '').split()[0]
                assert verb in allowed, (command, verb)

    def test_f4_journal_and_tail_line_caps(self):
        """S1: one auto-run read cannot dump a whole journal."""
        assert _verdict('journalctl -n 1000 -u nginx')[0] is True
        assert _verdict('journalctl -n 5000 -u nginx')[0] is False
        assert _verdict('journalctl -u nginx')[0] is False
        assert _verdict('tail -n 1000 /var/log/syslog')[0] is True
        assert _verdict('tail -n 5000 /var/log/syslog')[0] is False

    def test_f4_eligible_cases_have_no_endless_form(self):
        """R7: a static check per verb — no eligible case can run forever."""
        endless = ('-s', '--seconds', '-c', '--count', '--events', '-T')
        for command in ELIGIBLE:
            tokens = command.split()
            if tokens[0] == 'zpool':
                assert not any(token.isdigit() for token in tokens[1:]), command
            for token in tokens[1:]:
                if tokens[0] in ('free', 'ss', 'zpool'):
                    assert token not in endless, (command, token)
            # `follows` is the one flag F1 would set; R3 refuses it.
            assert not (tokens[0] == 'tail' and ('-f' in tokens or '-F' in tokens)), command

    def test_f4_any_flag_blocks(self):
        """R3: flags fail closed; the harmless list is deliberately empty."""
        from core.cmd_parse import breakdown as bd
        assert READ_HARMLESS_FLAGS == frozenset()
        assert bd('tail -f /var/log/syslog')['flags']
        assert _verdict('tail -f /var/log/syslog')[0] is False

    def test_f4_inner_targets_are_ineligible(self):
        """R4: the lane is opted in per gateway, the guest never did."""
        for command in ('pct exec 103 -- uptime', 'qm guest exec 201 -- uptime',
                        'docker exec web01 df -h'):
            ok, why = _verdict(command)
            assert ok is False
            assert why == 'inner-target'

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
