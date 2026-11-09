"""Chunk F14 — inventory ("where is X?").

Synthetic data only: `192.0.2.x`, `web01`, `*.example.test`. The source calls
are stubbed at `core.inventory._call_tool`, so no upstream is touched; the
adapters, the join, the refresh/stale rules and the two caps are all exercised
against the cache they write.
"""

import json
import os
import shutil
import subprocess

import pytest

from core.inventory import (
    DEFAULT_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES,
    SourceError, build_entities, interval_minutes, refresh_inventory,
    set_interval_minutes, stale_sources, whereis,
)
from core.pihole_seed import seed_pihole_tools
from core.read_policy import is_read_integration_call
from core.tool_handlers import run_handler
from db.gateways import register_gateway
from db.integrations import create_integration, get_integration, get_tool
from db.inventory import get_inventory_items, replace_source_items

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, 'fixtures', 'f14_harness.js')
NODE = shutil.which('node')

FIXTURES = {
    'get_cluster_resources': [
        {'type': 'lxc', 'vmid': 103, 'node': 'pve01', 'name': 'web01',
         'status': 'running'},
        {'type': 'node', 'node': 'pve01', 'name': 'pve01', 'status': 'online'},
    ],
    'proxy_hosts': [
        {'id': 1, 'domain_names': ['app.example.test'],
         'forward_host': '192.0.2.10', 'forward_port': 8080,
         'forward_scheme': 'http', 'enabled': True, 'nginx_online': True},
    ],
    'local_dns_records': {
        'hosts': [{'name': 'web01.example.test', 'ips': ['192.0.2.10']}],
        'cnames': [{'name': 'app.example.test',
                    'target': 'web01.example.test'}],
    },
    'list_sites': [{'id': 'site1', 'name': 'home'}],
    'list_known_clients': {
        'rows': [{'mac': 'AA:BB:CC:DD:EE:01', 'name': 'web01',
                  'ip': '192.0.2.10', 'active': True, 'lastSeen': 1700000000}],
    },
}


def _make_integrations():
    create_integration('pve', 'https://192.0.2.50:8006/api2/json', 'token', 'x',
                       kind='proxmox')
    create_integration('npm', 'https://192.0.2.51/api', 'session', 'x', kind='npm')
    create_integration('pihole', 'http://192.0.2.52/api', 'query_token', 'x',
                       kind='pihole')
    create_integration('omada', 'https://192.0.2.53:8043', 'token', 'x',
                       kind='omada')
    register_gateway('192.0.2.10', 'web01', 'v15.3')


def _patch(monkeypatch, fixtures=None, fail=None):
    calls = []

    def fake(integration, tool_name, args):
        calls.append((integration['name'], tool_name))
        if fail and tool_name in fail:
            raise SourceError(fail[tool_name])
        data = (fixtures or FIXTURES).get(tool_name)
        if data is None:
            raise SourceError('tool_missing:%s' % tool_name)
        return data

    monkeypatch.setattr('core.inventory._call_tool', fake)
    return calls


@pytest.fixture
def sources(monkeypatch):
    _make_integrations()
    return _patch(monkeypatch)


class TestRefreshAndJoin:

    def test_f14_one_entity_across_all_sources(self, sources):
        refresh_inventory()
        entities = build_entities()
        assert len(entities) == 1, entities
        entity = entities[0]
        assert entity['name'] == 'web01'
        assert entity['ips'] == ['192.0.2.10']
        assert 'web01.example.test' in entity['fqdns']
        assert 'app.example.test' in entity['fqdns']
        assert entity['vmids'] == ['103']
        assert {row['source'] for row in entity['sources']} == {
            'eshu', 'pve', 'npm', 'pihole', 'omada'}
        assert entity['stale'] is False

    def test_f14_sources_are_read_only(self, sources):
        calls = sources
        refresh_inventory()
        assert {name for _integration, name in calls} >= {
            'get_cluster_resources', 'proxy_hosts', 'local_dns_records',
            'list_sites', 'list_known_clients'}

    def test_f14_a_conflict_is_shown_not_resolved(self):
        items = [
            {'source': 'pihole', 'kind': 'dns', 'key': 'a',
             'name': 'nas.example.test', 'ips': ['192.0.2.20'],
             'fqdns': ['nas.example.test'], 'extra': {}},
            {'source': 'omada', 'kind': 'client', 'key': 'b',
             'name': 'printer', 'ips': ['192.0.2.20'], 'fqdns': [],
             'extra': {}},
        ]
        entities = build_entities(items, stale={})
        assert len(entities) == 1
        conflict = entities[0]['conflicts'][0]
        assert conflict['ip'] == '192.0.2.20'
        assert set(conflict['names']) == {'nas.example.test', 'printer'}

    def test_f14_a_failed_source_keeps_its_rows_and_is_stale(self, monkeypatch):
        _make_integrations()
        _patch(monkeypatch)
        refresh_inventory()
        before = [i for i in get_inventory_items() if i['source'] == 'npm']
        assert before
        _patch(monkeypatch, fail={'proxy_hosts': 'npm is down'})
        refresh_inventory()
        after = [i for i in get_inventory_items() if i['source'] == 'npm']
        assert len(after) == len(before)
        states = stale_sources()
        assert states['npm']['stale'] is True
        assert 'npm is down' in states['npm']['error']
        assert states['pihole']['stale'] is False

    def test_f14_eshu_only_needs_no_integrations(self):
        register_gateway('192.0.2.11', 'web02', 'v15.3')
        refresh_inventory()
        entities = build_entities()
        assert any(e['name'] == 'web02' and e['ips'] == ['192.0.2.11']
                   for e in entities)
        assert stale_sources()['eshu']['stale'] is False


class TestWhereis:

    def test_f14_whereis_caps_five_entities(self):
        items = [{'source': 'test', 'kind': 'client', 'key': str(i),
                  'name': 'web%d' % i, 'ips': ['192.0.2.%d' % (10 + i)],
                  'fqdns': [], 'extra': {}} for i in range(8)]
        replace_source_items('test', items)
        result = whereis('web')
        assert result['query'] == 'web'
        assert result['count'] == 5
        for entity in result['entities']:
            assert len(json.dumps(entity, separators=(',', ':'))) <= 600

    def test_f14_whereis_trims_and_flags_truncated(self):
        long_fqdns = ['a%02d.example.test' % i for i in range(60)]
        items = [{'source': 'test', 'kind': 'proxy', 'key': '1',
                  'name': 'big.example.test', 'ips': [], 'fqdns': long_fqdns,
                  'url': 'http://192.0.2.10:8080', 'extra': {}}]
        replace_source_items('test', items)
        entity = whereis('big')['entities'][0]
        assert entity['truncated'] is True
        assert len(json.dumps(entity, separators=(',', ':'))) <= 600

    def test_f14_whereis_matches_ip_vmid_and_mac(self):
        replace_source_items('test', [
            {'source': 'test', 'kind': 'guest', 'key': '1', 'name': 'guest-a',
             'ips': ['192.0.2.30'], 'fqdns': [], 'vmid': '103',
             'extra': {'mac': 'AA:BB:CC:DD:EE:02'}}])
        for query in ('192.0.2.30', '103', 'aa:bb:cc:dd:ee:02', 'guest-a'):
            result = whereis(query)
            assert result['count'] == 1, query
            assert result['entities'][0]['name'] == 'guest-a', query

    def test_f14_whereis_with_no_match(self):
        assert whereis('nothing-here')['count'] == 0


class TestSettings:

    def test_f14_interval_defaults_and_clamps(self):
        assert interval_minutes() == DEFAULT_INTERVAL_MINUTES
        assert set_interval_minutes(1) == MIN_INTERVAL_MINUTES
        assert interval_minutes() == MIN_INTERVAL_MINUTES
        assert set_interval_minutes(9999) == MAX_INTERVAL_MINUTES
        assert set_interval_minutes(30) == 30
        assert interval_minutes() == 30

    def test_f14_bad_interval_falls_back_to_default(self):
        assert set_interval_minutes('nope') == DEFAULT_INTERVAL_MINUTES


class TestLocalDnsRecords:

    def test_f14_local_dns_records_passes_the_read_check(self):
        create_integration('pihole2', 'http://192.0.2.60/api', 'query_token',
                           'tok', kind='pihole')
        integration = get_integration('pihole2')
        seed_pihole_tools(integration['id'])
        tool = get_tool('pihole2', 'local_dns_records')
        assert tool is not None
        assert tool['read_only'] == 1
        assert is_read_integration_call(
            tool, tool.get('transport') or 'http', {}) == (True, '')

    def test_f14_local_dns_records_parses_v6_shapes(self):
        create_integration('pihole3', 'http://192.0.2.61/api', 'query_token',
                           'tok', kind='pihole')
        integration = get_integration('pihole3')
        seed_pihole_tools(integration['id'])
        tool = get_tool('pihole3', 'local_dns_records')

        bodies = {
            'config/dns/hosts': {'config': {'dns': {
                'hosts': ['192.0.2.10 web01.example.test',
                          '192.0.2.11 web02.example.test']}}},
            'config/dns/cnameRecords': {'config': {'dns': {
                'cnameRecords': ['app.example.test,web01.example.test']}}},
        }

        def fake(integration, probe, args, **kwargs):
            path = probe['path_template']
            return {'status_code': 200, 'body': json.dumps(bodies[path]),
                    'error': None, 'truncated': 0, 'latency_ms': 0}

        import core.tool_handlers as handlers
        original = handlers.execute_integration_call
        handlers.execute_integration_call = fake
        try:
            out = json.loads(run_handler('pihole_local_dns_records',
                                         integration, tool, {}))
        finally:
            handlers.execute_integration_call = original
        assert out['hosts'] == [
            {'name': 'web01.example.test', 'ips': ['192.0.2.10']},
            {'name': 'web02.example.test', 'ips': ['192.0.2.11']},
        ]
        assert out['cnames'] == [
            {'name': 'app.example.test', 'target': 'web01.example.test'}]

    def test_f14_local_dns_records_upstream_error_is_reported(self):
        create_integration('pihole4', 'http://192.0.2.62/api', 'query_token',
                           'tok', kind='pihole')
        integration = get_integration('pihole4')
        seed_pihole_tools(integration['id'])
        tool = get_tool('pihole4', 'local_dns_records')

        import core.tool_handlers as handlers
        original = handlers.execute_integration_call
        handlers.execute_integration_call = lambda *a, **k: {
            'status_code': 404, 'body': '', 'error': 'not_found',
            'truncated': 0, 'latency_ms': 0}
        try:
            out = json.loads(run_handler('pihole_local_dns_records',
                                         integration, tool, {}))
        finally:
            handlers.execute_integration_call = original
        assert out['error'] == 'local_dns_records_failed'


class TestApi:

    def test_f14_inventory_endpoints(self, auth_client, monkeypatch):
        _make_integrations()
        _patch(monkeypatch)
        assert auth_client.get('/api/inventory').status_code == 200
        refreshed = auth_client.post('/api/inventory/refresh').json()
        assert refreshed['status'] == 'ok' and refreshed['sources'] >= 1
        snapshot = auth_client.get('/api/inventory').json()
        assert snapshot['items']
        assert snapshot['interval_minutes'] == DEFAULT_INTERVAL_MINUTES
        assert snapshot['refreshed_at'] > 0
        found = auth_client.get('/api/inventory?q=web01').json()
        assert found['items'] and found['items'][0]['name'] == 'web01'
        saved = auth_client.post('/api/inventory/settings',
                                 json={'interval_minutes': 7}).json()
        assert saved['interval_minutes'] == 7
        clamped = auth_client.post('/api/inventory/settings',
                                   json={'interval_minutes': 1}).json()
        assert clamped['interval_minutes'] == MIN_INTERVAL_MINUTES

    def test_f14_inventory_requires_a_session(self, client):
        assert client.get('/api/inventory').status_code in (401, 403)
        assert client.post('/api/inventory/refresh').status_code in (401, 403)


@pytest.mark.skipif(NODE is None, reason='node is not installed')
class TestUi:

    def _run(self, payload):
        proc = subprocess.run([NODE, HARNESS, json.dumps({'payload': payload})],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert 'error' not in out, out['error']
        return out

    def test_f14_inventory_escapes_source_and_entity_text(self):
        payload = '<img src=x onerror=alert(1)>'
        out = self._run(payload)
        for key in ('chips', 'entity'):
            assert payload not in out[key], key
            assert '&lt;img' in out[key], key

    def test_f14_inventory_marks_stale_sources_and_conflicts(self):
        out = self._run('192.0.2.20')
        assert 'stale' in out['chips']
        assert 'is claimed by' in out['entity']
        assert out['empty'].count('inv-entity') == 1
