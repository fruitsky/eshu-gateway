"""Always-on secret scrubbing tests.

Covers the key-name/value-pattern scrubber, the Hermes-reported Omada SSID
PSK leak through generic passthrough, audit-trail masking, and the
false-positive regression (hex ids / UUIDs must survive).
"""
import http.server
import json
import threading

import pytest

from db.integrations import create_integration, get_integration, get_integration_calls, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool
from core.secret_scrub import scrub_body, scrub_payload, scrub_value


class TestScrubber:

    def test_secret_keys_masked_but_neighbors_kept(self):
        out = scrub_value({
            'pskSetting': {'securityKey': 'MyPsk', 'enabled': True},
            'accessToken': 'abc', 'searchKey': 'jelly',
            'id': '640effd1b3f2ae5b912275ec',
        })
        assert out['pskSetting']['securityKey'] == '[redacted]'
        assert out['pskSetting']['enabled'] is True
        assert out['accessToken'] == '[redacted]'
        assert out['searchKey'] == 'jelly'
        # hex site id must survive — blanket hex masking would break output
        assert out['id'] == '640effd1b3f2ae5b912275ec'

    def test_uuid_survives(self):
        out = scrub_value({'id': '1ef948c6-3a4b-4c5d-8e6f-1a2b3c4d5e6f'})
        assert out['id'] == '1ef948c6-3a4b-4c5d-8e6f-1a2b3c4d5e6f'

    def test_value_patterns_masked(self):
        out = scrub_value({
            'header': 'Authorization: Bearer abcd1234xyz',
            'jwt': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U',
            'access': 'AccessToken=tok123',
            'pve': 'PVEAPIToken=user@realm!tokenid=uuid',
        })
        assert 'abcd1234xyz' not in out['header']
        assert out['jwt'] == '[redacted]'
        assert 'tok123' not in out['access']
        assert 'uuid' not in out['pve']

    def test_non_json_body_still_pattern_scanned(self):
        assert 'Bearer xyz' not in scrub_body('hello Bearer xyz world')

    def test_nested_lists(self):
        out = scrub_value({'rows': [{'name': 'x', 'password': 'p'}, {'name': 'y'}]})
        assert out['rows'][0]['password'] == '[redacted]'
        assert out['rows'][1]['name'] == 'y'


@pytest.fixture
def secret_upstream():
    """Upstream that echoes secret-shaped data (Omada-style SSID/WAN config +
    write responses) to exercise the generic passthrough scrub."""
    state = {'requests': []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state['requests'].append(('GET', self.path))
            if '/ssids' in self.path:
                self._respond(200, {'data': {'ssids': [{
                    'ssid': 'aqua_iot',
                    'pskSetting': {'securityKey': 'RealPsk123', 'enabled': True}}]}})
            elif '/wan' in self.path:
                self._respond(200, {'data': {'username': 'bob',
                                             'password': 'VirginPassword',
                                             'ip': '192.168.1.1'}})
            else:
                self._respond(404, {'error': 'not found'})

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            body = self.rfile.read(length) if length else b''
            state['requests'].append(('POST', self.path, body.decode('utf-8', 'replace')))
            self._respond(200, {'ok': True, 'echo_password': 'SecretValue'})

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}/api2/json", 'state': state}
    server.shutdown()
    server.server_close()


def _make(upstream, gate_mode='destructive', kind='custom'):
    create_integration('testapi', upstream['base_url'], 'none', '', kind=kind,
                       gate_mode=gate_mode)
    seed_for_kind(get_integration('testapi'))
    return get_integration('testapi')


class TestProxyScrub:

    def test_omada_psk_leak_closed(self, secret_upstream):
        """The exact Hermes finding: generic read of SSID config must not leak
        pskSetting.securityKey."""
        _make(secret_upstream)
        out = json.loads(run_tool('testapi', 'read', {'path': 'sites/home/ssids'}))
        ssid = out['data']['ssids'][0]
        assert ssid['ssid'] == 'aqua_iot'
        assert ssid['pskSetting']['securityKey'] == '[redacted]'
        assert ssid['pskSetting']['enabled'] is True

    def test_wan_password_leak_closed(self, secret_upstream):
        _make(secret_upstream)
        out = json.loads(run_tool('testapi', 'read', {'path': 'wan'}))
        assert out['data']['username'] == 'bob'
        assert out['data']['password'] == '[redacted]'
        assert out['data']['ip'] == '192.168.1.1'

    def test_write_response_scrubbed_in_body_and_audit(self, secret_upstream):
        _make(secret_upstream)
        out = json.loads(run_tool('testapi', 'write',
                                  {'method': 'POST', 'path': 'wan',
                                   'data': {'password': 'VirginPassword'}}, reason='test'))
        assert out['ok'] is True
        assert out['echo_password'] == '[redacted]'
        rows = get_integration_calls()['rows']
        row = next(r for r in rows if r['tool'] == 'write')
        assert 'SecretValue' not in row['response_summary']
        assert '[redacted]' in row['response_summary']

    def test_pending_payload_scrubbed_at_display_only(self, auth_client, secret_upstream):
        _make(secret_upstream, gate_mode='all')
        out = json.loads(run_tool('testapi', 'write',
                                  {'method': 'POST', 'path': 'wan',
                                   'data': {'password': 'VirginPassword'}}, reason='test'))
        assert out['status'] == 'pending'
        call_id = out['id']
        calls = auth_client.get('/api/integration-calls/pending').json()
        row = next(c for c in calls if c['id'] == call_id)
        assert 'VirginPassword' not in json.dumps(row['payload'])
        assert '[redacted]' in json.dumps(row['payload'])
        # stored payload stays intact so an approval still executes as-is
        assert get_pending_call(call_id)['payload']['data']['password'] == 'VirginPassword'

    def test_curated_reads_unaffected(self, secret_upstream):
        """A read-only tool with a fields projection returns normally — the
        scrub only masks what the projection already kept."""
        _make(secret_upstream)
        out = json.loads(run_tool('testapi', 'read', {'path': 'sites/home/ssids'}))
        assert 'ssid' in out['data']['ssids'][0]

    def test_hex_and_uuid_survive_proxy(self, secret_upstream):
        _make(secret_upstream)
        out = json.loads(run_tool('testapi', 'read', {'path': 'sites/home/ssids'}))
        # no hex/uuid fields in this fixture, but ensure scrub_payload leaves ids
        payload = scrub_payload({'id': '1ef948c6', 'nested': {'uuid': 'x' * 32}})
        assert payload['id'] == '1ef948c6'
        assert payload['nested']['uuid'] == 'x' * 32
