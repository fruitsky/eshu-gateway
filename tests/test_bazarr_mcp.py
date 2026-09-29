"""Bazarr MCP integration tests.

Covers the new kind: X-API-KEY header auth, the verified /api route shapes
(envelope unwrapping, `seriesid[]`/`episodeid[]` arrays), the always-gated
subtitle-search write, the generic read/write floor, and the /api prefix
injection for generic calls.
"""
import http.server
import json
import threading
from urllib.parse import urlparse, parse_qs

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool


def _series():
    return {'data': [
        {'sonarrSeriesId': 88, 'title': 'Silo', 'profileId': 1,
         'path': '/data/shows/Silo (2023)', 'episodeFileCount': 10,
         'episodeMissingCount': 1},
        {'sonarrSeriesId': 5, 'title': 'Bluey', 'profileId': 1,
         'path': '/data/shows/Bluey', 'episodeFileCount': 120,
         'episodeMissingCount': 0},
    ], 'total': 2}


def _episodes():
    return {'data': [
        {'sonarrSeriesId': 88, 'sonarrEpisodeId': 3746, 'season': 1,
         'episode': 10, 'title': 'Outside', 'monitored': True,
         'path': '/data/shows/Silo/Season 1/Silo - S01E10.mkv',
         'missing_subtitles': [], 'subtitles': [{'code2': 'en'}],
         'audio_language': 'en'},
    ]}


def _history():
    return {'data': [
        {'sonarrSeriesId': 88, 'sonarrEpisodeId': 3746, 'language': 'en',
         'provider': 'OpenSubtitles', 'action': 'downloaded'},
    ], 'total': 1}


@pytest.fixture
def bazarr_upstream():
    """Threaded upstream mimicking Bazarr /api, routed by X-API-KEY header."""
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _key(self):
            return self.headers.get('X-API-KEY', '')

        def _record(self, method, body=b''):
            p = urlparse(self.path)
            state['requests'].append({
                'method': method, 'path': p.path, 'query': parse_qs(p.query),
                'key': self._key(), 'body': body.decode('utf-8', 'replace'),
            })

        def _respond(self, status, payload=None):
            body = json.dumps(payload).encode('utf-8') if payload is not None else b''
            self.send_response(status)
            if payload is not None:
                self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            return self.rfile.read(length) if length else b''

        def do_GET(self):
            self._record('GET')
            if self._key() != 'bazarr-key':
                self._respond(401, {'message': 'Not Authenticated'})
                return
            path = urlparse(self.path).path
            if path == '/api/system/status':
                self._respond(200, {'data': {'version': '1.5.5', 'branch': 'master'}})
            elif path == '/api/series':
                self._respond(200, _series())
            elif path == '/api/episodes':
                self._respond(200, _episodes())
            elif path == '/api/episodes/history':
                self._respond(200, _history())
            else:
                self._respond(404, {'message': 'Not found'})

        def do_PATCH(self):
            self._record('PATCH', self._body())
            self._respond(204)

        def do_POST(self):
            self._record('POST', self._body())
            self._respond(201, {'id': 1})

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream, gate_mode='destructive'):
    create_integration("bazarr", upstream['base_url'], "header", "bazarr-key",
                       auth_header_name="X-API-KEY", kind="bazarr", gate_mode=gate_mode)
    seed_for_kind(get_integration("bazarr"))
    return get_integration("bazarr")


class TestBazarrReads:

    def test_system_status_unwraps(self, bazarr_upstream):
        _make(bazarr_upstream)
        out = json.loads(run_tool('bazarr', 'system_status', {}))
        assert out['version'] == '1.5.5'

    def test_series_envelope_and_search(self, bazarr_upstream):
        _make(bazarr_upstream)
        out = json.loads(run_tool('bazarr', 'series', {}))
        assert len(out) == 2 and out[0]['sonarrSeriesId'] == 88
        out = json.loads(run_tool('bazarr', 'series', {'search': 'blue'}))
        assert len(out) == 1 and out[0]['title'] == 'Bluey'
        out = json.loads(run_tool('bazarr', 'series', {'limit': 1}))
        assert len(out) == 1

    def test_episodes_array_params(self, bazarr_upstream):
        _make(bazarr_upstream)
        out = json.loads(run_tool('bazarr', 'episodes', {'seriesId': 88, 'episodeId': 3746}))
        assert out[0]['sonarrEpisodeId'] == 3746
        assert out[0]['missing_subtitles'] == []
        req = next(r for r in bazarr_upstream['state']['requests'] if r['path'] == '/api/episodes')
        assert req['query']['seriesid[]'] == ['88']
        assert req['query']['episodeid[]'] == ['3746']

    def test_history(self, bazarr_upstream):
        _make(bazarr_upstream)
        out = json.loads(run_tool('bazarr', 'history', {}))
        assert out['total'] == 1 and out['records'][0]['language'] == 'en'

    def test_invalid_key(self, bazarr_upstream):
        create_integration("bad", bazarr_upstream['base_url'], "header", "nope",
                           auth_header_name="X-API-KEY", kind="bazarr")
        seed_for_kind(get_integration("bad"))
        out = json.loads(run_tool('bad', 'system_status', {}))
        assert 'invalid_key' in out.get('error', '')


class TestBazarrWrites:

    def test_search_subtitles_gated_and_body(self, auth_client, bazarr_upstream):
        _make(bazarr_upstream)
        out = json.loads(run_tool('bazarr', 'search_subtitles', {'seriesId': 88},
                                  reason='fetch subs'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in bazarr_upstream['state']['requests'] if r['method'] == 'PATCH')
        body = json.loads(req['body'])
        assert req['path'] == '/api/series'
        assert body['seriesid'] == 88 and body['action'] == 'search-missing'
        result = json.loads(get_pending_call(out['id'])['result'])
        assert 'hint' in json.loads(result['body'])

    def test_generic_read_prefix(self, bazarr_upstream):
        _make(bazarr_upstream)
        # Generic read returns the raw envelope (no transform) but must hit
        # /api/series — i.e. the /api prefix was injected.
        out = json.loads(run_tool('bazarr', 'read', {'path': '/series'}))
        assert out['data'][0]['sonarrSeriesId'] == 88
        assert any(r['path'] == '/api/series' for r in bazarr_upstream['state']['requests'])

    def test_generic_write_always_gated(self, auth_client, bazarr_upstream):
        # Even under the benign gate_mode, the generic write must require approval.
        _make(bazarr_upstream, gate_mode='destructive')
        out = json.loads(run_tool('bazarr', 'write',
                                  {'method': 'POST', 'path': 'series', 'data': {'x': 1}},
                                  reason='touch'))
        assert out['status'] == 'pending'

    def test_mcp_namespace(self, bazarr_upstream):
        _make(bazarr_upstream)
        from core import mcp_server
        mcp_server.refresh_mcp_tools()
        assert 'bazarr_series' in mcp_server._registered_names
        assert 'bazarr_search_subtitles' in mcp_server._registered_names
        assert 'bazarr_read' in mcp_server._registered_names

    def test_catalog_and_floor(self, bazarr_upstream):
        _make(bazarr_upstream)
        names = {t['name'] for t in get_tools(get_integration('bazarr')['id'])}
        assert {'system_status', 'series', 'episodes', 'history',
                'search_subtitles', 'read', 'write'} <= names
        writes = [t for t in get_tools(get_integration('bazarr')['id']) if not t['read_only']]
        assert {t['name'] for t in writes} == {'search_subtitles', 'write'}
        assert all(t['always_gate'] for t in writes)
