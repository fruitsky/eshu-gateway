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
