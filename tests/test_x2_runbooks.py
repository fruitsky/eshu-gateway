"""Chunk X2 — the runbooks page.

F13's runbooks get an operator page: the same search agents use, a tag
filter, and a reversible retire that hides a runbook from every agent path
while leaving it for the operator. Synthetic data only.
"""
import json
import os
import shutil
import subprocess

import pytest

from core.conclusions import (
    find_runbooks, get_runbook_payload, list_runbook_payloads,
    promote_conclusion, save_conclusion,
)
from core.mcp_server import find_runbooks_fn, get_runbook_fn
from db.audit import get_audit_log
from db.conclusions import (
    confirm_conclusion, get_runbook, search_runbooks, set_runbook_retired,
)
from db.requests import create_request
from db.sessions import upsert_session

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = '192.0.2.10'


def _session(sid='sess-x2', agent='agent-a', goal='fix the web proxy'):
    upsert_session(sid, agent=agent, goal=goal)
    return sid


def _runbook(title='Fix the proxy', tags=('nginx', 'proxy'), sid='sess-x2',
             summary='Restarted nginx and fixed the proxy.',
             command='systemctl restart nginx'):
    """A confirmed conclusion promoted to a runbook, with one real step."""
    _session(sid, goal=title)
    req = create_request(HOST, command, status='approved', ttl=0, session_id=sid)
    row = save_conclusion(
        sid, 'agent-a', outcome='solved', summary=summary,
        worked=[{'ref': 'req:%d' % req, 'note': 'restart worked'}],
        did_not_work=[{'ref': 'req:%d' % req, 'note': 'reload alone was not enough'}],
        root_cause='stale upstream', fix=command,
        follow_ups=[], tags=list(tags), runbook_candidate=True)
    confirm_conclusion(row['id'], note='looks right', by='operator')
    return promote_conclusion(row['id'], title=title, by='operator')


class TestListAndFilters:

    def test_x2_list_returns_title_when_tags_steps_and_session(self, temp_db):
        rb = _runbook()
        rows = list_runbook_payloads('')
        assert len(rows) == 1
        item = rows[0]
        assert set(item) >= {'id', 'title', 'when_to_use', 'tags', 'step_count',
                             'session_id', 'created_at', 'retired'}
        assert item['id'] == rb['id']
        assert item['title'] == 'Fix the proxy'
        assert item['step_count'] == 1
        assert item['tags'] == ['nginx', 'proxy']
        assert item['retired'] is False

    def test_x2_search_uses_the_same_ranking_as_the_agents(self, temp_db):
        _runbook(title='Fix the proxy')
        _runbook(title='Rotate a certificate', tags=['tls'], sid='sess-x2-tls',
                 summary='Rotated the certificate and reloaded the listener.',
                 command='certbot renew')
        for query, expected in (('proxy', 'Fix the proxy'),
                                ('certificate', 'Rotate a certificate')):
            operator = list_runbook_payloads(query)
            agent = find_runbooks(query)['runbooks']
            assert [r['title'] for r in operator] == \
                   [r['title'] for r in agent] == [expected]

    def test_x2_tag_filter(self, temp_db):
        _runbook(title='Fix the proxy', tags=['nginx'])
        _runbook(title='Rotate a certificate', tags=['tls'], sid='sess-x2-tls',
                 summary='Rotated the certificate.',
                 command='certbot renew')
        assert [r['title'] for r in list_runbook_payloads('', ['tls'])] == \
               ['Rotate a certificate']
        assert list_runbook_payloads('', ['nope']) == []

    def test_x2_newest_first(self, temp_db):
        _runbook(title='First')
        _runbook(title='Second', sid='sess-x2-b')
        assert [r['title'] for r in list_runbook_payloads('')] == \
               ['Second', 'First']

    def test_x2_retired_are_hidden_unless_asked(self, temp_db):
        rb = _runbook()
        set_runbook_retired(rb['id'], True)
        assert list_runbook_payloads('') == []
        rows = list_runbook_payloads('', include_retired=True)
        assert len(rows) == 1 and rows[0]['retired'] is True


class TestRetire:

    def test_x2_retire_hides_from_agents_and_restore_brings_it_back(self, temp_db):
        rb = _runbook()
        assert find_runbooks('proxy')['count'] == 1
        assert get_runbook_payload(rb['id']) is not None

        assert set_runbook_retired(rb['id'], True)['retired'] is True

        # Agent paths: the search and the single-runbook read.
        assert find_runbooks('proxy')['count'] == 0
        assert get_runbook_payload(rb['id']) is None
        assert search_runbooks('proxy') == []
        assert json.loads(get_runbook_fn()(runbook_id=rb['id']))['error'] == 'not_found'
        assert json.loads(find_runbooks_fn()(query='proxy'))['count'] == 0

        # The operator can still open it (to restore it).
        assert get_runbook_payload(rb['id'], include_retired=True)['retired'] is True

        assert set_runbook_retired(rb['id'], False)['retired'] is False
        assert find_runbooks('proxy')['count'] == 1
        assert get_runbook_payload(rb['id']) is not None

    def test_x2_a_retired_runbook_cannot_be_promoted_over(self, temp_db):
        """Retire is not a hard delete: the row, its steps and its conclusion
        link are all still there, and the unique conclusion_id still holds."""
        rb = _runbook()
        set_runbook_retired(rb['id'], True)
        row = get_runbook(rb['id'], include_retired=True)
        assert row['steps'] and row['dead_ends']
        assert row['conclusion_id']

    def test_x2_retire_is_idempotent_and_keeps_the_first_time(self, temp_db):
        rb = _runbook()
        first = set_runbook_retired(rb['id'], True, now=1000)['retired_at']
        again = set_runbook_retired(rb['id'], True, now=2000)['retired_at']
        assert first == 1000 and again == 1000
        assert set_runbook_retired(rb['id'], False)['retired_at'] == 0

    def test_x2_retire_unknown_id_is_none(self, temp_db):
        assert set_runbook_retired(999999, True) is None

    def test_x2_get_runbook_hides_retired_by_default(self, temp_db):
        rb = _runbook()
        set_runbook_retired(rb['id'], True)
        assert get_runbook(rb['id']) is None
        assert get_runbook(rb['id'], include_retired=True) is not None


class TestApi:

    def test_x2_list_needs_a_session(self, client, temp_db):
        _runbook()
        assert client.get('/api/runbooks').status_code == 401

    def test_x2_list_returns_the_runbooks(self, auth_client, temp_db):
        rb = _runbook()
        res = auth_client.get('/api/runbooks')
        assert res.status_code == 200
        body = res.json()
        assert body['count'] == 1
        assert body['runbooks'][0]['id'] == rb['id']
        assert body['include_retired'] is False

    def test_x2_list_filters_and_shows_retired(self, auth_client, temp_db):
        rb = _runbook()
        set_runbook_retired(rb['id'], True)
        assert auth_client.get('/api/runbooks').json()['count'] == 0
        body = auth_client.get('/api/runbooks?include_retired=1').json()
        assert body['count'] == 1 and body['runbooks'][0]['retired'] is True
        assert auth_client.get(
            '/api/runbooks?include_retired=1&tag=tls').json()['count'] == 0
        assert auth_client.get(
            '/api/runbooks?include_retired=1&tag=nginx').json()['count'] == 1

    def test_x2_retire_route_is_audited_and_reversible(self, auth_client, temp_db):
        rb = _runbook()
        res = auth_client.post('/api/runbooks/%d/retire' % rb['id'],
                               json={'retired': True})
        assert res.status_code == 200 and res.json()['retired'] is True
        events = [e['event_type'] for e in get_audit_log(limit=20)]
        assert 'runbook_retired' in events
        assert auth_client.get('/api/runbooks').json()['count'] == 0

        res = auth_client.post('/api/runbooks/%d/retire' % rb['id'],
                               json={'retired': False})
        assert res.json()['retired'] is False
        events = [e['event_type'] for e in get_audit_log(limit=20)]
        assert 'runbook_unretired' in events
        assert auth_client.get('/api/runbooks').json()['count'] == 1

    def test_x2_retire_route_needs_a_session(self, client, temp_db):
        rb = _runbook()
        res = client.post('/api/runbooks/%d/retire' % rb['id'],
                          json={'retired': True})
        assert res.status_code == 401
        assert get_runbook(rb['id']) is not None

    def test_x2_retire_unknown_is_404(self, auth_client, temp_db):
        assert auth_client.post('/api/runbooks/999999/retire',
                                json={'retired': True}).status_code == 404

    def test_x2_detail_route_opens_a_retired_runbook(self, auth_client, temp_db):
        rb = _runbook()
        set_runbook_retired(rb['id'], True)
        res = auth_client.get('/api/runbooks/%d' % rb['id'])
        assert res.status_code == 200
        body = res.json()
        assert body['retired'] is True
        assert body['steps'][0]['breakdown']['steps'][0]['kind'] == 'write'


NODE = shutil.which('node')
HARNESS = os.path.join(HERE, 'fixtures', 'x2_harness.js')


class TestEscaping:

    def test_x2_agent_text_is_returned_verbatim_for_the_api_to_hand_over(self, temp_db):
        """The API is not the escaping boundary: the UI escapes. This pins the
        contract — the payload keeps the text byte for byte, so a template that
        forgets esc() is the only place a script tag could ever land."""
        title = '<img src=x onerror=alert(1)> nginx'
        rb = _runbook(title=title)
        assert list_runbook_payloads('')[0]['title'] == title
        assert get_runbook_payload(rb['id'])['title'] == title

    @pytest.mark.skipif(NODE is None, reason='node is not installed')
    def test_x2_the_page_escapes_every_runbook_field(self, temp_db):
        proc = subprocess.run([NODE, HARNESS, json.dumps({
            'payload': '<img src=x onerror=alert(1)>'})],
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert 'error' not in out, out['error']
        payload = '<img src=x onerror=alert(1)>'
        for key in ('list', 'listRetired', 'detail'):
            html = out[key]
            assert payload not in html, key
            assert '&lt;img' in html, key
            assert '<img' not in html, key

    @pytest.mark.skipif(NODE is None, reason='node is not installed')
    def test_x2_the_page_renders_marks_steps_and_dead_ends(self, temp_db):
        proc = subprocess.run([NODE, HARNESS, json.dumps({'payload': 'nginx'})],
                              capture_output=True, text=True, timeout=60)
        out = json.loads(proc.stdout)
        assert 'rb-row-title' in out['list'] and 'Retire' in out['list']
        assert 'rb-retired' in out['listRetired'] and 'Restore' in out['listRetired']
        assert 'rb-step-cmd' in out['detail']      # the command step
        assert 'bd-step' in out['detail']          # its F1 breakdown
        assert 'rb-dead-end' in out['detail']
        assert 'rb-retired-chip' in out['detail']
        assert 'rb-row-title' in out['listRetired']
