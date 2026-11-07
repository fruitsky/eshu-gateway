"""Chunk F2 — the reviewable approval card.

The renderer lives in `dashboard/static/app.js`; these tests load the real file
in a stubbed browser context (`tests/fixtures/f2_harness.js`) and render a
fixture pending row, so the assertions cover the shipped code rather than a
copy. The XSS test is the important one: section names, step text, context
chips and decoded scripts are all agent-controlled.
"""

import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f2_harness.js')
APP_JS = os.path.join(HERE, '..', 'dashboard', 'static', 'app.js')
INDEX_HTML = os.path.join(HERE, '..', 'dashboard', 'static', 'index.html')
NODE = shutil.which('node')

pytestmark = pytest.mark.skipif(NODE is None, reason='node is not installed')


def render(row):
    proc = subprocess.run([NODE, HARNESS, json.dumps({'row': row})],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert 'error' not in out, out['error']
    return out


def step(text, kind='read', section='', context=None, why='', script=None):
    item = {'text': text, 'op': '', 'kind': kind, 'why': why,
            'section': section, 'context': context or [], 'elevated': False}
    if script is not None:
        item['script'] = script
    return item


def row(steps, headline=None, flags=None, complete=True, command=None):
    counts = {'steps': len(steps), 'read': 0, 'write': 0, 'unknown': 0}
    for item in steps:
        counts[item['kind']] += 1
    if headline is None:
        headline = '%d steps \u00b7 %d read \u00b7 %d write' % (
            counts['steps'], counts['read'], counts['write'])
    return {
        'id': 7,
        'status': 'pending',
        'command': command if command is not None else 'ls -la && df -h',
        'breakdown': {
            'version': 1,
            'steps': steps,
            'sections': [],
            'summary': counts,
            'headline': headline,
            'inner_targets': [],
            'flags': flags or [],
            'complete': complete,
        },
    }


NINE_READ_STEPS = [
    step('uptime', section='UPTIME'),
    step('df -h /', section='DISK'),
    step('free -h', section='MEM'),
    step('journalctl -u nginx --no-pager | tail -n 20', section='LOGS',
         context=['pct 103'], why='journalctl (no vacuum/rotate)'),
    step('docker ps', section='CONTAINERS', context=['docker web01']),
    step('ss -tulpn', section='NET'),
    step('ip -br addr', section='NET'),
    step('systemctl --failed', section='SERVICES'),
    step('lsblk', section='DISK'),
]


class TestHeadlineAndSteps:

    def test_f2_all_read_headline_and_step_list_render(self):
        out = render(row(NINE_READ_STEPS))
        assert '9 steps' in out['headline']
        assert '9 read' in out['headline']
        assert 'bd-read' in out['headline']
        assert 'read it as written' not in out['headline']
        body = out['body']
        for section in ('UPTIME', 'DISK', 'MEM', 'LOGS', 'CONTAINERS', 'NET', 'SERVICES'):
            assert section in body
        assert 'journalctl -u nginx --no-pager | tail -n 20' in body
        # context chains render as chips
        assert 'pct 103' in body and 'docker web01' in body
        assert body.count('bd-step') >= 9

    def test_f2_mixed_command_shows_the_write_step(self):
        steps = NINE_READ_STEPS + [
            step("sed -i 's/a/b/' /etc/nginx/nginx.conf", kind='write',
                 section='FIX', why='sed -i')]
        out = render(row(steps))
        assert '1 write' in out['headline']
        assert 'bd-write' in out['body']
        assert 'sed -i &#39;s/a/b/&#39; /etc/nginx/nginx.conf' in out['body']
        assert 'bd-write' in out['headline'] or 'bd-unknown' in out['headline']

    def test_f2_unknown_step_warns_and_incomplete_parsing_is_flagged(self):
        out = render(row([step('frobnicate --now', kind='unknown')]))
        assert 'read it as written' in out['headline']
        assert 'bd-unknown' in out['body']
        out = render(row([step('cat <<EOF\nbody')], complete=False))
        assert 'could not fully parse' in out['headline']

    def test_f2_flags_render_as_chips(self):
        flags = ['encoded', 'heredoc', 'nested-ssh', 'sensitive-path',
                 'redirect-to-file', 'follows', 'repo-config']
        out = render(row([step('x', kind='read')], flags=flags))
        body = out['body'] + out['flags']
        for label in ('encoded', 'heredoc', 'nested ssh', 'sensitive path',
                      'redirect', 'runs until stopped', 'repo can run programs'):
            assert label in body, label

    def test_f2_script_field_renders_in_a_pre_block(self):
        out = render(row([step('python3 -', kind='unknown',
                               script='import os\nprint(os.getcwd())',
                               why='python3 script from stdin (2 lines)')]))
        assert '<pre' in out['body']
        assert 'import os' in out['body']
        assert 'script' in out['body']


class TestEscaping:

    PAYLOAD = '<img src=x onerror=alert(1)>'

    def test_f2_agent_controlled_strings_are_escaped(self):
        payload = self.PAYLOAD
        steps = [
            step('cat ' + payload, kind='read', section=payload,
                 context=[payload], why=payload),
            step('python3 -', kind='unknown', script=payload),
        ]
        out = render(row(steps, headline=payload, flags=['follows']))
        combined = out['headline'] + out['body'] + out['spiedHeadline'] + out['spiedBody']
        # Nothing agent-controlled reaches the DOM raw.
        assert payload not in combined
        assert '&lt;img' in combined
        # And it went through escapeHtml (not some other escaper).
        for value in [payload, 'cat ' + payload]:
            assert value in out['escapeCalls'], value
        # The context chip, section header and script body are all covered.
        assert out['escapeCalls'].count(payload) >= 4


class TestSurfaces:

    def test_f2_context_panel_has_breakdown_sections(self):
        html = open(INDEX_HTML, encoding='utf-8').read()
        assert 'ctx-breakdown-head' in html
        assert 'ctx-breakdown-steps' in html

    def test_f2_history_link_and_raw_toggle_exist(self):
        src = open(APP_JS, encoding='utf-8').read()
        assert 'toggleHistoryBreakdown' in src
        assert '/breakdown' in src
        # The `X` queue shortcut toggles the raw command.
        assert "k === 'x' || k === 'X'" in src
