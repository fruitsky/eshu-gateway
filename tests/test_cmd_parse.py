"""Chunk F1 — command breakdown engine.

The fixture table in ``tests/fixtures/commands/*.txt`` is the review surface:
one line of metadata plus one synthetic command per case. Every fixture is
made up (documentation IPs only, ``web01``-style names, ``/tmp/synthetic-*``
paths) — see ``test_f1_fixtures_use_only_documentation_ips``.
"""

import ipaddress
import os
import random
import re
import string
import time

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fixtures', 'commands')

from core.cmd_parse import breakdown  # noqa: E402 — conftest puts dashboard/ on sys.path


def _load_cases():
    cases = []
    for filename in sorted(os.listdir(FIXTURES_DIR)):
        if not filename.endswith('.txt'):
            continue
        path = os.path.join(FIXTURES_DIR, filename)
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for block in re.split(r'^### ', text, flags=re.M)[1:]:
            header, _sep, body = block.partition('\n')
            parts = [part.strip() for part in header.split('|')]
            name = parts[0]
            meta = {}
            for part in parts[1:]:
                if '=' in part:
                    key, value = part.split('=', 1)
                    meta[key.strip()] = value.strip()
            cases.append({
                'file': filename,
                'name': name,
                'command': body.rstrip('\n'),
                'meta': meta,
            })
    return cases


CASES = _load_cases()
CASE_IDS = ['%s::%s' % (case['file'], case['name']) for case in CASES]


def _split_list(value):
    return [item for item in (value or '').split(',') if item]


class TestFixtureTable:

    def test_f1_fixture_table_is_large_enough(self):
        # The chunk asks for at least 60 synthetic cases covering every rule.
        assert len(CASES) >= 60, 'only %d cases' % len(CASES)

    @pytest.mark.parametrize('case', CASES, ids=CASE_IDS)
    def test_f1_fixture_kinds(self, case):
        from core.cmd_parse import breakdown
        result = breakdown(case['command'])
        assert [step['kind'] for step in result['steps']] == _split_list(case['meta'].get('kinds'))

    @pytest.mark.parametrize('case', CASES, ids=CASE_IDS)
    def test_f1_fixture_flags_and_sections(self, case):
        from core.cmd_parse import breakdown
        result = breakdown(case['command'])
        assert set(result['flags']) == set(_split_list(case['meta'].get('flags')))
        assert result['sections'] == _split_list(case['meta'].get('sections'))
        assert result['inner_targets'] == _split_list(case['meta'].get('inner'))
        expected_complete = case['meta'].get('complete', 'true') != 'false'
        assert result['complete'] is expected_complete

    @pytest.mark.parametrize('case', CASES, ids=CASE_IDS)
    def test_f1_summary_matches_steps(self, case):
        from core.cmd_parse import breakdown
        result = breakdown(case['command'])
        summary = result['summary']
        assert summary['steps'] == len(result['steps'])
        assert summary['read'] + summary['write'] + summary['unknown'] == summary['steps']
        for kind in ('read', 'write', 'unknown'):
            assert summary[kind] == sum(1 for step in result['steps'] if step['kind'] == kind)


class TestShape:

    def test_f1_result_shape(self):
        from core.cmd_parse import breakdown
        result = breakdown('systemctl restart nginx')
        assert set(result) == {'version', 'steps', 'sections', 'summary', 'headline',
                               'inner_targets', 'flags', 'complete'}
        assert result['version'] == 1
        assert isinstance(result['headline'], str)
        step = result['steps'][0]
        assert set(step) == {'text', 'op', 'kind', 'why', 'section', 'context', 'elevated'}

    def test_f1_headline_names_the_write(self):
        from core.cmd_parse import breakdown
        result = breakdown('echo "=== LOGS ==="; journalctl -u nginx; systemctl restart nginx')
        summary = result['summary']
        assert result['headline'].startswith(
            '%d steps · %d read · %d write' % (summary['steps'], summary['read'], summary['write']))
        assert result['headline'].endswith(': systemctl restart nginx')

    def test_f1_step_operators(self):
        from core.cmd_parse import breakdown
        result = breakdown('a=1 ls && df -h || uptime; free -h | wc -l')
        assert [step['op'] for step in result['steps']] == ['', '&&', '||', ';', '|']

    def test_f1_quotes_do_not_split(self):
        from core.cmd_parse import breakdown
        result = breakdown('echo "a;b|c" && grep -E "x|y" /etc/hosts')
        assert len(result['steps']) == 2

    def test_f1_section_marker_is_not_a_step(self):
        from core.cmd_parse import breakdown
        result = breakdown('echo "=== DISK ===" && df -h')
        assert len(result['steps']) == 1
        assert result['steps'][0]['section'] == 'DISK'
        assert result['sections'] == ['DISK']

    def test_f1_plain_echo_is_a_step(self):
        from core.cmd_parse import breakdown
        result = breakdown('echo "starting"; ls')
        assert len(result['steps']) == 2
        assert result['sections'] == []


class TestWrappers:

    def test_f1_pct_exec_context_and_inner_target(self):
        from core.cmd_parse import breakdown
        result = breakdown('pct exec 103 -- systemctl status nginx')
        step = result['steps'][0]
        assert step['context'] == ['pct 103']
        assert step['text'] == 'systemctl status nginx'
        assert step['kind'] == 'read'
        assert result['inner_targets'] == ['pct:103']

    def test_f1_chained_context_is_outermost_first(self):
        from core.cmd_parse import breakdown
        result = breakdown('sudo pct exec 103 -- docker exec -it web01 date')
        step = result['steps'][0]
        assert step['context'] == ['sudo', 'pct 103', 'docker web01']
        assert step['elevated'] is True
        assert result['inner_targets'] == ['pct:103', 'docker:web01']

    def test_f1_docker_exec_context(self):
        from core.cmd_parse import breakdown
        result = breakdown("docker exec -it web01 sh -c 'journalctl -u app | tail -n 5'")
        assert [step['context'] for step in result['steps']] == [
            ['docker web01', 'sh -c'], ['docker web01', 'sh -c']]
        assert result['inner_targets'] == ['docker:web01']

    def test_f1_su_is_elevated(self):
        from core.cmd_parse import breakdown
        result = breakdown("su - root -c 'cat /etc/hostname'")
        assert result['steps'][0]['elevated'] is True
        assert result['steps'][0]['context'] == ['su']

    def test_f1_depth_three_is_allowed(self):
        from core.cmd_parse import breakdown
        result = breakdown('sudo sudo sudo ls')
        assert [step['kind'] for step in result['steps']] == ['read']
        assert result['complete'] is True

    def test_f1_depth_four_gives_up(self):
        from core.cmd_parse import breakdown
        result = breakdown('sudo sudo sudo sudo ls')
        assert [step['kind'] for step in result['steps']] == ['unknown']
        assert result['complete'] is False


class TestEncoded:

    def test_f1_encoded_decoded_steps_appear(self):
        import base64
        from core.cmd_parse import breakdown
        payload = base64.b64encode(b"echo '=== DISK ==='\ndf -h /").decode()
        result = breakdown('echo %s | base64 -d | bash' % payload)
        assert result['flags'] == ['encoded']
        assert result['sections'] == ['DISK']
        assert [step['kind'] for step in result['steps']] == ['read']
        assert result['steps'][0]['text'] == 'df -h /'
        assert 'decoded' in result['steps'][0]['context']

    def test_f1_encoded_inside_su_keeps_elevation(self):
        import base64
        from core.cmd_parse import breakdown
        payload = base64.b64encode(b"echo '=== HOST ==='\nuptime").decode()
        result = breakdown('su - root -c "echo %s | base64 -d | bash"' % payload)
        step = result['steps'][0]
        assert step['text'] == 'uptime'
        assert result['sections'] == ['HOST']
        assert step['elevated'] is True
        assert step['context'] == ['su', 'decoded']

    def test_f1_undecodable_payload_is_one_unknown_step(self):
        from core.cmd_parse import breakdown
        result = breakdown("echo 'no blob here' | base64 -d | bash")
        assert len(result['steps']) == 1
        assert result['steps'][0]['kind'] == 'unknown'
        assert result['complete'] is False


class TestFailures:

    def test_f1_unterminated_quote_is_total(self):
        from core.cmd_parse import breakdown
        result = breakdown('echo "unterminated')
        assert len(result['steps']) == 1
        assert result['steps'][0]['kind'] == 'unknown'
        assert result['complete'] is False

    def test_f1_random_strings_never_raise(self):
        from core.cmd_parse import breakdown
        random.seed(20261107)
        alphabet = string.printable
        for _ in range(500):
            command = ''.join(random.choice(alphabet)
                              for _ in range(random.randint(0, 120)))
            result = breakdown(command)
            assert isinstance(result, dict)
            summary = result['summary']
            assert summary['steps'] == len(result['steps'])
            assert summary['read'] + summary['write'] + summary['unknown'] == summary['steps']
            assert all(step['kind'] in ('read', 'write', 'unknown')
                       for step in result['steps'])

    def test_f1_empty_command(self):
        from core.cmd_parse import breakdown
        result = breakdown('')
        assert result['steps'] == []
        assert result['summary'] == {'steps': 0, 'read': 0, 'write': 0, 'unknown': 0}


class TestRules:

    def test_f1_risk_flagged_step_is_never_read(self):
        from core.cmd_parse import breakdown
        from core.cmd_risk import get_cmd_risk
        commands = [case['command'] for case in CASES]
        commands += [
            'rm -rf /tmp/synthetic-dir',
            'systemctl restart nginx',
            'journalctl --vacuum-time=7d',
            'find / -name "*.log" -delete',
            'cat /tmp/rm',
            'ls /var/lib/rm',
        ]
        for command in commands:
            for step in breakdown(command)['steps']:
                if step['kind'] == 'read':
                    assert get_cmd_risk(step['text']) is None, (command, step)

    def test_f1_network_fetch_hint(self):
        from core.cmd_parse import breakdown
        assert breakdown('curl -s http://192.0.2.10/status')['steps'][0]['why'] == 'network fetch (GET)'
        assert breakdown('curl -o /tmp/x http://192.0.2.10/f')['steps'][0]['kind'] == 'unknown'

    def test_f1_redirect_why(self):
        from core.cmd_parse import breakdown
        step = breakdown('echo hi > /tmp/synthetic-out')['steps'][0]
        assert step['kind'] == 'write'
        assert step['why'] == 'redirect to /tmp/synthetic-out'
        assert '> /tmp/synthetic-out' in step['text']

    def test_f1_stdout_dup_is_not_a_write(self):
        from core.cmd_parse import breakdown
        result = breakdown('journalctl -u nginx 2>&1 | tail -n 20')
        assert [step['kind'] for step in result['steps']] == ['read', 'read']
        assert result['flags'] == []

    def test_f1_sensitive_path_flag(self):
        from core.cmd_parse import breakdown
        assert 'sensitive-path' in breakdown('cat /srv/app/.env')['flags']
        assert 'sensitive-path' in breakdown('cat /root/.ssh/id_rsa')['flags']
        assert 'sensitive-path' not in breakdown('cat /etc/hostname')['flags']

    def test_f1_journalctl_vacuum_is_a_write(self):
        from core.cmd_parse import breakdown
        step = breakdown('journalctl --vacuum-size=1G')['steps'][0]
        assert step['kind'] == 'write'
        assert 'vacuum' in step['why']

    def test_f1_sed_inplace_is_a_write(self):
        from core.cmd_parse import breakdown
        assert breakdown("sed -i 's/a/b/' /etc/nginx/nginx.conf")['steps'][0]['kind'] == 'write'
        assert breakdown("sed 's/a/b/' /etc/nginx/nginx.conf")['steps'][0]['kind'] == 'read'


class TestCache:

    def test_f1_cached_result_is_a_copy(self):
        from core.cmd_parse import breakdown
        first = breakdown('ls -la')
        first['steps'][0]['kind'] = 'write'
        first['flags'].append('tampered')
        second = breakdown('ls -la')
        assert second['steps'][0]['kind'] == 'read'
        assert 'tampered' not in second['flags']

    def test_f1_performance_1000_breakdowns_of_2kb(self):
        from core.cmd_parse import breakdown
        command = '\n'.join(
            'journalctl -u service%d --since today --no-pager | grep -i error | tail -n 20' % i
            for i in range(28))
        assert len(command) >= 2048
        started = time.time()
        for _ in range(1000):
            breakdown(command)
        elapsed = time.time() - started
        assert elapsed < 2.0, '1000 breakdowns took %.2fs' % elapsed


class TestFixturesAreSynthetic:

    def test_f1_fixtures_use_only_documentation_ips(self):
        allowed = [ipaddress.ip_network('192.0.2.0/24'),
                   ipaddress.ip_network('198.51.100.0/24'),
                   ipaddress.ip_network('203.0.113.0/24')]
        for case in CASES:
            for match in re.findall(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', case['command']):
                address = ipaddress.ip_address(match)
                assert any(address in network for network in allowed), (
                    '%s uses a non-documentation IP: %s' % (case['name'], match))

    def test_f1_fixtures_contain_no_eshu_database_path(self):
        for case in CASES:
            assert 'eshu.db' not in case['command']


class TestReport:

    SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'scripts', 'breakdown_report.py')

    def _make_db(self, tmp_path, commands):
        import sqlite3
        path = tmp_path / 'copy.db'
        conn = sqlite3.connect(str(path))
        conn.execute('CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                     'target_ip TEXT, command TEXT, status TEXT, created_at INTEGER, '
                     'expires_at INTEGER)')
        for index, command in enumerate(commands):
            conn.execute('INSERT INTO requests (target_ip, command, status, created_at, '
                         'expires_at) VALUES (?, ?, ?, ?, ?)',
                         ('192.0.2.10', command, 'pending',
                          1700000000 + index, 1700000900 + index))
        conn.commit()
        conn.close()
        return path

    def _run(self, *args):
        import subprocess
        import sys
        return subprocess.run([sys.executable, self.SCRIPT] + list(args),
                              capture_output=True, text=True, timeout=60)

    def test_f1_report_prints_aggregates_only(self, tmp_path):
        import hashlib
        path = self._make_db(tmp_path, [
            'journalctl -u nginx --no-pager',
            'ls -la && systemctl restart nginx',
            'frobnicate --now',
            'cat /srv/app/.env',
        ])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = self._run('--db', str(path))
        assert result.returncode == 0, result.stderr
        assert 'commands: 4 analysed' in result.stdout
        assert 'read' in result.stdout and 'unknown' in result.stdout
        assert 'frobnicate' in result.stdout
        # Privacy: command text is not printed without --show-unknown.
        assert '/srv/app/.env' not in result.stdout
        assert 'systemctl restart nginx' not in result.stdout
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    def test_f1_report_show_unknown_is_opt_in(self, tmp_path):
        path = self._make_db(tmp_path, ['frobnicate --now'])
        result = self._run('--db', str(path), '--show-unknown', '1')
        assert result.returncode == 0, result.stderr
        assert 'frobnicate --now' in result.stdout

    def test_f1_report_since_filters(self, tmp_path):
        path = self._make_db(tmp_path, ['ls -la', 'frobnicate --now'])
        import sqlite3
        conn = sqlite3.connect(str(path))
        conn.execute('UPDATE requests SET created_at = 1600000000 WHERE command = ?',
                     ('ls -la',))
        conn.commit()
        conn.close()
        result = self._run('--db', str(path), '--since', '2023-11-14')
        assert result.returncode == 0, result.stderr
        assert 'commands: 1 analysed' in result.stdout

    def test_f1_report_missing_db_exits_2(self, tmp_path):
        result = self._run('--db', str(tmp_path / 'nope.db'))
        assert result.returncode == 2
        assert 'no such database' in result.stderr


class TestApi:

    def _pending(self, client, command='journalctl -u nginx --no-pager', ip='10.0.0.1'):
        import base64
        response = client.post('/api/request', json={
            'target_ip': ip,
            'encoded_command': base64.b64encode(command.encode()).decode(),
        }, headers={'X-Gateway-Token': 'testtoken-10-0-0-1'})
        assert response.status_code == 200
        return response.json()['id']

    @pytest.fixture
    def gateway(self, auth_client):
        from db.gateways import register_gateway, set_gateway_token
        register_gateway('10.0.0.1', 'web01', 'v15.3')
        set_gateway_token('10.0.0.1', 'testtoken-10-0-0-1')
        return auth_client

    def test_f1_pending_row_has_breakdown(self, gateway):
        request_id = self._pending(gateway)
        rows = gateway.get('/api/requests').json()
        row = next(item for item in rows if item['id'] == int(request_id))
        assert row['breakdown']['summary']['steps'] == 1
        assert row['breakdown']['steps'][0]['kind'] == 'read'
        assert row['breakdown']['headline'].startswith('1 step · 1 read')

    def test_f1_history_row_has_no_breakdown(self, gateway):
        request_id = self._pending(gateway)
        gateway.post('/api/approve/%d' % int(request_id))
        rows = gateway.get('/api/requests').json()
        row = next(item for item in rows if item['id'] == int(request_id))
        assert 'breakdown' not in row

    def test_f1_breakdown_endpoint_returns_it(self, gateway):
        request_id = self._pending(gateway)
        response = gateway.get('/api/requests/%d/breakdown' % int(request_id))
        assert response.status_code == 200
        body = response.json()
        assert body['steps'][0]['kind'] == 'read'
        assert body['complete'] is True

    def test_f1_breakdown_endpoint_needs_a_session(self, client):
        from db.gateways import register_gateway, set_gateway_token
        register_gateway('10.0.0.1', 'web01', 'v15.3')
        set_gateway_token('10.0.0.1', 'testtoken-10-0-0-1')
        request_id = self._pending(client)
        response = client.get('/api/requests/%d/breakdown' % int(request_id))
        assert response.status_code in (401, 403)

    def test_f1_breakdown_endpoint_404(self, gateway):
        assert gateway.get('/api/requests/999999/breakdown').status_code == 404


class TestReviewRound1:
    """Regressions for the F1 review round 1 (R1–R11).

    Each test is named after its reviewer item and failed on the branch as
    reviewed; see the Round 2 paragraph in the chunk's Implementer notes.
    """

    # ── R1: `#` only starts a comment at a word start ───────────────────────

    def test_f1_r1_hash_inside_word_is_not_a_comment(self):
        for command in ('ls /tmp/#dir; rm -f /tmp/synthetic-x',
                        'echo a#b; rm -f /tmp/synthetic-x'):
            result = breakdown(command)
            assert [step['kind'] for step in result['steps']] == ['read', 'write'], command
        # A real comment is still removed.
        assert [step['kind'] for step in breakdown('ls # rm -f /tmp/synthetic-x')['steps']] == ['read']

    # ── R2: substitutions in heredoc bodies, here-strings, redirect targets ─

    def test_f1_r2_heredoc_body_substitution(self):
        result = breakdown('cat <<EOF\n$(rm -rf /tmp/synthetic-x)\nEOF')
        assert [step['kind'] for step in result['steps']] == ['write', 'write']

    def test_f1_r2_quoted_heredoc_body_is_data(self):
        result = breakdown("cat <<'EOF'\n$(rm -rf /tmp/synthetic-x)\nEOF")
        assert [step['kind'] for step in result['steps']] == ['read']

    def test_f1_r2_herestring_and_redirect_target_substitution(self):
        for command in ('cat <<< "$(rm -rf /tmp/synthetic-x)"',
                        'cat < "$(rm -rf /tmp/synthetic-x)"',
                        'cat > "$(rm -rf /tmp/synthetic-x)"'):
            result = breakdown(command)
            assert 'write' in [step['kind'] for step in result['steps']], command

    # ── R3: process substitution ────────────────────────────────────────────

    def test_f1_r3_process_substitution(self):
        result = breakdown('cat <(rm -rf /tmp/synthetic-x)')
        assert result['complete'] is True
        assert 'write' in [step['kind'] for step in result['steps']]
        contexts = [ctx for step in result['steps'] for ctx in step['context']]
        assert 'procsubst' in contexts
        result = breakdown('diff <(ls /tmp/a) <(ls /tmp/b)')
        assert result['complete'] is True
        assert [step['kind'] for step in result['steps']] == ['read', 'read', 'read']
        # `>( … )` is a sink: never a plain read.
        assert breakdown('ls >(cat)')['steps'][0]['kind'] != 'read'

    # ── R4: sed flags and scripts ───────────────────────────────────────────

    def test_f1_r4_sed_combined_flags_and_scripts(self):
        for command in ("sed -Ei 's/a/b/' /tmp/f.txt", "sed -ni 'p' /tmp/f.txt",
                        "sed -ri 's/a/b/' /tmp/f.txt", "sed -si 's/a/b/' /tmp/f.txt"):
            assert breakdown(command)['steps'][0]['kind'] == 'write', command
        assert breakdown("sed 's/a/b/ge' /tmp/f.txt")['steps'][0]['kind'] == 'unknown'
        assert breakdown("sed 's#a#b#w /tmp/synthetic-out' /tmp/f.txt")['steps'][0]['kind'] == 'write'
        assert breakdown("sed -n 'w /tmp/synthetic-out' /tmp/f.txt")['steps'][0]['kind'] == 'write'
        assert breakdown("sed -e 's/a/b/' -e 'w /tmp/synthetic-out' /tmp/f.txt")['steps'][0]['kind'] == 'write'
        assert breakdown('sed -f /tmp/script.sed /tmp/f.txt')['steps'][0]['kind'] == 'unknown'
        assert breakdown("sed 's/a/b/' /tmp/f.txt")['steps'][0]['kind'] == 'read'

    # ── R5: dangerous environment / shell-state changes ─────────────────────

    def test_f1_r5_dangerous_assignments(self):
        for command in ('LD_PRELOAD=/tmp/x.so cat /tmp/f',
                        'env LD_PRELOAD=/tmp/x.so ls',
                        'PATH=/tmp/bin:$PATH ls',
                        'export PATH=/tmp/bin:$PATH',
                        'declare PATH=/tmp/bin',
                        'local PATH',
                        'printf -v PATH /tmp/bin'):
            result = breakdown(command)
            assert result['steps'][0]['kind'] == 'unknown', command
            assert result['steps'][0]['why'] == 'changes how later commands run', command
        # Safe assignments stay read.
        assert breakdown('FOO=1 ls')['steps'][0]['kind'] == 'read'
        assert breakdown('export LC_ALL=C')['steps'][0]['kind'] == 'read'

    def test_f1_r5_state_builtins_are_unknown(self):
        # `set` moved to read in review round 2 (S7): shell options cannot
        # change how later commands resolve.
        for command in ('unset FOO', 'alias ll=ls', 'hash -r',
                        'enable -n echo', "trap 'rm -rf /tmp/x' EXIT",
                        'source /tmp/script.sh', '. /tmp/script.sh'):
            assert breakdown(command)['steps'][0]['kind'] == 'unknown', command
        assert breakdown('read -r line')['steps'][0]['kind'] == 'read'

    # ── R6: path-qualified verbs ────────────────────────────────────────────

    def test_f1_r6_path_qualified_verbs(self):
        for command in ('/tmp/synthetic-bin/cat /tmp/f', './cat /tmp/f', 'bin/cat /tmp/f'):
            assert breakdown(command)['steps'][0]['kind'] == 'unknown', command
        assert breakdown('/usr/bin/cat /tmp/f')['steps'][0]['kind'] == 'read'
        assert breakdown('/bin/systemctl status nginx')['steps'][0]['kind'] == 'read'
        result = breakdown('/tmp/synthetic-bin/sudo ls')
        assert result['steps'][0]['kind'] == 'unknown'
        assert result['steps'][0]['context'] == []

    # ── R7: verb-specific gaps ──────────────────────────────────────────────

    def test_f1_r7_awk_exec_and_pipes(self):
        assert breakdown("awk '{ system (\"id\") }' /tmp/f")['steps'][0]['kind'] == 'unknown'
        assert breakdown("awk '{ print | \"sh\" }' /tmp/f")['steps'][0]['kind'] == 'unknown'
        assert breakdown('awk -f /tmp/prog.awk /tmp/f')['steps'][0]['kind'] == 'unknown'
        assert breakdown("awk '@include \"/tmp/x\"' /tmp/f")['steps'][0]['kind'] == 'unknown'
        assert breakdown("awk 'BEGIN { print 1 }' /tmp/f")['steps'][0]['kind'] == 'read'
        assert breakdown("awk -i inplace '{print}' /tmp/f")['steps'][0]['kind'] == 'write'

    def test_f1_r7_git_output_and_external_diff(self):
        assert breakdown('git log --output=/tmp/synthetic-out')['steps'][0]['kind'] == 'write'
        assert breakdown('git diff --ext-diff')['steps'][0]['kind'] == 'unknown'
        assert breakdown('git show --textconv HEAD')['steps'][0]['kind'] == 'unknown'
        assert breakdown('git log --oneline -5')['steps'][0]['kind'] == 'read'

    def test_f1_r7_smartctl_actions_win(self):
        assert breakdown('smartctl -a -t long /dev/sda')['steps'][0]['kind'] == 'write'
        assert breakdown('smartctl -a /dev/sda')['steps'][0]['kind'] == 'read'

    def test_f1_r7_ip_netns_exec_is_a_wrapper(self):
        result = breakdown('ip netns exec webns uptime')
        assert result['steps'][0]['kind'] == 'read'
        assert result['steps'][0]['context'] == ['netns webns']

    def test_f1_r7_misc_verb_paths(self):
        assert breakdown("find /tmp -name '*.log' -fprint0 /tmp/synthetic-list")['steps'][0]['kind'] == 'write'
        assert breakdown('sort --compress-program=/tmp/x /tmp/f')['steps'][0]['kind'] == 'unknown'
        assert breakdown('tree -o /tmp/synthetic-out /tmp')['steps'][0]['kind'] == 'write'
        assert breakdown('dmidecode --dump-bin /tmp/synthetic-dmi.bin')['steps'][0]['kind'] == 'write'
        assert breakdown('systemd-analyze set-log-level debug')['steps'][0]['kind'] == 'write'
        assert breakdown('nvidia-smi -q')['steps'][0]['kind'] == 'read'
        assert breakdown('nvidia-smi -r')['steps'][0]['kind'] == 'unknown'
        assert breakdown('vcgencmd measure_temp')['steps'][0]['kind'] == 'read'
        assert breakdown('vcgencmd set_config int=1')['steps'][0]['kind'] == 'unknown'

    def test_f1_r7_follow_flag(self):
        for command in ('tail -f /var/log/syslog', 'journalctl -f',
                        'docker logs -f web01', 'dmesg -w', 'docker events'):
            result = breakdown(command)
            assert result['steps'][0]['kind'] == 'read', command
            assert 'follows' in result['flags'], command
        assert 'follows' not in breakdown('tail -n 20 /var/log/syslog')['flags']

    # ── R8: sensitive reads ─────────────────────────────────────────────────

    def test_f1_r8_sensitive_reads(self):
        assert 'sensitive-path' in breakdown('getent shadow')['flags']
        assert 'sensitive-path' in breakdown('cat /proc/1/environ')['flags']
        assert 'sensitive-path' in breakdown('docker inspect web01')['flags']
        assert 'sensitive-path' in breakdown('docker compose config')['flags']
        assert 'sensitive-path' in breakdown('env')['flags']
        assert 'sensitive-path' in breakdown('printenv')['flags']
        assert 'sensitive-path' not in breakdown('env FOO=1 uptime')['flags']

    # ── R9: decoded interpreter payloads ────────────────────────────────────

    def test_f1_r9_decoded_interpreter_payload_is_one_step(self):
        import base64
        payload = base64.b64encode(b"import os\nprint(os.getcwd())\n").decode()
        result = breakdown('echo %s | base64 -d | python3' % payload)
        assert result['summary']['steps'] == 1
        step = result['steps'][0]
        assert step['kind'] == 'unknown'
        assert 'python3' in step['why']
        assert 'import os' in step['script']

    def test_f1_r9_python_heredoc_is_one_step(self):
        result = breakdown("python3 - <<'PY'\nimport os\nprint(os.getcwd())\nPY")
        assert result['summary']['steps'] == 1
        step = result['steps'][0]
        assert step['kind'] == 'unknown'
        assert 'import os' in step['script']

    # ── R10: accuracy ───────────────────────────────────────────────────────

    def test_f1_r10_control_tar_install(self):
        for command in (':', 'exit 1', 'return 0', 'break', 'continue', 'wait'):
            assert breakdown(command)['steps'][0]['kind'] == 'read', command
        assert breakdown('tar -tzf /tmp/a.tar.gz')['steps'][0]['kind'] == 'read'
        assert breakdown('tar -czf /tmp/a.tar.gz /tmp/dir')['steps'][0]['kind'] == 'write'
        assert breakdown('tar xf /tmp/a.tar')['steps'][0]['kind'] == 'write'
        assert breakdown('install -m 755 /tmp/a /usr/local/bin/a')['steps'][0]['kind'] == 'write'

    def test_f1_r10_runuser_is_a_wrapper(self):
        result = breakdown('runuser -u root -- cat /etc/hostname')
        assert result['steps'][0]['kind'] == 'read'
        assert result['steps'][0]['context'] == ['runuser']
        assert result['steps'][0]['elevated'] is True
        result = breakdown("runuser -l root -c 'cat /etc/hostname'")
        assert result['steps'][0]['kind'] == 'read'
        assert result['steps'][0]['elevated'] is True

    def test_f1_r10_shell_functions(self):
        result = breakdown('f() { rm -rf /tmp/synthetic-x; }\nf')
        assert [step['kind'] for step in result['steps']] == ['write', 'write']
        assert result['steps'][-1]['why'] == 'calls function f'
        result = breakdown('function g { ls /tmp; }\ng')
        assert [step['kind'] for step in result['steps']] == ['read', 'read']
        assert result['steps'][-1]['why'] == 'calls function g'
        assert breakdown('undefined_thing /tmp/x')['steps'][0]['kind'] == 'unknown'

    # ── R11: report scope ───────────────────────────────────────────────────

    def test_f1_r11_report_excludes_integration_rows(self, tmp_path):
        import sqlite3
        import subprocess
        import sys
        path = tmp_path / 'copy.db'
        conn = sqlite3.connect(str(path))
        conn.execute('CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                     'target_ip TEXT, command TEXT, status TEXT, created_at INTEGER, '
                     'expires_at INTEGER)')
        rows = [
            ('192.0.2.10', 'uptime', 'pending'),
            ('192.0.2.10', 'frobnicate', 'pending'),
            ('192.0.2.10', 'some_tool', 'integration-approved'),
            ('192.0.2.10', 'other_tool', 'integration-denied'),
        ]
        for index, (ip, command, status) in enumerate(rows):
            conn.execute('INSERT INTO requests (target_ip, command, status, created_at, '
                         'expires_at) VALUES (?, ?, ?, ?, ?)',
                         (ip, command, status, 1700000000 + index, 1700000900 + index))
        conn.commit()
        conn.close()
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              'scripts', 'breakdown_report.py')
        default = subprocess.run([sys.executable, script, '--db', str(path)],
                                 capture_output=True, text=True, timeout=60)
        assert default.returncode == 0, default.stderr
        assert 'commands: 2 analysed' in default.stdout
        assert 'excluded' in default.stdout
        included = subprocess.run([sys.executable, script, '--db', str(path),
                                   '--include-integration'],
                                  capture_output=True, text=True, timeout=60)
        assert included.returncode == 0, included.stderr
        assert 'commands: 4 analysed' in included.stdout


class TestReviewRound2:
    """Regressions for the F1 review round 2 (S1-S7).

    Each failed on the branch as reviewed; see the Round 3 paragraph in the
    chunk's Implementer notes.
    """

    # ── S1: a command after a heredoc operator on the same line ─────────────

    def test_f1_s1_command_after_heredoc_operator_same_line(self):
        result = breakdown('cat <<EOF; rm -f /tmp/synthetic-x\nbody line\nEOF')
        assert [step['kind'] for step in result['steps']] == ['read', 'write']
        assert result['complete'] is True
        assert result['steps'][0]['text'].startswith('cat')
        # The heredoc belongs to `cat`, not to the command after the `;`.
        assert result['steps'][1]['text'] == 'rm -f /tmp/synthetic-x'

    # ── S2: every heredoc on a line, not just the first ─────────────────────

    def test_f1_s2_every_heredoc_body_is_scanned(self):
        command = ('cat <<A <<B\nfirst body\nA\n'
                   '$(rm -rf /tmp/synthetic-x)\nB')
        result = breakdown(command)
        kinds = [step['kind'] for step in result['steps']]
        assert 'write' in kinds, kinds
        assert result['complete'] is True
        contexts = [ctx for step in result['steps'] for ctx in step['context']]
        assert 'heredoc' in contexts

    # ── S3: `>& FILE` and `&>> FILE` ───────────────────────────────────────

    def test_f1_s3_ampersand_redirects_write_files(self):
        result = breakdown('ls >& /tmp/synthetic-out')
        assert result['steps'][0]['kind'] == 'write'
        assert 'redirect-to-file' in result['flags']
        result = breakdown('ls &>> /tmp/synthetic-out')
        assert result['steps'][0]['kind'] == 'write'
        assert 'redirect-to-file' in result['flags']
        assert result['complete'] is True
        # fd duplication is still not a file write
        result = breakdown('ls >&2')
        assert result['steps'][0]['kind'] == 'read'
        assert 'redirect-to-file' not in result['flags']

    # ── S4: tar options that run a program ─────────────────────────────────

    def test_f1_s4_tar_options_that_execute(self):
        commands = [
            'tar -cf /tmp/x.tar --checkpoint-action=exec=sh /tmp/dir',
            'tar -I /tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar --use-compress-program=/tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar -F /tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar --info-script=/tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar --new-volume-script=/tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar --rsh-command=/tmp/prog -cf /tmp/x.tar /tmp/dir',
            'tar --rmt-command=/tmp/prog -cf /tmp/x.tar /tmp/dir',
        ]
        for command in commands:
            assert breakdown(command)['steps'][0]['kind'] == 'unknown', command
        assert breakdown('tar -cf /tmp/x.tar /tmp/dir')['steps'][0]['kind'] == 'write'

    # ── S5: awk output and command pipes ───────────────────────────────────

    def test_f1_s5_awk_output_and_command_pipes(self):
        out = breakdown('awk \'{printf "x" >> "/tmp/synthetic-out"}\' /tmp/f')
        assert out['steps'][0]['kind'] == 'write'
        out = breakdown("awk '{c|getline}' /tmp/f")
        assert out['steps'][0]['kind'] == 'unknown'
        out = breakdown('awk \'BEGIN{print > "/tmp/synthetic-out"}\' /tmp/f')
        assert out['steps'][0]['kind'] == 'write'
        out = breakdown("awk '{print $1}' /tmp/f")
        assert out['steps'][0]['kind'] == 'read'

    # ── S6: git reads can run repository config ────────────────────────────

    def test_f1_s6_git_reads_carry_repo_config_flag(self):
        for command in ('git status', 'git -C /tmp/synthetic-repo log --oneline -5',
                        'git diff', 'git show HEAD'):
            result = breakdown(command)
            assert result['steps'][0]['kind'] == 'read', command
            assert 'repo-config' in result['flags'], command
        assert 'repo-config' not in breakdown('git branch')['flags']
        assert 'repo-config' not in breakdown('ls')['flags']

    # ── S7: `set` is shell options, not a state change ─────────────────────

    def test_f1_s7_set_is_shell_options(self):
        for command in ('set -e', 'set -euo pipefail', 'set +x', 'set -- a b', 'set'):
            result = breakdown(command)
            assert result['steps'][0]['kind'] == 'read', command
            assert result['steps'][0]['why'] == 'shell options', command
        # R5 still applies to unset of a dangerous name.
        assert breakdown('unset PATH')['steps'][0]['kind'] == 'unknown'
