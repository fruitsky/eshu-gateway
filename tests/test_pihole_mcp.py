"""Pi-hole MCP integration tests.

Covers the two-instance model (one integration record per box, MCP tools
namespaced by integration name), the query_token auth type, numeric-string
parsing, the v5 []-on-bad-token fingerprint, and the always-gated write tools.
"""
import http.server
import json
import threading
from urllib.parse import urlparse, parse_qs

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool
from core.secret_scrub import scrub_body


def _data_for(token):
    if token == 'pihole2-token':
        return {'status': 'enabled', 'dns_queries_today': '355,179',
                'ads_blocked_today': '1,693', 'ads_percentage_today': '0.5',
                'domains_being_blocked': '102,364', 'queries_forwarded': '300,000',
                'queries_cached': '55,179', 'clients_ever_seen': '42',
                'unique_clients': '30', 'unique_domains': '102,364', 'privacy_level': 0,
                'dns_queries_all_types': '355,179', 'reply_NODATA': '2,000',
                'reply_UNKNOWN': '1,000', 'gravity_last_updated': 1724000000}
    if token == 'pihole3-token':
        return {'status': 'enabled', 'dns_queries_today': '28,894',
                'ads_blocked_today': '306', 'ads_percentage_today': '1.1',
                'domains_being_blocked': '5,781', 'queries_forwarded': '20,000',
                'queries_cached': '8,894', 'unique_clients': '8', 'privacy_level': 0}
    return None


@pytest.fixture
def pihole_upstream():
    """Threaded upstream mimicking Pi-hole v5 /admin/api.php. Returns [] (the
    v5 auth-failure fingerprint) for any unknown token."""
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query, keep_blank_values=True)
            token = (params.get('auth') or [''])[0]
            state['requests'].append({
                'path': parsed.path, 'query': parsed.query, 'token': token,
                'disable': (params.get('disable') or [''])[0],
            })
            if _data_for(token) is None:
                payload = []
            elif 'version' in params:
                payload = {'version': 3}
            elif 'status' in params or 'enable' in params:
                payload = {'status': 'enabled'}
            elif 'disable' in params:
                payload = {'status': 'disabled'}
            else:
                payload = _data_for(token)
            body = json.dumps(payload).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}/admin", 'state': state}
    server.shutdown()
    server.server_close()


def _make(name, token, upstream, gate_mode='destructive'):
    create_integration(name, upstream['base_url'], "query_token", token,
                       kind="pihole", gate_mode=gate_mode)
    seed_for_kind(get_integration(name))
    return get_integration(name)


class TestPiholeReads:

    def test_summary_numeric_parsing(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'summary', {}))
        assert out['status'] == 'enabled'
        assert out['queriesToday'] == 355179
        assert out['adsBlockedToday'] == 1693
        assert out['adsPct'] == 0.5
        assert out['domainsBlocked'] == 102364
        assert out['clientsSeen'] == 30
        assert pihole_upstream['state']['requests'][0]['token'] == 'pihole2-token'

    def test_two_instances_distinct_targets(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        _make('pihole3', 'pihole3-token', pihole_upstream)
        out2 = json.loads(run_tool('pihole2', 'summary', {}))
        out3 = json.loads(run_tool('pihole3', 'summary', {}))
        assert out2['queriesToday'] == 355179
        assert out3['queriesToday'] == 28894
        assert out3['adsPct'] == 1.1
        toks = [r['token'] for r in pihole_upstream['state']['requests']]
        assert toks == ['pihole2-token', 'pihole3-token']

    def test_summary_full_adds_replies(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'summary', {'full': True}))
        assert out['queriesAllTypes'] == 355179
        assert out['replies']['NODATA'] == 2000
        assert out['gravityLastUpdated'] == 1724000000

    def test_bad_token_returns_invalid_token(self, pihole_upstream):
        _make('pihole-bad', 'wrong-token', pihole_upstream)
        out = json.loads(run_tool('pihole-bad', 'summary', {}))
        assert out['error'] == 'invalid_token'
        out = json.loads(run_tool('pihole-bad', 'status', {}))
        assert out['error'] == 'invalid_token'

    def test_status(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'status', {}))
        assert out == {'status': 'enabled'}

    def test_api_version(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'api_version', {}))
        assert out == {'apiVersion': 3}

    def test_scrubber_masks_auth_in_body(self):
        assert 'auth=secrettoken' not in scrub_body('{"url": "api.php?enable&auth=secrettoken"}')


class TestPiholeWrites:

    def test_enable_blocking_gated_and_executes(self, auth_client, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'enable_blocking', {}, reason='re-enable'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in pihole_upstream['state']['requests'] if 'enable' in r['query'])
        assert req['token'] == 'pihole2-token'
        result = json.loads(get_pending_call(out['id'])['result'])
        assert 'Blocking re-enabled' in json.loads(result['body'])['hint']

    def test_disable_blocking_plain(self, auth_client, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'disable_blocking', {}, reason='maintenance'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in pihole_upstream['state']['requests'] if 'disable' in r['query'])
        assert req['disable'] == ''

    def test_disable_blocking_timed(self, auth_client, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        out = json.loads(run_tool('pihole2', 'disable_blocking',
                                  {'durationSeconds': 300}, reason='5 min'))
        assert out['status'] == 'pending'
        assert auth_client.post(f'/api/integration-calls/{out["id"]}/approve').status_code == 200
        req = next(r for r in pihole_upstream['state']['requests'] if 'disable' in r['query'])
        assert req['disable'] == '300'

    def test_no_generic_floor(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        names = {t['name'] for t in get_tools(get_integration('pihole2')['id'])}
        assert 'read' not in names and 'write' not in names
        assert {'summary', 'status', 'api_version', 'enable_blocking',
                'disable_blocking'} <= names
        writes = [t for t in get_tools(get_integration('pihole2')['id']) if not t['read_only']]
        assert len(writes) == 2
        assert all(t['always_gate'] for t in writes)


class TestPiholeApi:

    def test_create_integration_accepts_query_token(self, auth_client):
        """query_token must pass the create/update auth_type validation."""
        r = auth_client.post("/api/integrations", json={
            "name": "pihole2", "base_url": "http://x/admin", "auth_type": "query_token",
            "secret": "big-token", "kind": "pihole",
        })
        assert r.status_code == 200
        row = next(x for x in auth_client.get("/api/integrations").json() if x["name"] == "pihole2")
        assert row["auth_type"] == "query_token"
        # update path accepts it too (round-trip)
        r = auth_client.put("/api/integrations/pihole2", json={"secret": "new-token"})
        assert r.status_code == 200


class TestMcpNamespace:

    def test_namespaced_by_integration_name(self, pihole_upstream):
        _make('pihole2', 'pihole2-token', pihole_upstream)
        _make('pihole3', 'pihole3-token', pihole_upstream)
        from core import mcp_server
        mcp_server.refresh_mcp_tools()
        names = mcp_server._registered_names
        assert 'pihole2_summary' in names
        assert 'pihole3_summary' in names
        assert 'pihole2_disable_blocking' in names
        assert 'pihole3_enable_blocking' in names
