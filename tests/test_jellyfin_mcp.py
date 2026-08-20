"""Jellyfin MCP integration tests.

Covers the fully-curated jellyfin_* seed (no generic passthrough), the
PascalCase response transforms, stable error mapping, always-gated writes, and
the scan_library path-variant/query behaviour.
"""
import http.server
import json
import threading
from urllib.parse import urlparse

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool


@pytest.fixture
def jellyfin_upstream():
    """Threaded upstream mimicking Jellyfin 10.11 (PascalCase, X-Emby-Token)."""
    state = {'requests': [], 'fail': None}

    def _log_body():
        return '\n'.join(f'2026-08-20T20:00:{i:02d}Z line {i}' for i in range(100))

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _record(self, method, body=b''):
            p = urlparse(self.path)
            state['requests'].append({
                'method': method, 'path': p.path, 'query': p.query,
                'api_key': self.headers.get('X-Emby-Token', ''),
                'body': body.decode('utf-8', 'replace'),
            })

        def _respond(self, status, payload):
            body = payload if isinstance(payload, str) else json.dumps(payload)
            body = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._record('GET')
            if state['fail']:
                self._respond(*state['fail'])
                return
            path = urlparse(self.path).path
            if path == '/System/Info':
                self._respond(200, {'Id': '5dc36f32399742309bc253761900d6f6',
                                    'Version': '10.11.11', 'ServerName': 'jellyfin',
                                    'OperatingSystem': 'Linux', 'SystemArchitecture': 'X64',
                                    'CachePath': '/var/lib/jellyfin/cache',
                                    'LogPath': '/var/log/jellyfin',
                                    'TranscodingTempPath': '/mnt/jellyfincache/transcodes',
                                    'WebPath': '/usr/lib/jellyfin', 'VersionName': 'stable',
                                    'HasPendingRestart': False})
            elif path == '/Library/VirtualFolders':
                self._respond(200, [
                    {'Name': 'Movies', 'CollectionType': 'movies',
                     'Locations': ['/mnt/nasdownloads/movies'], 'ItemId': 'e3d0a'},
                    {'Name': 'Shows', 'CollectionType': 'tvshows',
                     'Locations': ['/mnt/nasdownloads/shows'], 'ItemId': 'f4b1'},
                ])
            elif path == '/Items/Counts':
                self._respond(200, {'MovieCount': 36, 'SeriesCount': 51, 'EpisodeCount': 816})
            elif path == '/Sessions':
                self._respond(200, [
                    {'Id': 's1', 'DeviceName': 'Firefox', 'Client': 'Jellyfin Web',
                     'ApplicationVersion': '10.11.11', 'UserName': 'jellyfin',
                     'IsActive': True, 'LastActivityDate': '2026-08-20T19:59:58.068896Z',
                     'NowPlayingQueueFullItems': [{'Name': 'Movie.mkv', 'Type': 'Movie'}],
                     'PlayState': {'IsPaused': False, 'RepeatMode': 'RepeatNone',
                                   'PlaybackOrder': 'Default'},
                     'TranscodingInfo': {'Bitrate': 4000000, 'TranscodeReasons': ['VideoCodecNotSupported'],
                                         'IsVideoDirect': False, 'IsAudioDirect': True}},
                    {'Id': 's2', 'DeviceName': 'Android', 'Client': 'Jellyfin Android',
                     'ApplicationVersion': '10.11.11', 'UserName': 'jellyfin',
                     'IsActive': False, 'LastActivityDate': '2026-08-19T10:00:00Z',
                     'NowPlayingQueueFullItems': []},
                ])
            elif path == '/ScheduledTasks':
                self._respond(200, [
                    {'Id': 't1', 'Name': 'Clean Transcode Directory', 'State': 'Idle',
                     'Category': 'Maintenance',
                     'LastExecutionResult': {'Status': 'Completed', 'Progress': 100,
                                             'EndTimeUtc': '2026-08-20T02:00:00Z'}},
                    {'Id': 't2', 'Name': 'Extract Chapter Images', 'State': 'Running',
                     'Category': 'Library',
                     'LastExecutionResult': {'Status': 'Running', 'Progress': 40, 'EndTimeUtc': None}},
                ])
            elif path == '/Plugins':
                self._respond(200, [
                    {'Name': 'Subtitle Extract', 'Version': '7.0.0.0', 'Status': 'Active'},
                    {'Name': 'Streamyfin', 'Version': '0.68.1.0', 'Status': 'Restart'},
                ])
            elif path == '/System/ActivityLog/Entries':
                self._respond(200, {'TotalRecordCount': 9419, 'Items': [
                    {'Name': 'Login', 'Type': 'UserAuthenticated', 'Date': '2026-08-20T20:00:00Z',
                     'Severity': 'Info'},
                    {'Name': 'Library Scan', 'Type': 'LibraryChanged', 'Date': '2026-08-20T19:00:00Z',
                     'Severity': 'Info'},
                ]})
            elif path == '/System/Logs':
                self._respond(200, [
                    {'Name': 'FFmpeg.Transcode', 'Size': 482355},
                    {'Name': 'jellyfin', 'Size': 123456},
                ])
            elif path.startswith('/System/Logs/'):
                self._respond(200, _log_body())
            elif path == '/Users':
                self._respond(200, [
                    {'Id': '07978d5c70ef493cbd24d62aafb4848f', 'Name': 'jellyfin',
                     'Policy': {'IsAdministrator': True, 'IsDisabled': False},
                     'IsHidden': False},
                    {'Id': 'guest', 'Name': 'guest'},
                ])
            else:
                self._respond(404, 'not found')

        def _body(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            return self.rfile.read(length) if length else b''

        def do_POST(self):
            self._record('POST', self._body())
            self._respond(200, {})

        def do_DELETE(self):
            self._record('DELETE')
            self._respond(200, {})

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}", 'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream, gate_mode='destructive'):
    create_integration("jellyfin", upstream['base_url'], "header", "jf-secret-key",
                       auth_header_name="X-Emby-Token", kind="jellyfin",
                       gate_mode=gate_mode)
    seed_for_kind(get_integration("jellyfin"))
    return get_integration("jellyfin")


class TestJellyfinReads:

    def test_system_info_compact(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'system_info', {}))
        assert out['version'] == '10.11.11'
        assert out['serverName'] == 'jellyfin'
        assert out['os'] == 'Linux'
        assert out['transcodePath'] == '/mnt/jellyfincache/transcodes'
        assert out['id'] == '5dc36f32399742309bc253761900d6f6'
        assert 'OperatingSystem' not in out  # PascalCase raw not passed through

    def test_system_info_full(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'system_info', {'full': True}))
        assert out['versionName'] == 'stable'
        assert out['hasPendingRestart'] is False

    def test_libraries_array(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'libraries', {}))
        assert len(out) == 2
        m = next(x for x in out if x['name'] == 'Movies')
        assert m['type'] == 'movies'
        assert m['locations'] == ['/mnt/nasdownloads/movies']
        assert m['itemId'] == 'e3d0a'
        assert 'CollectionType' not in m
        out = json.loads(run_tool('jellyfin', 'libraries', {'search': 'show'}))
        assert len(out) == 1 and out[0]['name'] == 'Shows'

    def test_item_counts_passthrough(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'item_counts', {}))
        assert out == {'MovieCount': 36, 'SeriesCount': 51, 'EpisodeCount': 816}

    def test_sessions_now_playing_from_queue(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'sessions', {}))
        active = next(x for x in out if x['deviceName'] == 'Firefox')
        assert active['nowPlaying'] == {'name': 'Movie.mkv', 'type': 'Movie'}
        assert active['playState']['isPaused'] is False
        assert active['transcode']['bitrate'] == 4000000
        idle = next(x for x in out if x['deviceName'] == 'Android')
        assert 'nowPlaying' not in idle

    def test_sessions_active_only_and_search(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'sessions', {'activeOnly': True}))
        assert len(out) == 1 and out[0]['deviceName'] == 'Firefox'
        out = json.loads(run_tool('jellyfin', 'sessions', {'search': 'android'}))
        assert len(out) == 1 and out[0]['deviceName'] == 'Android'

    def test_scheduled_tasks(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'scheduled_tasks', {}))
        t = next(x for x in out if x['id'] == 't1')
        assert t['name'] == 'Clean Transcode Directory'
        assert t['category'] == 'Maintenance'
        assert t['lastStatus'] == 'Completed'
        assert t['lastProgress'] == 100
        out = json.loads(run_tool('jellyfin', 'scheduled_tasks', {'category': 'Library'}))
        assert len(out) == 1 and out[0]['id'] == 't2'

    def test_plugins_surfaces_status(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'plugins', {}))
        st = next(x for x in out if x['name'] == 'Streamyfin')
        assert st['status'] == 'Restart'
        assert st['version'] == '0.68.1.0'

    def test_activity_log(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'activity_log', {}))
        assert out['total'] == 9419
        assert len(out['entries']) == 2
        assert out['entries'][0]['name'] == 'Login'
        assert out['entries'][0]['severity'] == 'Info'
        req = jellyfin_upstream['state']['requests'][0]
        assert 'limit=20' in req['query']
        out = json.loads(run_tool('jellyfin', 'activity_log', {'search': 'scan', 'limit': 5}))
        assert len(out['entries']) == 1 and out['entries'][0]['name'] == 'Library Scan'

    def test_logs_and_get_log(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'logs', {}))
        assert {'name', 'size'} <= set(out[0])
        assert out[0]['name'] == 'FFmpeg.Transcode'
        log = json.loads(run_tool('jellyfin', 'get_log', {'name': 'FFmpeg.Transcode', 'tailLines': 10}))
        assert log['name'] == 'FFmpeg.Transcode'
        assert log['lines'] == 10
        assert 'line 90' in log['content']
        assert 'line 0' not in log['content']
        # the working route is /System/Logs/Log?name=... (path form 404s)
        req = next(r for r in jellyfin_upstream['state']['requests'] if r['path'] == '/System/Logs/Log')
        assert 'name=FFmpeg.Transcode' in req['query']

    def test_users(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'users', {}))
        admin = next(x for x in out if x['name'] == 'jellyfin')
        assert admin['id'] == '07978d5c70ef493cbd24d62aafb4848f'
        assert admin['isAdmin'] is True  # from Policy.IsAdministrator
        guest = next(x for x in out if x['name'] == 'guest')
        assert 'isAdmin' not in guest  # omitted, not null

    def test_api_key_header_sent(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        run_tool('jellyfin', 'system_info', {})
        assert jellyfin_upstream['state']['requests'][0]['api_key'] == 'jf-secret-key'


class TestJellyfinErrors:

    def test_invalid_key(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        jellyfin_upstream['state']['fail'] = (401, 'Invalid API key')
        out = json.loads(run_tool('jellyfin', 'system_info', {}))
        assert 'invalid_key' in out.get('error', '')

    def test_not_found_plain_text(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        jellyfin_upstream['state']['fail'] = (404, 'not found')
        out = json.loads(run_tool('jellyfin', 'libraries', {}))
        assert 'not_found' in out.get('error', '')

    def test_method_not_allowed_empty_body(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        jellyfin_upstream['state']['fail'] = (405, '')
        out = json.loads(run_tool('jellyfin', 'get_log', {'name': 'x'}))
        assert 'method_not_allowed' in out.get('error', '')

    def test_unavailable_plain_text(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        jellyfin_upstream['state']['fail'] = (500, 'boom')
        out = json.loads(run_tool('jellyfin', 'sessions', {}))
        assert 'jellyfin_unavailable' in out.get('error', '')


class TestJellyfinWrites:

    def test_writes_gated_even_under_destructive(self, jellyfin_upstream):
        """always_gate means approval is required even though the integration's
        gate mode is 'destructive' (POST restart would otherwise auto-run)."""
        _make(jellyfin_upstream, gate_mode='destructive')
        for tool, args in [('scan_library', {}), ('restart', {}),
                           ('start_task', {'taskId': 't1'}),
                           ('stop_task', {'taskId': 't1'}),
                           ('stop_transcodes', {})]:
            out = json.loads(run_tool('jellyfin', tool, args, reason='test'))
            assert out['status'] == 'pending', tool
            # the poll hint must reference the same id as the JSON body
            assert f'check_approval({out["id"]})' in out['message']

    def test_scan_library_full_path_variant_and_query(self, auth_client, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'scan_library',
                                  {'itemId': 'e3d0a', 'replaceAllMetadata': True},
                                  reason='scan movies'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in jellyfin_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/Items/e3d0a/Refresh')
        assert 'replaceAllMetadata=True' in req['query']
        assert 'replaceAllImages=False' in req['query']
        # the approval result carries the verification hint via check_approval
        result = json.loads(get_pending_call(out['id'])['result'])
        body = json.loads(result['body'])
        assert 'Scan triggered' in body['hint']

    def test_scan_library_defaults_full_refresh(self, auth_client, jellyfin_upstream):
        _make(jellyfin_upstream)
        out = json.loads(run_tool('jellyfin', 'scan_library', {}, reason='scan'))
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in jellyfin_upstream['state']['requests']
                   if r['method'] == 'POST' and r['path'] == '/Library/Refresh')
        assert 'replaceAllMetadata=False' in req['query']

    def test_no_generic_passthrough(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        names = {t['name'] for t in get_tools(get_integration('jellyfin')['id'])}
        assert 'read' not in names and 'write' not in names
        assert 'system_info' in names and 'scan_library' in names

    def test_merge_response_hint(self):
        from core.integration_proxy import merge_response_hint
        tool = {'response_hint': 'verify me'}
        assert json.loads(merge_response_hint(tool, '{}'))['hint'] == 'verify me'
        out = json.loads(merge_response_hint(tool, ''))
        assert out['hint'] == 'verify me'
        out = json.loads(merge_response_hint(tool, 'plain text'))
        assert out['hint'] == 'verify me' and out['content'] == 'plain text'
        assert merge_response_hint({}, '{"a":1}') == '{"a":1}'

    def test_seed_full_catalog(self, jellyfin_upstream):
        _make(jellyfin_upstream)
        names = {t['name'] for t in get_tools(get_integration('jellyfin')['id'])}
        assert {'system_info', 'libraries', 'item_counts', 'sessions',
                'scheduled_tasks', 'plugins', 'activity_log', 'logs',
                'get_log', 'users'}.issubset(names)
        assert {'scan_library', 'restart', 'start_task', 'stop_task',
                'stop_transcodes'}.issubset(names)
        writes = [t for t in get_tools(get_integration('jellyfin')['id'])
                  if not t['read_only']]
        assert len(writes) == 5
        assert all(t['always_gate'] for t in writes)
        assert all(t['error_codes'].get('401') == 'invalid_key' for t in writes)
