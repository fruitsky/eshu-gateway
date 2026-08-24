"""Prowlarr MCP integration tests.

Covers the credential hard rule: indexer definitions carry real credentials in
`fields[]` — read output never includes them (even full:true), approval-card
display drops them, and the scrubber drop-list + secret_hashes fingerprint
protect audit/approval rows.
"""
import hashlib
import http.server
import json
import threading
from urllib.parse import urlparse

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool
from core.secret_scrub import scrub_body, scrub_value, secret_hashes


def _indexers():
    return [{'id': 1, 'name': 'Nyaa', 'protocol': 'torrent', 'enable': True,
             'priority': 25, 'indexerFeedType': 'RSS', 'sortOrder': 0,
             'implementation': 'Cardigann',
             'fields': [{'name': 'apiKey', 'value': 'NYAA-SECRET-KEY'},
                        {'name': 'baseUrl', 'value': 'https://nyaa.si'}],
             'description': 'Anime torrents', 'tags': [], 'language': 'en'}]


@pytest.fixture
def prowlarr_upstream():
    """Threaded upstream mimicking Prowlarr /api/v1, routed by X-Api-Key."""
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _key(self):
            return self.headers.get('X-Api-Key', '')

        def _record(self, method, body=b''):
            p = urlparse(self.path)
            state['requests'].append({
                'method': method, 'path': p.path, 'query': p.query,
                'key': self._key(), 'body': body.decode('utf-8', 'replace'),
            })

        def _respond(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            return self.rfile.read(length) if length else b''

        def do_GET(self):
            self._record('GET')
            if self._key() != 'prowlarr-key':
                self._respond(401, {'message': 'API Key Invalid'})
                return
            path = urlparse(self.path).path
            if path == '/api/v1/system/status':
                self._respond(200, {'version': '1.29.2', 'appName': 'Prowlarr',
                                    'branch': 'master', 'startTime': '2026-01-01T00:00:00Z'})
            elif path == '/api/v1/indexer':
                self._respond(200, _indexers())
            elif path == '/api/v1/indexerstats':
                self._respond(200, {'indexers': [{'indexerName': 'Nyaa',
                                                  'queryCount': 102, 'grabs': 12,
                                                  'failures': 2}]})
            elif path == '/api/v1/indexerstatus':
                self._respond(200, [{'indexerId': 1, 'indexerName': 'Nyaa',
                                     'disabledTill': '2026-08-21T12:00:00Z',
                                     'mostRecentFailure': 'timeout', 'escalation': 3,
                                     'attemptedQueries': 5}])
            else:
                self._respond(404, {'message': 'Not found'})

        def do_POST(self):
            self._record('POST', self._body())
            self._respond(201, {'id': 9})

        def do_PUT(self):
            self._record('PUT', self._body())
            self._respond(200, {})

        def do_DELETE(self):
            self._record('DELETE')
            self._respond(200, {})

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream, gate_mode='destructive'):
    create_integration("prowlarr", upstream['base_url'], "header", "prowlarr-key",
                       auth_header_name="X-Api-Key", kind="prowlarr", gate_mode=gate_mode)
    seed_for_kind(get_integration("prowlarr"))
    return get_integration("prowlarr")


class TestScrubberFields:

    def test_scrub_drops_fields_key(self):
        out = scrub_value({'name': 'Nyaa', 'fields': [{'name': 'apiKey', 'value': 'SECRET'}]})
        assert out == {'name': 'Nyaa'}
        assert 'SECRET' not in json.dumps(out)

    def test_scrub_body_drops_fields(self):
        body = scrub_body('{"indexers": [{"name": "Nyaa", "fields": [{"name": "password", "value": "pw"}]}]}')
        assert 'fields' not in body
        assert 'pw' not in body

    def test_scrub_preserves_dict_fields(self):
        """Home Assistant service-schema `fields` is a dict — it must NOT be
        dropped (only list-shaped `fields` like Prowlarr's are)."""
        out = scrub_value({'light': {'turn_on': {'fields': {'entity_id': {'selector': {'entity': {}}}}}}})
        assert out['light']['turn_on']['fields']['entity_id'] == {'selector': {'entity': {}}}

    def test_secret_hashes_fingerprint_fields(self):
        h = secret_hashes({'body': {'fields': [{'name': 'apiKey', 'value': 'NYAA-SECRET-KEY'}]}})
        assert 'body.fields' in h
        assert h['body.fields'] == hashlib.sha256(
            json.dumps([{'name': 'apiKey', 'value': 'NYAA-SECRET-KEY'}], sort_keys=True).encode()).hexdigest()


class TestProwlarrReads:

    def test_system_status(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'system_status', {}))
        assert out['appName'] == 'Prowlarr' and out['version'] == '1.29.2'

    def test_indexers_never_include_fields(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        for args in ({}, {'full': True}):
            out = json.loads(run_tool('prowlarr', 'indexers', args))
            raw = json.dumps(out)
            assert 'fields' not in raw
            assert 'NYAA-SECRET-KEY' not in raw
            assert out[0]['name'] == 'Nyaa'
            assert out[0]['protocol'] == 'torrent'
        full = json.loads(run_tool('prowlarr', 'indexers', {'full': True}))
        assert full[0]['description'] == 'Anime torrents'

    def test_indexers_search(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'indexers', {'search': 'nya'}))
        assert len(out) == 1
        out = json.loads(run_tool('prowlarr', 'indexers', {'search': 'zzz'}))
        assert out == []

    def test_indexer_stats(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'indexer_stats', {}))
        assert out['total'] == 1
        row = out['indexers'][0]
        # live API uses queryCount/grabs/failures — the superset projection
        # surfaces whichever count names the build returns
        assert row['queryCount'] == 102
        assert row['grabs'] == 12
        assert row['failures'] == 2

    def test_indexer_status(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'indexer_status', {}))
        row = out[0]
        assert row['indexerName'] == 'Nyaa'
        assert row['mostRecentFailure'] == 'timeout'
        assert row['disabledTill'] == '2026-08-21T12:00:00Z'
        assert row['escalation'] == 3

    def test_invalid_key(self, prowlarr_upstream):
        create_integration("bad", prowlarr_upstream['base_url'], "header", "nope",
                           auth_header_name="X-Api-Key", kind="prowlarr")
        seed_for_kind(get_integration("bad"))
        out = json.loads(run_tool('bad', 'system_status', {}))
        assert 'invalid_key' in out.get('error', '')


class TestProwlarrWrites:

    def test_writes_gated_even_under_destructive(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        for tool, args in [('add_indexer', {'body': {'name': 'X'}}),
                           ('update_indexer', {'id': 1, 'body': {'name': 'X'}}),
                           ('delete_indexer', {'id': 1}),
                           ('sync_indexers', {})]:
            out = json.loads(run_tool('prowlarr', tool, args, reason='test'))
            assert out['status'] == 'pending', tool

    def test_add_indexer_card_hides_fields_but_stored_for_execution(self, auth_client, prowlarr_upstream):
        _make(prowlarr_upstream)
        body = {'name': 'Nyaa', 'fields': [{'name': 'apiKey', 'value': 'NYAA-SECRET-KEY'}]}
        out = json.loads(run_tool('prowlarr', 'add_indexer', {'body': body}, reason='add nyaa'))
        call_id = out['id']
        # Approval card display drops fields entirely
        calls = auth_client.get('/api/integration-calls/pending').json()
        row = next(c for c in calls if c['id'] == call_id)
        assert 'NYAA-SECRET-KEY' not in json.dumps(row['payload'])
        assert 'fields' not in json.dumps(row['payload'])
        assert row['payload']['body']['name'] == 'Nyaa'
        # The stored row keeps the real body so the approval executes as-is
        assert get_pending_call(call_id)['payload']['body']['fields'][0]['value'] == 'NYAA-SECRET-KEY'
        assert auth_client.post(f'/api/integration-calls/{call_id}/approve').status_code == 200
        req = next(r for r in prowlarr_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/api/v1/indexer')
        assert json.loads(req['body'])['fields'][0]['value'] == 'NYAA-SECRET-KEY'

    def test_resolve_strips_fields(self, auth_client, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'add_indexer',
                                  {'body': {'name': 'X', 'fields': [{'name': 'apiKey', 'value': 'S'}]}},
                                  reason='add'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        row = get_pending_call(out['id'])
        assert 'S' not in json.dumps(row['payload'])
        assert row['secret_hashes'].get('body.fields')

    def test_delete_indexer(self, auth_client, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'delete_indexer', {'id': 1}, reason='remove'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in prowlarr_upstream['state']['requests']
                   if r['method'] == 'DELETE' and r['path'] == '/api/v1/indexer/1')
        assert req is not None

    def test_sync_indexers(self, auth_client, prowlarr_upstream):
        _make(prowlarr_upstream)
        out = json.loads(run_tool('prowlarr', 'sync_indexers', {}, reason='propagate'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in prowlarr_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/api/v1/indexer/sync')
        assert req is not None
        result = json.loads(get_pending_call(out['id'])['result'])
        assert 'Sonarr AND Radarr' in json.loads(result['body'])['hint']

    def test_no_generic_floor_and_catalog(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        names = {t['name'] for t in get_tools(get_integration('prowlarr')['id'])}
        assert 'read' not in names and 'write' not in names
        assert {'system_status', 'indexers', 'indexer_stats', 'indexer_status',
                'add_indexer', 'update_indexer', 'delete_indexer',
                'sync_indexers'} <= names
        writes = [t for t in get_tools(get_integration('prowlarr')['id']) if not t['read_only']]
        assert len(writes) == 4
        assert all(t['always_gate'] for t in writes)

    def test_mcp_namespace(self, prowlarr_upstream):
        _make(prowlarr_upstream)
        from core import mcp_server
        mcp_server.refresh_mcp_tools()
        assert 'prowlarr_indexers' in mcp_server._registered_names
        assert 'prowlarr_sync_indexers' in mcp_server._registered_names
