"""Pulse MCP integration tests.

Covers the curated `pulse_*` seed catalog: compact response transforms,
search/limit shaping, gating of write tools, error envelopes, v6 stubs, and
credential redaction in the audit/UI paths. The upstream mock mirrors Pulse
v6.3.2 (metrics.{cpu,memory,disk}, data[] envelope, no /backups endpoint).
"""
import http.server
import json
import threading

import pytest

from db.integrations import create_integration, get_integration, get_tools, get_pending_call
from core.seeds import seed_for_kind
from core.tool_runner import run_tool


@pytest.fixture
def pulse_upstream():
    """Threaded upstream mimicking Pulse v5 (X-API-Token auth). Records every
    request (method, path, auth header, body) for assertions."""
    state = {'requests': []}

    def _resource(res_id):
        return {
            'id': res_id, 'name': 'CloudFlare' if res_id.endswith('104') else 'Test-VM',
            'type': 'system-container' if res_id.endswith('104') else 'vm', 'status': 'stopped',
            'metrics': {
                'cpu': {'value': 3.5, 'percent': 3.5, 'unit': 'percent'},
                'memory': {'value': 225485783, 'used': 225485783, 'total': 536870912,
                           'percent': 42, 'unit': 'bytes'},
                'disk': {'value': 2147483648, 'used': 2147483648, 'total': 8589934592,
                         'percent': 25, 'unit': 'bytes'},
                'netIn': {'value': 1024, 'unit': 'bytes/s'},
                'netOut': {'value': 2048, 'unit': 'bytes/s'},
            },
            'identity': {'hostnames': ['CloudFlare'], 'ipAddresses': ['192.168.1.240']},
            'canonicalIdentity': {'displayName': 'CloudFlare', 'hostname': 'CloudFlare'},
            'proxmox': {'sourceId': res_id, 'nodeName': 'pve3', 'clusterName': 'prox-cluster',
                        'instance': 'pve', 'vmid': 104 if res_id.endswith('104') else 200,
                        'cpus': 2, 'osName': 'Ubuntu', 'uptime': 0,
                        'lastBackup': '2026-08-20T01:30:30Z'},
            'alerts': [{'id': 'guest-powered-off-' + res_id, 'type': 'powered-off',
                        'level': 'warning', 'message': "Container is powered off"}],
        }

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _path(self):
            from urllib.parse import urlparse, unquote
            return unquote(urlparse(self.path).path)

        def _record(self, method, body=b''):
            state['requests'].append({
                'method': method, 'path': self._path(), 'full_path': self.path,
                'api_token': self.headers.get('X-API-Token', ''),
                'body': body.decode('utf-8', 'replace'),
            })

        def _respond(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self._path()
            self._record('GET')
            if path == '/api/health':
                self._respond(200, {'status': 'healthy', 'timestamp': 1787234977,
                                    'uptime': 3171279.7, 'proxyInstallScriptAvailable': True})
            elif path == '/api/version':
                self._respond(200, {'version': '6.3.2', 'channel': 'stable',
                                    'deploymentType': 'proxmoxve', 'containerized': True,
                                    'updateAvailable': False})
            elif path == '/api/resources':
                resources = [_resource('pve:pve3:104'), _resource('pve:pve3:200')]
                resources.append({'id': 'prox-cluster-cluster-pbs_backups', 'type': 'storage',
                                  'name': 'pbs_backups', 'status': 'online',
                                  'metrics': {'disk': {'value': 1099511627776,
                                                       'used': 1099511627776,
                                                       'total': 4398046511104,
                                                       'percent': 25, 'unit': 'bytes'}}})
                self._respond(200, {'data': resources,
                                    'aggregations': {'totalResources': 3, 'byType': {'system-container': 1, 'vm': 1, 'storage': 1}},
                                    'meta': {}})
            elif path.startswith('/api/resources/'):
                res_id = path.rsplit('/', 1)[-1]
                if res_id in ('pve:pve3:104', 'pve:pve3:200'):
                    self._respond(200, _resource(res_id))
                else:
                    self._respond(404, {'error': 'resource not found', 'code': 'resource_not_found'})
            elif path == '/api/alerts/active':
                self._respond(200, [
                    {'id': 'pve:pve:200-memory', 'type': 'memory', 'level': 'warning',
                     'resourceId': 'pve:pve:200', 'resourceName': 'TrueNas-Scale', 'node': 'pve',
                     'message': 'VM memory at 90.8%', 'value': 92.85, 'threshold': 85,
                     'startTime': '2026-08-02T12:07:00.622Z', 'lastSeen': '2026-08-20T14:09:29.864Z',
                     'acknowledged': True, 'ackUser': 'admin'},
                    {'id': 'pve:pve3:104-off', 'type': 'powered-off', 'level': 'critical',
                     'resourceName': 'CloudFlare', 'node': 'pve3',
                     'message': "Container 'CloudFlare' is powered off", 'value': 0,
                     'threshold': 0, 'acknowledged': False},
                ])
            elif path == '/api/alerts/history':
                self._respond(200, [
                    {'id': 'h1', 'type': 'cpu', 'level': 'warning', 'resourceName': 'VM-A',
                     'message': 'CPU high', 'value': 95.0, 'threshold': 90,
                     'acknowledged': True, 'metadata': {'hostUUID': 'x'}},
                    {'id': 'h2', 'type': 'disk', 'level': 'critical', 'resourceName': 'VM-B',
                     'message': 'Disk full', 'value': 99.0, 'threshold': 95, 'acknowledged': False},
                ])
            elif path == '/api/charts':
                n_points = state.get('charts_points', 1000)
                n_resources = state.get('charts_resources', 1)
                series = {'cpu': {'points': [{'t': 1700000000000 + i * 1000, 'v': i % 100} for i in range(n_points)]}}
                data = {}
                for r in range(n_resources):
                    data[f'pve:pve3:{104 + r}'] = dict(series)
                self._respond(200, {'data': data, 'hostData': {},
                                    'nodeData': {}, 'stats': {}, 'timestamp': 1700000000000})
            elif path == '/api/config/nodes':
                self._respond(200, [{
                    'id': 'pve-0', 'type': 'pve', 'name': 'pve', 'host': 'https://192.168.1.215:8006',
                    'hasPassword': True, 'tokenName': 'pulse-monitor@pam!x', 'hasToken': True,
                    'status': 'connected', 'isCluster': True, 'clusterName': 'prox-cluster',
                    'clusterEndpoints': [{'NodeID': 'node/pve', 'NodeName': 'pve',
                                          'Host': 'https://pve:8006', 'IP': '192.168.1.215',
                                          'Online': True, 'PulseReachable': True, 'PulseError': ''}],
                }])
            elif path == '/api/state':
                self._respond(200, {'connectionHealth': {'host-089e143c-77de-470a-bc05-b018397120e2': True,
                                                         'pve': True, 'pbs-proxmox-backup-server': False},
                                    'resources': [], 'stats': {}})
            elif path == '/api/discover':
                self._respond(403, {'error': 'missing_scope', 'requiredScope': 'settings:write'})
            elif path == '/api/storage/':
                self._respond(400, {'error': 'Storage ID is required', 'code': 'missing_storage_id'})
            else:
                self._respond(404, {'error': 'not found'})

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            body = self.rfile.read(length) if length else b''
            self._record('POST', body)
            self._respond(200, {'ok': True})

        def do_PUT(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            body = self.rfile.read(length) if length else b''
            self._record('PUT', body)
            self._respond(200, {'ok': True})

        def do_DELETE(self):
            self._record('DELETE')
            self._respond(200, {'ok': True})

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'base_url': f"http://127.0.0.1:{server.server_address[1]}/api", 'state': state}
    server.shutdown()
    server.server_close()


def _make_pulse(upstream, gate_mode='destructive'):
    create_integration(
        "pulse", upstream['base_url'], "header", "pulse-secret-token",
        auth_header_name="X-API-Token", kind="pulse", gate_mode=gate_mode)
    integration = get_integration("pulse")
    seed_for_kind(integration)
    return integration


def _tool(name):
    integration = get_integration("pulse")
    return next(t for t in get_tools(integration['id']) if t['name'] == name)


class TestPulseReads:

    def test_health(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'health', {}))
        assert out['status'] == 'healthy'
        assert out['version'] == '6.3.2'
        assert out['channel'] == 'stable'
        assert out['deploymentType'] == 'proxmoxve'
        assert 'proxyInstallScriptAvailable' not in out
        assert pulse_upstream['state']['requests'][0]['api_token'] == 'pulse-secret-token'

    def test_fleet_summary_compact(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'fleet_summary', {}))
        assert out['count'] == 3
        r = next(x for x in out['resources'] if x['id'] == 'pve:pve3:104')
        assert r['name'] == 'CloudFlare'
        assert r['cpuPct'] == 3.5
        assert r['memPct'] == 42
        assert r['memTotal'] == 536870912
        assert r['memFree'] == 536870912 - 225485783
        assert r['diskPct'] == 25
        assert r['diskFree'] == 8589934592 - 2147483648
        assert r['ip'] == ['192.168.1.240']
        assert r['alerts'][0]['level'] == 'warning'
        assert 'proxmox' not in r
        assert 'aggregations' not in out

    def test_fleet_summary_search_and_limit(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'fleet_summary', {'search': 'cloud', 'limit': 5}))
        assert len(out['resources']) == 1
        assert out['resources'][0]['name'] == 'CloudFlare'
        out = json.loads(run_tool('pulse', 'fleet_summary', {'limit': 2}))
        assert len(out['resources']) == 2

    def test_fleet_summary_full(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'fleet_summary', {'full': True}))
        assert 'aggregations' in out
        assert out['aggregations']['totalResources'] == 3
        r = next(x for x in out['resources'] if x['id'] == 'pve:pve3:104')
        assert r['osName'] == 'Ubuntu'
        assert r['node'] == 'pve3'
        assert r['trafficIn'] == 1024

    def test_get_resource(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_resource', {'resourceId': 'pve:pve3:104'}))
        assert out['id'] == 'pve:pve3:104'
        assert out['cpuPct'] == 3.5
        assert out['memUsed'] == 225485783

    def test_get_resource_not_found(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_resource', {'resourceId': 'nope'}))
        assert out['error'] and 'resource_not_found' in out['error']

    def test_list_alerts_active(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_alerts', {}))
        assert len(out) == 2
        a = out[0]
        assert a['id'] == 'pve:pve:200-memory'
        assert a['level'] == 'warning'
        assert a['resourceName'] == 'TrueNas-Scale'
        assert 'ackUser' not in a

    def test_list_alerts_search(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_alerts', {'search': 'powered off', 'limit': 10}))
        assert len(out) == 1
        assert out[0]['id'] == 'pve:pve3:104-off'

    def test_list_alerts_search_matches_id(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_alerts', {'search': 'pve:pve3:104-off'}))
        assert len(out) == 1
        assert out[0]['id'] == 'pve:pve3:104-off'
        out = json.loads(run_tool('pulse', 'list_alerts', {'search': '1ef948c6'}))
        assert out == []

    def test_list_alerts_history(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_alerts', {'scope': 'history', 'limit': 10}))
        assert len(out) == 2
        assert out[0]['acknowledged'] is True
        req = pulse_upstream['state']['requests'][0]
        assert req['path'] == '/api/alerts/history'
        assert 'limit=10' in req['full_path']

    def test_get_charts_downsample(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_charts', {'range': '1h', 'resource': 'pve:pve3:104'}))
        pts = out['pve:pve3:104']['cpu']['points']
        assert len(pts) <= 200
        assert pts[0]['v'] is not None

    def test_get_charts_invalid_range(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_charts', {'range': '2h'}))
        assert out['error'] == 'invalid_range'

    def test_get_charts_metric_filter(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_charts', {'range': '24h', 'resource': 'pve:pve3:104', 'metric': 'cpu'}))
        assert list(out.keys()) == ['pve:pve3:104']
        assert list(out['pve:pve3:104'].keys()) == ['cpu']
        req = pulse_upstream['state']['requests'][0]
        assert 'range=24h' in req['full_path']
        assert 'maxPoints' not in req['full_path']

    def test_get_charts_all_resources(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        pulse_upstream['state']['charts_resources'] = 3
        out = json.loads(run_tool('pulse', 'get_charts', {'range': '7d'}))
        assert 'pve:pve3:104' in out and 'pve:pve3:105' in out and 'pve:pve3:106' in out
        assert out['pve:pve3:104']['cpu']['points']

    def test_get_charts_large_payload_not_truncated(self, pulse_upstream):
        """A charts payload >1 MB (the old proxy cap) must still be downsampled
        by the transform, never returned as a truncated raw blob."""
        pulse_upstream['state']['charts_points'] = 40000  # ~1.4 MB JSON
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'get_charts', {'range': '1h', 'resource': 'pve:pve3:104', 'metric': 'cpu', 'maxPoints': 10}))
        assert list(out.keys()) == ['pve:pve3:104']
        assert len(out['pve:pve3:104']['cpu']['points']) <= 10

    def test_list_storage(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_storage', {}))
        assert len(out) == 1
        s = out[0]
        assert s['id'] == 'prox-cluster-cluster-pbs_backups'
        assert s['total'] == 4398046511104
        assert s['free'] == 4398046511104 - 1099511627776
        assert s['pct'] == 25

    def test_list_nodes_compact(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_nodes', {}))
        assert len(out) == 1
        n = out[0]
        assert n['id'] == 'pve-0'
        assert n['status'] == 'connected'
        assert n['isCluster'] is True
        assert 'hasPassword' not in n
        assert 'tokenName' not in n
        assert 'endpoints' not in n

    def test_list_nodes_full_endpoints(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_nodes', {'full': True}))
        ep = out[0]['endpoints'][0]
        assert ep['node'] == 'pve'
        assert ep['ip'] == '192.168.1.215'
        assert ep['online'] is True
        assert ep['pulseReachable'] is True

    def test_connection_health_projects_only_map(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'connection_health', {}))
        assert out == {'connectionHealth': {'host-089e143c-77de-470a-bc05-b018397120e2': True,
                                            'pve': True, 'pbs-proxmox-backup-server': False}}
        assert pulse_upstream['state']['requests'][0]['path'] == '/api/state'


class TestPulseWrites:

    def test_acknowledge_alert_gated_under_all(self, pulse_upstream):
        _make_pulse(pulse_upstream, gate_mode='all')
        out = json.loads(run_tool('pulse', 'acknowledge_alert', {'id': 'h1'}, reason='test'))
        assert out['status'] == 'pending'

    def test_remove_node_gated_under_destructive(self, pulse_upstream):
        _make_pulse(pulse_upstream, gate_mode='destructive')
        out = json.loads(run_tool('pulse', 'remove_node', {'id': 'pve-0'}, reason='test'))
        assert out['status'] == 'pending'

    def test_acknowledge_alert_autoruns_under_destructive(self, pulse_upstream):
        """Documented posture: non-destructive writes auto-run (audited) under
        the default 'destructive' gate mode — the operator's chosen policy."""
        _make_pulse(pulse_upstream, gate_mode='destructive')
        out = json.loads(run_tool('pulse', 'acknowledge_alert', {'alertIdentifier': 'h1'}, reason='test'))
        assert out.get('status') != 'pending'
        assert out.get('ok') is True
        req = pulse_upstream['state']['requests'][0]
        assert req['method'] == 'POST'
        assert req['path'] == '/api/alerts/acknowledge'
        assert json.loads(req['body'])['alertIdentifier'] == 'h1'

    def test_bulk_acknowledge_array_body(self, pulse_upstream):
        _make_pulse(pulse_upstream, gate_mode='all')
        out = json.loads(run_tool('pulse', 'acknowledge_alerts_bulk', {'alertIdentifiers': ['h1', 'h2']}, reason='test'))
        assert out['status'] == 'pending'

    def test_stub_returns_not_implemented(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        out = json.loads(run_tool('pulse', 'list_findings', {}))
        assert out['error'] == 'not_implemented'

    def test_discover_surfaces_scope_error(self, pulse_upstream):
        """discover is gated under 'all'; once approved the upstream 403 body is
        normalized to a stable code: message error."""
        _make_pulse(pulse_upstream, gate_mode='all')
        out = json.loads(run_tool('pulse', 'discover', {}, reason='test'))
        assert out['status'] == 'pending'


class TestPulseRedactionAndApprove:

    def test_pending_payload_redacted_but_stored_intact(self, auth_client, pulse_upstream):
        _make_pulse(pulse_upstream, gate_mode='all')
        out = json.loads(run_tool('pulse', 'add_node',
                                  {'host': 'https://192.168.1.9:8006', 'password': 'sekret'},
                                  reason='add node'))
        assert out['status'] == 'pending'
        call_id = out['id']
        calls = auth_client.get('/api/integration-calls/pending').json()
        row = next(c for c in calls if c['id'] == call_id)
        assert row['payload'].get('password') == '[redacted]'
        assert 'sekret' not in json.dumps(row['payload'])
        # The stored payload is intact so an approval still executes with the
        # real credential.
        assert get_pending_call(call_id)['payload']['password'] == 'sekret'

    def test_approve_executes_and_history_redacted(self, auth_client, pulse_upstream):
        _make_pulse(pulse_upstream, gate_mode='all')
        out = json.loads(run_tool('pulse', 'add_node',
                                  {'host': 'https://192.168.1.9:8006', 'password': 'sekret'},
                                  reason='add node'))
        call_id = out['id']
        r = auth_client.post(f'/api/integration-calls/{call_id}/approve')
        assert r.status_code == 200
        req = next(x for x in pulse_upstream['state']['requests']
                   if x['method'] == 'POST' and x['path'] == '/api/config/nodes')
        assert 'sekret' in req['body']  # executed with the real credential
        reqs = auth_client.get('/api/requests').json()
        row = next(x for x in reqs if 'add_node' in x['command'])
        assert '[redacted]' in row['command']
        assert 'sekret' not in row['command']

    def test_seed_creates_full_catalog(self, pulse_upstream):
        _make_pulse(pulse_upstream)
        tools = get_tools(get_integration('pulse')['id'])
        names = {t['name'] for t in tools}
        assert {'health', 'fleet_summary', 'get_resource', 'list_alerts',
                'get_charts', 'list_storage', 'list_nodes',
                'connection_health'}.issubset(names)
        assert {'acknowledge_alert', 'acknowledge_alerts_bulk', 'add_node',
                'update_node', 'remove_node', 'test_node_connection',
                'discover'}.issubset(names)
        # backups was retired on v6 (no /backups endpoint upstream)
        assert 'list_backups' not in names
        assert 'list_findings' in names
        stubs = {t['name'] for t in tools if t.get('not_implemented')}
        assert {'plan_action', 'set_operator_state'}.issubset(stubs)
        # read + write generic floor is present too
        assert 'read' in names and 'write' in names

    def test_v6_stub_paths_match_manifest(self, pulse_upstream):
        """The v6 manifest declares real paths under /ai/patrol, /actions and
        /resources/{resourceId}/operator-state — the declared stubs must point
        there (not the old guessed paths), so a later implementation wires up
        without touching the catalog."""
        _make_pulse(pulse_upstream)
        tools = {t['name']: t for t in get_tools(get_integration('pulse')['id'])}
        assert tools['list_findings']['path_template'] == '/ai/patrol/findings'
        assert tools['ack_finding']['path_template'] == '/ai/patrol/acknowledge'
        assert tools['snooze_finding']['path_template'] == '/ai/patrol/snooze'
        assert tools['dismiss_finding']['path_template'] == '/ai/patrol/dismiss'
        assert tools['resolve_finding']['path_template'] == '/ai/patrol/resolve'
        assert tools['decide_action']['path_template'] == '/actions/{actionId}/decision'
        assert tools['execute_action']['path_template'] == '/actions/{actionId}/execute'
        assert tools['get_operator_state']['path_template'] == '/resources/{resourceId}/operator-state'
        assert tools['set_operator_state']['path_template'] == '/resources/{resourceId}/operator-state'
        assert tools['set_operator_state']['method'] == 'PUT'
        assert tools['clear_operator_state']['method'] == 'DELETE'
        # operator-state + action tools carry their path-param in the schema
        op = {p['name'] for p in (tools['get_operator_state'].get('params') or [])}
        assert 'resourceId' in op
        act = {p['name'] for p in (tools['decide_action'].get('params') or [])}
        assert 'actionId' in act

    def test_test_endpoint_skips_stubs(self, auth_client, pulse_upstream):
        """The Test button must not pick a not_implemented stub (its placeholder
        path 404s upstream) — it should land on a real read-only tool."""
        _make_pulse(pulse_upstream)
        r = auth_client.post('/api/integrations/pulse/test')
        assert r.status_code == 200
        body = r.json()
        assert body['status_code'] == 200
        assert body['tool'] != 'ack_finding'
        assert body['tool'] in {'connection_health', 'health'}
