"""Seerr integration tests: curated search/details/request tools.

Seerr is the request layer; these tests cover the auth header, compact
projections, the safe request body, and the absence of a generic passthrough.
"""
import http.server
import json
import threading
from urllib.parse import urlparse

import pytest

from core.seeds import seed_for_kind
from core.tool_runner import run_tool
from db.integrations import (
    create_integration,
    get_integration,
    get_pending_calls,
    get_tools,
)


@pytest.fixture
def seerr_upstream():
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _record(self, method, body=b''):
            p = urlparse(self.path)
            state['requests'].append({
                'method': method,
                'path': p.path,
                'query': p.query,
                'key': self.headers.get('X-Api-Key', ''),
                'body': body.decode('utf-8', 'replace'),
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
            if self.headers.get('X-Api-Key', '') != 'seerr-key':
                self._respond(401, {'message': 'Unauthorized'})
                return
            path = urlparse(self.path).path
            if path == '/api/v1/search':
                self._respond(200, {
                    'page': 1, 'totalPages': 1, 'totalResults': 3,
                    'results': [
                        {'id': 10378, 'mediaType': 'movie',
                         'title': 'Big Buck Bunny',
                         'releaseDate': '2008-04-10',
                         'overview': 'A large rabbit gets revenge.',
                         'mediaInfo': {'status': 5}},
                        {'id': 33050, 'mediaType': 'tv',
                         'name': 'Pioneer One',
                         'firstAirDate': '2010-06-16',
                         'overview': 'A Cold War relic returns.',
                         'mediaInfo': {'status': 2, 'seasons': [
                             {'seasonNumber': 1, 'status': 5}]}},
                        {'id': 999, 'mediaType': 'person', 'name': 'Someone'},
                    ]})
            elif path == '/api/v1/movie/10378':
                self._respond(200, {
                    'id': 10378, 'title': 'Big Buck Bunny',
                    'releaseDate': '2008-04-10', 'status': 'Released',
                    'overview': 'A large rabbit gets revenge.', 'runtime': 10,
                    'mediaInfo': {'status': 5}})
            elif path == '/api/v1/tv/33050':
                self._respond(200, {
                    'id': 33050, 'name': 'Pioneer One',
                    'firstAirDate': '2010-06-16', 'status': 'Ended',
                    'overview': 'A Cold War relic returns.',
                    'mediaInfo': {'status': 2},
                    'seasons': [{'seasonNumber': 1, 'episodeCount': 6,
                                 'mediaInfo': {'status': 5}}]})
            elif path == '/api/v1/request':
                self._respond(200, {
                    'pageInfo': {'pages': 1, 'pageSize': 20,
                                 'results': 1, 'page': 1},
                    'results': [{
                        'id': 1, 'status': 2,
                        'media': {'mediaType': 'movie', 'tmdbId': 10378,
                                  'status': 5},
                        'requestedBy': {'displayName': 'household'},
                        'createdAt': '2026-10-25T00:00:00Z'}]})
            elif path == '/api/v1/status':
                self._respond(200, {'version': '3.0.1'})
            else:
                self._respond(404, {'message': 'Not found'})

        def do_POST(self):
            body = self._body()
            self._record('POST', body)
            if self.headers.get('X-Api-Key', '') != 'seerr-key':
                self._respond(401, {'message': 'Unauthorized'})
                return
            try:
                payload = json.loads(body or '{}')
            except ValueError:
                payload = {}
            self._respond(201, {
                'id': 2, 'status': 2,
                'media': {'mediaType': payload.get('mediaType'),
                          'tmdbId': payload.get('mediaId'),
                          'status': 2},
                'requestedBy': {'displayName': 'household'},
                'createdAt': '2026-10-25T00:00:00Z'})

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}",
           'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream):
    create_integration('seerr', upstream['base_url'] + '/api/v1',
                       'header', 'seerr-key',
                       auth_header_name='X-Api-Key', kind='seerr',
                       gate_mode='destructive')
    integration = get_integration('seerr')
    seed_for_kind(integration)
    return integration


class TestSeerrSeed:
    def test_curated_tools_only(self, seerr_upstream):
        integration = _make(seerr_upstream)
        names = {t['name'] for t in get_tools(integration['id'])}
        assert names == {'search', 'movie', 'tv', 'requests', 'request_create'}
        assert 'read' not in names and 'write' not in names

    def test_request_create_is_ungated(self, seerr_upstream):
        integration = _make(seerr_upstream)
        tool = next(t for t in get_tools(integration['id'])
                    if t['name'] == 'request_create')
        assert tool['read_only'] == 0
        assert tool['always_gate'] == 0
        assert tool['handler'] == 'seerr_request_create'


class TestSeerrReads:
    def test_search_projection_filters_people(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'search', {
            'query': 'Big Buck Bunny'}))
        assert out['totalResults'] == 3
        assert len(out['results']) == 2
        movie = out['results'][0]
        assert movie['mediaType'] == 'movie'
        assert movie['id'] == 10378 and movie['mediaStatus'] == 5
        tv = out['results'][1]
        assert tv['mediaType'] == 'tv'
        assert tv['seasons'][0]['seasonNumber'] == 1

    def test_movie_details(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'movie', {'movieId': 10378}))
        assert out['title'] == 'Big Buck Bunny'
        assert out['mediaStatus'] == 5 and out['runtime'] == 10

    def test_tv_details(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'tv', {'tvId': 33050}))
        assert out['name'] == 'Pioneer One'
        assert out['seasons'][0]['status'] == 5

    def test_requests_projection(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'requests', {}))
        row = out['results'][0]
        assert row['status'] == 2
        assert row['mediaType'] == 'movie'
        assert row['tmdbId'] == 10378
        assert row['requestedBy'] == 'household'

    def test_auth_header_on_every_call(self, seerr_upstream):
        _make(seerr_upstream)
        run_tool('seerr', 'search', {'query': 'x'})
        run_tool('seerr', 'movie', {'movieId': 10378})
        assert all(r['key'] == 'seerr-key'
                   for r in seerr_upstream['state']['requests'])


class TestSeerrRequestCreate:
    def test_movie_request_body_is_safe(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'request_create', {
            'mediaType': 'movie', 'mediaId': 10378}, reason='test'))
        assert out['id'] == 2 and out['status'] == 2
        post = next(r for r in seerr_upstream['state']['requests']
                    if r['method'] == 'POST')
        body = json.loads(post['body'])
        assert body == {'mediaType': 'movie', 'mediaId': 10378}
        assert 'is4k' not in body and 'userId' not in body
        assert get_pending_calls() == []

    def test_tv_request_defaults_to_all_seasons(self, seerr_upstream):
        _make(seerr_upstream)
        run_tool('seerr', 'request_create', {
            'mediaType': 'tv', 'mediaId': 33050}, reason='test')
        post = next(r for r in seerr_upstream['state']['requests']
                    if r['method'] == 'POST')
        assert json.loads(post['body'])['seasons'] == 'all'

    def test_tv_request_specific_seasons(self, seerr_upstream):
        _make(seerr_upstream)
        run_tool('seerr', 'request_create', {
            'mediaType': 'tv', 'mediaId': 33050, 'seasons': [1, 2]},
            reason='test')
        post = next(r for r in seerr_upstream['state']['requests']
                    if r['method'] == 'POST')
        assert json.loads(post['body'])['seasons'] == [1, 2]

    def test_bad_media_type_rejected_without_upstream(self, seerr_upstream):
        _make(seerr_upstream)
        out = json.loads(run_tool('seerr', 'request_create', {
            'mediaType': 'person', 'mediaId': 1}, reason='test'))
        assert out['error'] == 'invalid_request'
        assert not [r for r in seerr_upstream['state']['requests']
                    if r['method'] == 'POST']
