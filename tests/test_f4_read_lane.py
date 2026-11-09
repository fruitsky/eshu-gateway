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
    # status verbs (no file operands; SC2 path-checks any operand, so a unit
    # name is not a read — see the INELIGIBLE list and the open question)
    'uptime',
    'df -h',
    'free -h',
    'systemctl status',
    'systemctl list-units --type=service',
    'systemctl list-timers --no-pager',
    'ip addr show',
    'ip -4 route',
    'ss -tlnp',
    'ss -s',
    'ps aux',
    'ps -ef',
    'uname -a',
    'hostname',
    'lsblk',
    'lsblk -f',
    'lscpu',
    'whoami',
    'id',
    'id -u',
    'date',
    'date -u',
    'nproc',
    'journalctl -u nginx -n 200 --no-pager',
    'journalctl -u nginx -n 1000 --no-pager',
    'journalctl -n 5 --dmesg',
    'journalctl --disk-usage',
    'docker ps',
    'docker ps -a',
    'docker stats --no-stream',
    'docker images -q',
    'pct list',
    'qm list',
    'zpool status',
    'zpool status -v',
    'zpool list -H',
    'zfs list',
    'zfs list -d 1 -t filesystem',
    # file readers, only inside the safe trees
    'head -n 50 /var/log/nginx/error.log',
    'head --lines=50 /var/log/syslog',
    'head -50 /var/log/syslog',
    'tail -n 50 /var/log/syslog',
    'tail -c 4096 /var/log/syslog',
    'grep -m 50 -e error /var/log/syslog',
    'grep -m 10 -i -e error /var/log/syslog',
    'grep -m 10 --regexp=error /var/log/syslog',
    'grep -m 500 -A 1 -e error /var/log/syslog',
    'grep -m 300 -C 1 -e error /var/log/syslog',
    'grep -m 1 -A 500 -e error /var/log/syslog',
    'grep error /proc/1/status',
    'ls -R /var/log',
    'ls -la /var/log',
    'stat /var/log/syslog',
    'stat -f /var/log',
    'du -sh /var/log',
    'du -d 1 /var/log',
    'cat /proc/cpuinfo',
    'cat -n /proc/meminfo',
    'cat /sys/class/thermal/thermal_zone0/temp',
    'cat /etc/os-release',
    'cat /etc/hostname',
    'cat /etc/hosts',
    'cat /etc/resolv.conf',
    'sudo cat /proc/loadavg',
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
    # SC6: /proc links, page maps and huge kernel files
    'cat /proc/1/exe',
    'cat /proc/1/pagemap',
    'cat /proc/kpageflags',
    'cat /sys/kernel/debug/tracing/trace',
    'cat /sys/kernel/tracing/trace',
    'cat /sys/fs/bpf/synthetic',
    'cat /var/log/installer/syslog',
    'cat /var/log/cloud-init.log',
    'cat /var/log/cloud-init-output.log',
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
    # SC2: a status verb reads no file, so a bare name is not a read
    'systemctl status nginx',
    'systemctl is-active nginx',
    'systemctl is-enabled nginx',
    'ip addr show dev eth0',
    'ip link set eth0 down',
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
    # SC1: options are an allowlist, matched exactly, with no abbreviation
    'df --huma',
    'head --line=5 /var/log/syslog',
    'journalctl --line 10',
    'ls --colour=always /var/log',
    'grep --recur -m 5 -e error /var/log',
    'ss -tlnpq',
    'ls -lz /var/log',
    'cat -- /proc/cpuinfo',
    'cat - /proc/cpuinfo',
    'head -n5x /var/log/syslog',
    # SC1: no status verb may take an option that names a file, a host, a
    # machine, a context or a format template
    'docker -H 192.0.2.5 ps',
    'docker --context synthetic ps',
    'systemctl -H 192.0.2.5 status',
    'journalctl --root=/ -n 5',
    'docker ps --format {{.Names}}',
    'ps -o pid,cmd',
    'stat -c %n /var/log/syslog',
    'date +%s',
    'journalctl -o json -n 5',
    'grep -f /etc/passwd /var/log/syslog',
    'grep --exclude-from=/etc/passwd -m 5 -e error /var/log',
    # R2 round 6: recursion is not in the lane at all (grep -r refused)
    'grep -r -m 50 -e error /var/log',
    'grep -r -m 50 -e error /var/log/',
    'grep --recursive -m 50 -e error /var/log',
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
    # S2/SC5: line and byte caps, and no way around them
    'head -c 2000000 /var/log/syslog',
    'tail -c 2000000 /var/log/syslog',
    'head -n 5000 /var/log/syslog',
    'tail --lines=5000 /var/log/syslog',
    'head -n +5 /var/log/syslog',
    'tail -n -5 /var/log/syslog',
    'journalctl -n -1',
    'journalctl --no-tail -n 5',
    # SC5: a log is read through head, tail or `grep -m` only
    'cat /var/log/syslog',
    'wc -l /var/log/syslog',
    'grep -e error /var/log/syslog',
    'grep -r -e error /var/log',
    'cat /var/log/nginx/error.log',
    # S3: /sys/firmware
    'cat /sys/firmware/efi/efivars/synthetic',
    # R4/SC4: the guest never opted in, and the policy checks subcommands
    'pct exec 103 -- uptime',
    'docker exec web01 df -h',
    'qm guest exec 201 -- uptime',
    'pct start 103',
    'zfs destroy tank/data',
    'zpool',
    'docker',
    'ip addr flush dev eth0',
    # SC3: input redirection, here-string or heredoc feeds the step
    'cat /proc/cpuinfo < /etc/shadow',
    'head -n 5 /var/log/syslog < /etc/shadow',
    'grep -m 5 -e error /var/log/syslog < /etc/passwd',
    'cat <<< synthetic',
    'cat <<EOF\nhi\nEOF',
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
        endless = {
            'free': ('-s', '--seconds', '-c', '--count'),
            'ss': ('-E', '--events'),
            'zpool': ('-T', '-t'),
        }
        for command in ELIGIBLE:
            tokens = command.split()
            if tokens[0] == 'zpool':
                assert not any(token.isdigit() for token in tokens[1:]), command
            for token in tokens[1:]:
                if tokens[0] in endless:
                    assert token not in endless[tokens[0]], (command, token)
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


class TestSecurityConditions:
    """SC1–SC8 from the security reviewer's sign-off (Round 5)."""

    def test_f4_sc1_options_are_an_exact_allowlist(self):
        for command in ('df --huma', 'head --line=5 /var/log/syslog',
                        'journalctl --line 10', 'ls --colour=always /var/log',
                        'ss -tlnpq', 'ls -lz /var/log'):
            ok, why = _verdict(command)
            assert ok is False, command
            assert why.startswith('option:'), (command, why)

    def test_f4_sc1_dashdash_and_lone_dash_are_refused(self):
        for command in ('cat -- /proc/cpuinfo', 'cat - /proc/cpuinfo'):
            ok, why = _verdict(command)
            assert ok is False and why == 'dash', (command, why)

    def test_f4_sc1_short_clusters_need_every_letter(self):
        assert _verdict('ss -tlnp')[0] is True
        assert _verdict('ss -tlnpq')[0] is False
        assert _verdict('ls -la /var/log')[0] is True
        assert _verdict('ls -lz /var/log')[0] is False

    def test_f4_sc1_values_are_decided_per_verb(self):
        # `-m` is a grep option (a pattern), not a file-reader option.
        assert _verdict('grep -m 5 -e error /var/log/syslog')[0] is True
        assert _verdict('head -m 5 /var/log/syslog')[0] is False
        # `-c` is bytes for head/tail and not an option for free.
        assert _verdict('head -c 4096 /var/log/syslog')[0] is True
        assert _verdict('free -c 5')[0] is False

    def test_f4_sc1_no_status_option_names_a_file_host_or_format(self):
        for command in ('docker -H 192.0.2.5 ps',
                        'docker --context synthetic ps',
                        'systemctl -H 192.0.2.5 status',
                        'docker ps --format {{.Names}}',
                        'ps -o pid,cmd', 'stat -c %n /var/log/syslog',
                        'date +%s', 'journalctl -o json -n 5',
                        'grep -f /etc/passwd /var/log/syslog'):
            assert _verdict(command)[0] is False, command

    def test_f4_sc2_a_status_operand_is_not_a_read(self):
        """SC2: a word that is not an option or its value is path-checked."""
        for command in ('systemctl status nginx', 'systemctl is-active nginx',
                        'ip addr show dev eth0'):
            ok, why = _verdict(command)
            assert ok is False and why.startswith('operand:'), (command, why)
        # The value of a listed option is not an operand.
        assert _verdict('journalctl -u nginx -n 5')[0] is True

    def test_f4_sc2_grep_pattern_is_the_one_exception(self):
        assert _verdict('grep error /proc/1/status')[0] is True
        # With `-e` there is no positional pattern: every other word is a file.
        assert _verdict('grep -e error /etc/shadow')[0] is False
        assert _verdict('grep error /etc/shadow')[0] is False

    def test_f4_sc3_input_redirection_is_ineligible(self):
        for command in ('cat /proc/cpuinfo < /etc/shadow',
                        'head -n 5 /var/log/syslog < /etc/shadow',
                        'cat /proc/cpuinfo < /proc/loadavg'):
            ok, why = _verdict(command)
            assert ok is False and 'input-redirect' in why, (command, why)
        assert _verdict('cat <<< synthetic')[0] is False
        ok, why = _verdict('cat <<EOF\nhi\nEOF')
        assert ok is False and 'heredoc' in why

    def test_f4_sc3_f1_records_the_redirection_per_step(self):
        bd = breakdown('cat /proc/cpuinfo < /etc/shadow')
        assert 'input-redirect' in bd['flags']
        assert bd['steps'][0]['input_redirects'] == [
            {'op': '<', 'target': '/etc/shadow'}]

    def test_f4_sc4_subcommands_are_checked_here(self):
        """SC4: the policy decides, not F1 — so probe it with words F1 never
        classifies as a read."""
        from core import read_policy
        check = read_policy._check_status
        assert check('zpool', ['status']) == (True, '')
        assert check('zpool', ['list', '-H']) == (True, '')
        assert check('zpool', [])[1] == 'subcommand:-'
        assert check('zpool', ['destroy', 'tank'])[1] == 'subcommand:destroy'
        assert check('zpool', ['import'])[1] == 'subcommand:import'
        assert check('docker', ['rm', 'web01'])[1] == 'subcommand:rm'
        assert check('pct', ['start', '103'])[1] == 'subcommand:start'
        assert check('ip', ['addr', 'flush', 'dev', 'eth0'])[1] == 'action:flush'
        assert check('ip', ['link', 'set', 'eth0', 'down'])[1] == 'action:set'
        assert check('systemctl', ['list-unit-files'])[1] == 'subcommand:list-unit-files'
        # F1 already refuses these as write/unknown; the policy also refuses
        # the subcommand when F1 calls it a read.
        assert _verdict('systemctl list-unit-files')[1] == 'subcommand:list-unit-files'

    def test_f4_sc5_caps_apply_to_every_spelling(self):
        assert _verdict('head -n 1000 /var/log/syslog')[0] is True
        assert _verdict('head -n 1001 /var/log/syslog')[0] is False
        assert _verdict('head --lines=1001 /var/log/syslog')[0] is False
        assert _verdict('head -1001 /var/log/syslog')[0] is False
        assert _verdict('head -c 1048576 /var/log/syslog')[0] is True
        assert _verdict('head -c 1048577 /var/log/syslog')[0] is False
        assert _verdict('tail -c 1048577 /var/log/syslog')[0] is False
        assert _verdict('head -n +5 /var/log/syslog')[0] is False
        assert _verdict('head -n5x /var/log/syslog')[0] is False
        assert _verdict('tail -n -5 /var/log/syslog')[0] is False
        assert _verdict('journalctl -n 1000')[0] is True
        assert _verdict('journalctl -n 1001')[0] is False
        assert _verdict('grep -m 1000 -e x /var/log/syslog')[0] is True
        assert _verdict('grep -m 1001 -e x /var/log/syslog')[0] is False

    def test_f4_sc5_logs_need_a_capped_reader(self):
        assert _verdict('head -n 20 /var/log/syslog')[0] is True
        assert _verdict('tail /var/log/syslog')[0] is True
        assert _verdict('grep -m 5 -e x /var/log/syslog')[0] is True
        for command in ('cat /var/log/syslog', 'wc -l /var/log/syslog',
                        'grep -e x /var/log/syslog'):
            assert _verdict(command)[0] is False, command

    def test_f4_sc6_safe_trees_are_tighter(self):
        for command in ('cat /proc/1/exe', 'cat /proc/1/pagemap',
                        'cat /proc/kpageflags',
                        'cat /sys/kernel/debug/tracing/trace',
                        'cat /sys/kernel/tracing/trace',
                        'cat /sys/fs/bpf/synthetic',
                        'cat /var/log/installer/syslog',
                        'cat /var/log/cloud-init.log',
                        'cat /var/log/cloud-init-output.log'):
            ok, why = _verdict(command)
            assert ok is False and why.startswith('operand:'), (command, why)
        # The ordinary files next to them still read.
        assert _verdict('cat /proc/1/status')[0] is True
        assert _verdict('cat /sys/class/thermal/thermal_zone0/temp')[0] is True
        assert _verdict('head -n 5 /var/log/cloud-init.log')[0] is False

    def test_f4_sc7_any_needs_a_human_verdict_wins(self):
        bd = breakdown('uptime')
        assert eligible(bd, {'action': 'jit'}, 'uptime', 'on') == (True, '')
        assert eligible(bd, {'action': 'auto_approved',
                             'detail_type': 'exact_whitelist'},
                        'uptime', 'on') == (True, '')
        for verdict in ({'action': 'jit', 'detail_type': 'self_protection_jit'},
                        {'action': 'jit', 'detail_type': 'shell_meta'},
                        {'action': 'jit', 'detail_type': 'future_kind'},
                        {'action': 'future_thing'}):
            ok, why = eligible(bd, verdict, 'uptime', 'on')
            assert ok is False, verdict
            assert why.startswith('verdict:'), (verdict, why)

    def test_f4_sc8_every_option_in_every_eligible_case_is_allowlisted(self):
        """Generated: an eligible case may use only its verb's listed options."""
        import shlex
        from core import read_policy
        verbs = read_policy.READ_LANE_VERBS['file'] | \
            read_policy.READ_LANE_VERBS['status']
        checked = 0
        for command in ELIGIBLE:
            for step in breakdown(command)['steps']:
                tokens = shlex.split(step['text'])
                verb, rest = tokens[0], tokens[1:]
                assert verb in verbs, (command, verb)
                sub = ''
                if verb in read_policy.SUBCOMMANDS:
                    index = next((i for i, token in enumerate(rest)
                                  if not token.startswith('-')), None)
                    assert index is not None, command
                    sub = rest.pop(index)
                    assert sub in read_policy.SUBCOMMANDS[verb], (command, sub)
                    if verb in read_policy.ACTIONS:
                        action = next((i for i, token in enumerate(rest)
                                       if not token.startswith('-')), None)
                        if action is not None:
                            assert rest[action] in read_policy.ACTIONS[verb]
                            rest.pop(action)
                spec = read_policy.option_spec_for(verb, sub)
                assert spec is not None, (command, verb, sub)
                index = 0
                while index < len(rest):
                    token = rest[index]
                    if token in spec['words']:
                        checked += 1
                    elif token.startswith('--'):
                        name, sep, _value = token.partition('=')
                        assert name in spec['flags'] or name in spec['values'], \
                            (command, token)
                        checked += 1
                        if name in spec['values'] and not sep:
                            index += 1
                    elif token.startswith('-') and len(token) > 1:
                        if token in spec['flags'] or token in spec['values']:
                            checked += 1
                            if token in spec['values']:
                                index += 1
                        elif spec['digits'] and token[1:].isdigit():
                            checked += 1
                        else:
                            letters = token[1:]
                            for position, letter in enumerate(letters):
                                option = '-' + letter
                                if option in spec['values']:
                                    assert position == len(letters) - 1, (command, token)
                                    break
                                assert letter in spec['letters'], (command, token, option)
                            checked += 1
                    index += 1
        assert checked >= 50, checked


class TestRoundSixFixes:
    """Round 6 R1–R4 (reviewer round 5)."""

    def test_f4_r1_normalised_paths_cannot_slip_past_a_prefix_denial(self):
        prefixes = ('/sys/firmware', '/sys/kernel/debug', '/sys/kernel/tracing',
                    '/sys/fs/bpf', '/var/log/installer')
        verbs = ('cat {p}', 'head -n 5 {p}', 'tail -n 5 {p}',
                 'grep -m 5 -e x {p}', 'ls {p}', 'stat {p}')
        for prefix in prefixes:
            split = prefix.rfind('/')
            for spelling in (prefix[:split] + '//' + prefix[split + 1:],
                             prefix[:split] + '/./' + prefix[split + 1:]):
                for template in verbs:
                    command = template.format(p=spelling)
                    ok, why = _verdict(command)
                    assert ok is False, (command, why)

    def test_f4_r1_a_trailing_slash_still_reads(self):
        assert _verdict('head -n 5 /var/log/')[0] is True
        assert _verdict('ls /var/log/')[0] is True
        assert _verdict('cat /etc/hosts/')[0] is False

    def test_f4_r2_recursive_grep_is_not_in_the_lane(self):
        for operand in ('/var/log', '/var/log/', '//var/log', '/var/log/./'):
            for command in ('grep -r -m 5 -e x %s' % operand,
                            'grep --recursive -m 5 -e x %s' % operand):
                ok, why = _verdict(command)
                assert ok is False, (command, why)

    def test_f4_r3_grep_context_cannot_undo_the_cap(self):
        assert _verdict('grep -m 1000 -e x /var/log/syslog')[0] is True
        assert _verdict('grep -m 500 -A 1 -e x /var/log/syslog')[0] is True
        assert _verdict('grep -m 300 -C 1 -e x /var/log/syslog')[0] is True
        assert _verdict('grep -m 500 -A 2 -e x /var/log/syslog')[0] is False
        assert _verdict('grep -m 1000 -A 1000 -e x /var/log/syslog')[0] is False
        assert _verdict('grep -m 10 -C 100 -e x /var/log/syslog')[0] is False
        # Context outside /var/log is bounded by the option caps alone.
        assert _verdict('grep -m 5 -A 10 -e x /proc/1/status')[0] is True

    def test_f4_r4_head_and_tail_have_no_zero_terminated(self):
        for command in ('head -z /var/log/syslog', 'head --zero-terminated /var/log/syslog',
                        'head -qz /var/log/syslog', 'tail -z /var/log/syslog',
                        'tail --zero-terminated /var/log/syslog',
                        'tail -qz /var/log/syslog'):
            ok, why = _verdict(command)
            assert ok is False, (command, why)

    def test_f4_s2_zpool_letters_match_its_flags(self):
        assert _verdict('zpool status -g')[0] is True
        assert _verdict('zpool status -G')[0] is False

    def test_f4_s4_grep_perl_regexp_is_out(self):
        assert _verdict('grep -P -m 5 -e x /var/log/syslog')[0] is False
        assert _verdict('grep --perl-regexp -m 5 -e x /var/log/syslog')[0] is False

    def test_f4_sc7_chained_read_is_not_lane_eligible_end_to_end(self, gateway):
        """Round-5 behaviour: `shell_meta` is not a plain `jit`.

        The security round 2 answered Q1 ("allow shell_meta"), so the next
        round flips this test together with SC9; it is kept here because this
        round was scoped to the feature reviewer's R1-R4.
        """
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'shadow')
        _jit(client, headers, 'uptime && whoami')
        row = next(r for r in get_all_requests()
                   if r['command'] == 'uptime && whoami')
        assert row['status'] == 'pending'
        assert row['reason'] == ''
        set_gateway_read_lane('10.0.0.1', 'on')
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                         "VALUES ('read_lane_on_enabled', '1')")
            conn.commit()
        response = _jit(client, headers, 'uptime || whoami')
        assert 'read_lane' not in response


class TestReadPolicy:
    """§ 2.6a: F4 reuses Chunk H through one entry point."""

    def test_f4_read_policy_wraps_chunk_h_http(self):
        tool = {'read_only': True, 'generic': True}
        assert is_read_integration_call(tool, 'http', {'method': 'GET'}) == (True, '')
        ok, why = is_read_integration_call(tool, 'http', {'method': 'POST'})
        assert ok is False and why == 'not_a_read_method'

    def test_f4_a_curated_write_tool_is_not_a_read(self):
        """The `read_only` guard the merged F9/F14 wrapper depends on."""
        assert is_read_integration_call(
            {'read_only': False}, 'http', {}) == (False, 'write_tool')
        assert is_read_integration_call(
            {'read_only': True}, 'ws',
            {'endpoint': '/addons', 'method': 'get'}) == (True, '')

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

    def _unlock_on(self):
        """Round 4/5: `on` also needs the hand-set meta key."""
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                         "VALUES ('read_lane_on_enabled', '1')")
            conn.commit()

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
        self._unlock_on()
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
        self._unlock_on()
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
        self._unlock_on()
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
        self._unlock_on()
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
        self._unlock_on()
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
        # The on-lock is its own gate (Round 4); unlock it to test readiness.
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                         "VALUES ('read_lane_on_enabled', '1')")
            conn.commit()
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


class TestOnLock:
    """The `on` mode is hard-locked until an operator unlocks it by hand."""

    def _ready(self, ip='10.0.0.1'):
        from db.core import db_conn
        with db_conn() as conn:
            conn.execute(
                "UPDATE gateways SET read_lane = 'shadow', read_lane_since = ? "
                "WHERE ip = ?", (int(time.time()) - 8 * 86400, ip))
            conn.commit()

    def _unlock(self, on=True):
        from db.core import db_conn
        with db_conn() as conn:
            if on:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) "
                             "VALUES ('read_lane_on_enabled', '1')")
            else:
                conn.execute("DELETE FROM meta WHERE key = 'read_lane_on_enabled'")
            conn.commit()

    def test_f4_on_is_locked_by_default(self, gateway):
        client, _headers = gateway
        self._ready()
        response = client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'on'})
        assert response.status_code == 409
        assert response.json()['detail'] == 'read_lane_on_locked'
        assert get_gateway_read_lane('10.0.0.1') != 'on'

    def test_f4_on_locked_even_after_seven_days(self, gateway):
        client, _headers = gateway
        from db.gateways import read_lane_ready
        self._ready()
        assert read_lane_ready('10.0.0.1') is True
        assert client.put('/api/gateways/10.0.0.1/read-lane',
                          json={'mode': 'on'}).status_code == 409

    def test_f4_shadow_and_off_unaffected_by_the_lock(self, gateway):
        client, _headers = gateway
        for mode in ('shadow', 'off', 'shadow'):
            assert client.put('/api/gateways/10.0.0.1/read-lane',
                              json={'mode': mode}).status_code == 200
        assert get_gateway_read_lane('10.0.0.1') == 'shadow'

    def test_f4_on_allowed_when_unlocked_and_ready(self, gateway):
        client, _headers = gateway
        self._unlock()
        self._ready()
        response = client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'on'})
        assert response.status_code == 200
        assert response.json()['read_lane'] == 'on'
        assert get_gateway_read_lane('10.0.0.1') == 'on'

    def test_f4_on_still_needs_seven_days_when_unlocked(self, gateway):
        client, _headers = gateway
        self._unlock()
        client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'shadow'})
        response = client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'on'})
        assert response.status_code == 400
        assert get_gateway_read_lane('10.0.0.1') == 'shadow'

    def test_f4_stored_on_is_shadow_while_the_key_is_unset(self, gateway):
        """Round 5: the lock also covers an `on` row written behind the API."""
        client, headers = gateway
        set_gateway_read_lane('10.0.0.1', 'on')     # bypasses the endpoint
        assert get_gateway_read_lane('10.0.0.1') == 'on'
        assert 'read_lane' not in _jit(client, headers, 'uptime')
        row = next(r for r in get_all_requests() if r['command'] == 'uptime')
        assert row['status'] == 'pending'
        assert row['reason'] == 'read-lane-shadow'

    def test_f4_no_api_writes_the_lock_key(self, gateway):
        client, _headers = gateway
        from db.gateways import read_lane_on_enabled
        assert read_lane_on_enabled() is False
        client.put('/api/gateways/10.0.0.1/read-lane', json={'mode': 'on'})
        assert read_lane_on_enabled() is False
        row = next(g for g in client.get('/api/gateways').json()
                   if g['ip'] == '10.0.0.1')
        assert row['read_lane_on_enabled'] is False
        # Only a hand edit enables it; once set, the UI is told.
        self._unlock()
        row = next(g for g in client.get('/api/gateways').json()
                   if g['ip'] == '10.0.0.1')
        assert row['read_lane_on_enabled'] is True


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
