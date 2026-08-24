"""Sonarr + Radarr (*arr) MCP integration tests.

Covers the two-app model (kind sonarr/radarr, X-Api-Key header, name-based MCP
namespace), compact projections, the always-gated writes, and the guardrail:
search flags / deleteFiles / removeFromClient all default OFF and are
server-merged / echoed in the request.
"""
import http.server
import json
import threading
from urllib.parse import urlparse, parse_qs

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool


@pytest.fixture
def arr_upstream():
    """Threaded upstream mimicking an *arr /api/v3 app. Routes by X-Api-Key
    header; unknown keys get the 401 problem-details."""
    state = {'requests': []}

    def _sonarr_series():
        return [{'id': 5, 'title': 'Bluey', 'year': 2018, 'status': 'continuing',
                 'monitored': True, 'qualityProfileId': 1,
                 'language': {'id': 18, 'name': 'Portuguese (PT)'},
                 'path': '/media/series/Bluey', 'tags': [1],
                 'statistics': {'episodeFileCount': 120, 'episodeCount': 154,
                                'sizeOnDisk': 1048576},
                 'overview': 'A blue heeler.'}]

    def _radarr_movies():
        return [{'id': 7, 'title': 'Jaws', 'year': 1975, 'status': 'released',
                 'monitored': True, 'qualityProfileId': 1, 'hasFile': True,
                 'path': '/media/movies/Jaws', 'tags': [], 'sizeOnDisk': 2097152,
                 'overview': 'Shark.'}]

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
            key = self._key()
            if key not in ('sonarr-key', 'radarr-key'):
                self._respond(401, {'message': 'API Key Invalid'})
                return
            path = urlparse(self.path).path
            if path == '/api/v3/system/status':
                self._respond(200, {
                    'appName': 'Sonarr' if key == 'sonarr-key' else 'Radarr',
                    'version': '4.0.16.2944' if key == 'sonarr-key' else '5.17.1',
                    'branch': 'main', 'isDocker': True,
                    'startTime': '2026-01-01T00:00:00Z'})
            elif path == '/api/v3/series':
                self._respond(200, _sonarr_series())
            elif path == '/api/v3/movie':
                self._respond(200, _radarr_movies())
            elif path == '/api/v3/queue':
                self._respond(200, {'totalRecords': 1, 'records': [
                    {'id': 10, 'title': 'Show S01E01', 'status': 'downloading',
                     'trackedDownloadStatus': 'ok', 'errorMessage': None,
                     'sizeleft': 100, 'timeleft': '00:01:00'}]})
            elif path == '/api/v3/history':
                self._respond(200, {'totalRecords': 1, 'records': [
                    {'id': 1, 'eventType': 'grabbed', 'title': 'Show S01E01',
                     'date': '2026-08-21T10:00:00Z',
                     'quality': {'quality': {'id': 1, 'name': 'HDTV-720p'}},
                     'indexer': {'id': 1, 'name': 'Nyaa'},
                     'language': {'id': 18, 'name': 'Portuguese (PT)'}}]})
            elif path == '/api/v3/qualityprofile':
                self._respond(200, [{'id': 1, 'name': 'HD-1080p', 'cutoff': 3,
                                     'items': [{'quality': {'id': 1, 'name': 'HDTV-720p'},
                                                'allowed': True}]}])
            elif path == '/api/v3/customformat':
                self._respond(200, [{'id': 3, 'name': 'PT-PT Dub',
                                     'includeCustomFormatWhenRenaming': False,
                                     'specifications': [{'implementation': 'LanguageSpecification',
                                                         'negate': False}]}])
            elif path == '/api/v3/language':
                self._respond(200, [{'id': 1, 'name': 'English'},
                                    {'id': 18, 'name': 'Portuguese (PT)'},
                                    {'id': 33, 'name': 'Portuguese (BR)'}])
            elif path == '/api/v3/rootfolder':
                self._respond(200, [{'id': 1, 'path': '/media/series',
                                     'accessible': True, 'freeSpace': 1099511627776}])
            elif path.startswith('/api/v3/command/'):
                self._respond(200, {'id': int(path.rsplit('/', 1)[-1]),
                                    'name': 'RefreshSeries', 'status': 'completed',
                                    'started': 't0', 'ended': 't1', 'duration': '00:00:05'})
            else:
                self._respond(404, {'message': 'Not found'})

        def do_POST(self):
            self._record('POST', self._body())
            self._respond(201, {'id': 99})

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


def _make(name, key, kind, upstream, gate_mode='destructive'):
    create_integration(name, upstream['base_url'], "header", key,
                       auth_header_name="X-Api-Key", kind=kind, gate_mode=gate_mode)
    seed_for_kind(get_integration(name))
    return get_integration(name)


class TestArrReads:

    def test_system_status_both_apps(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        _make('radarr', 'radarr-key', 'radarr', arr_upstream)
        s = json.loads(run_tool('sonarr', 'system_status', {}))
        r = json.loads(run_tool('radarr', 'system_status', {}))
        assert s['appName'] == 'Sonarr' and s['version'] == '4.0.16.2944'
        assert r['appName'] == 'Radarr' and r['version'] == '5.17.1'
        keys = [x['key'] for x in arr_upstream['state']['requests']]
        assert keys == ['sonarr-key', 'radarr-key']

    def test_series_projection(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'series', {}))
        assert len(out) == 1
        s = out[0]
        assert s['title'] == 'Bluey' and s['id'] == 5
        assert s['language']['name'] == 'Portuguese (PT)'
        assert s['statistics']['episodeCount'] == 154
        assert 'overview' not in s

    def test_series_full_and_search(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'series', {'full': True}))
        assert out[0]['overview'] == 'A blue heeler.'
        out = json.loads(run_tool('sonarr', 'series', {'search': 'blue'}))
        assert len(out) == 1
        out = json.loads(run_tool('sonarr', 'series', {'search': 'jaws'}))
        assert out == []

    def test_movies_projection(self, arr_upstream):
        _make('radarr', 'radarr-key', 'radarr', arr_upstream)
        out = json.loads(run_tool('radarr', 'movies', {}))
        m = out[0]
        assert m['title'] == 'Jaws' and m['hasFile'] is True
        assert m['sizeOnDisk'] == 2097152

    def test_queue_paginated(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'queue', {}))
        assert out['total'] == 1
        assert out['records'][0]['trackedDownloadStatus'] == 'ok'
        req = arr_upstream['state']['requests'][0]
        assert 'page=1' in req['query'] and 'pageSize=20' in req['query']

    def test_history(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'history', {}))
        rec = out['records'][0]
        assert rec['eventType'] == 'grabbed'
        assert rec['quality'] == 'HDTV-720p'
        assert rec['indexer'] == 'Nyaa'
        assert rec['language'] == 'Portuguese (PT)'

    def test_quality_profiles_custom_formats_languages_rootfolders(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        qp = json.loads(run_tool('sonarr', 'quality_profiles', {}))
        assert qp[0]['name'] == 'HD-1080p' and qp[0]['items'][0]['allowed'] is True
        cf = json.loads(run_tool('sonarr', 'custom_formats', {}))
        assert cf[0]['name'] == 'PT-PT Dub'
        assert cf[0]['specifications'][0]['implementation'] == 'LanguageSpecification'
        langs = json.loads(run_tool('sonarr', 'languages', {}))
        assert {l['name'] for l in langs} >= {'Portuguese (PT)', 'Portuguese (BR)'}
        rf = json.loads(run_tool('sonarr', 'rootfolders', {}))
        assert rf[0]['path'] == '/media/series' and rf[0]['freeSpace'] == 1099511627776

    def test_command_status(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'command_status', {'id': 42}))
        assert out['id'] == 42 and out['status'] == 'completed'
        req = arr_upstream['state']['requests'][0]
        assert req['path'] == '/api/v3/command/42'

    def test_invalid_key(self, arr_upstream):
        _make('bad', 'nope', 'sonarr', arr_upstream)
        out = json.loads(run_tool('bad', 'system_status', {}))
        assert 'invalid_key' in out.get('error', '')


class TestArrWrites:

    def test_writes_gated_even_under_destructive(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        for tool, args in [('add_series', {'body': {'title': 'X'}}),
                           ('update_series', {'id': 5, 'body': {'title': 'X'}}),
                           ('command', {'name': 'RSS Sync'}),
                           ('remove_from_queue', {'id': 10}),
                           ('delete_series', {'id': 5})]:
            out = json.loads(run_tool('sonarr', tool, args, reason='test'))
            assert out['status'] == 'pending', tool

    def test_add_series_search_default_off(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'add_series', {'body': {'title': 'X'}},
                                  reason='add a series'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/api/v3/series')
        body = json.loads(req['body'])
        assert body['title'] == 'X'
        assert body['searchForMissingEpisodes'] is False  # guardrail default merged

    def test_add_series_search_explicit_true(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'add_series',
                                  {'body': {'title': 'X'}, 'searchForMissingEpisodes': True},
                                  reason='add and search'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/api/v3/series')
        assert json.loads(req['body'])['searchForMissingEpisodes'] is True

    def test_update_series_puts_full_body(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'update_series',
                                  {'id': 5, 'body': {'id': 5, 'title': 'Bluey', 'monitored': False}},
                                  reason='unmonitor'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'PUT' and r['path'] == '/api/v3/series/5')
        assert json.loads(req['body'])['monitored'] is False

    def test_command_body_merge(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'command',
                                  {'name': 'RefreshSeries', 'data': {'seriesId': 5}},
                                  reason='refresh bluey'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/api/v3/command')
        body = json.loads(req['body'])
        assert body['name'] == 'RefreshSeries' and body['seriesId'] == 5

    def test_delete_files_default_false(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'delete_series', {'id': 5}, reason='remove'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'DELETE' and r['path'] == '/api/v3/series/5')
        assert 'deleteFiles=False' in req['query']

    def test_remove_from_queue_params(self, auth_client, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        out = json.loads(run_tool('sonarr', 'remove_from_queue',
                                  {'id': 10, 'removeFromClient': True}, reason='stuck'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in arr_upstream['state']['requests']
                   if r['method'] == 'DELETE' and r['path'] == '/api/v3/queue/10')
        assert 'removeFromClient=True' in req['query']
        assert 'blocklist=False' in req['query']

    def test_no_generic_floor_and_full_catalog(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        names = {t['name'] for t in get_tools(get_integration('sonarr')['id'])}
        assert 'read' not in names and 'write' not in names
        assert {'system_status', 'series', 'queue', 'history', 'quality_profiles',
                'custom_formats', 'languages', 'rootfolders', 'command_status',
                'add_series', 'update_series', 'command', 'remove_from_queue',
                'delete_series'} <= names
        writes = [t for t in get_tools(get_integration('sonarr')['id']) if not t['read_only']]
        assert len(writes) == 5
        assert all(t['always_gate'] for t in writes)

    def test_mcp_namespace_distinct_per_app(self, arr_upstream):
        _make('sonarr', 'sonarr-key', 'sonarr', arr_upstream)
        _make('radarr', 'radarr-key', 'radarr', arr_upstream)
        from core import mcp_server
        mcp_server.refresh_mcp_tools()
        names = mcp_server._registered_names
        assert 'sonarr_series' in names
        assert 'radarr_movies' in names
        assert 'sonarr_add_series' in names
        assert 'radarr_add_movie' in names
