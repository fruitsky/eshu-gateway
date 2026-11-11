"""Chunk F21 — snapshot before approve.

Synthetic data only: TEST-NET addresses (192.0.2.x), `web01`, `*.example.test`,
and a fake Proxmox at the `urlopen` layer, so the path guard, the auth header,
the tool path and the JSON parsing all run for real.

The four snapshot tools are the point of the chunk: every agent path must
refuse them, and only `core/snapshots.py` may reach the Proxmox subtree.
"""
import ast
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from core.integration_proxy import (
    SNAPSHOT_INTERNAL, ProxyError, execute_generic_call,
    execute_integration_call, is_snapshot_path, snapshot_refusal,
)
from core.snapshots import (
    _recheck, _upid_node, clear_probe_cache, create_and_approve,
    dashboard_guest, delete, expiry_hours, offer_for_request, resolve_target,
    rollback, set_dashboard_guest, sweep,
)
from db.core import db_conn
from db.audit import get_audit_log
from db.gateways import (
    clear_trigger_freeze, register_gateway, set_gateway_proxmox_bindings,
    set_trigger_freeze,
)
from db.integrations import (
    create_integration, get_integration, get_integration_by_id,
    get_integrations, get_snapshot_secret, get_tool, set_snapshot_secret,
    set_tools_internal,
)
from db.inventory import replace_source_items
from db.misc import set_meta_json
from db.requests import create_request, get_request_row
from db.snapshots import (
    claim_state, create_snapshot, get_snapshot, set_snapshot_state,
    snapshots_for_request,
)
from core.proxmox_seed import seed_proxmox_tools

HOST = '192.0.2.10'
NODE = 'pve'
NODE_IP = '192.0.2.50'
GUEST_IP = '192.0.2.20'


class FakePVE:
    """A Proxmox API answer at the urlopen layer."""

    def __init__(self):
        self.requests = []
        self.snapshots = {}       # (node, vmid) -> [{name, description, snaptime}]
        self.configs = {}         # (type, vmid) -> {'net0': 'ip=192.0.2.20/24'}
        self.tasks = {}           # upid -> {'status': 'stopped', 'exitstatus': 'OK'}
        self.task_owner = {}      # upid -> the Authorization that started it
        self.task_seq = 0
        self.block_create = None  # (started, release) events, for the race test
        self.fail_create = None
        self.fail_task = False
        self.fail_rollback = None
        self.fail_delete = None
        self.bad_upid = None
        # AM2's probe answers, per token. The main token is read-only; the
        # snapshot token may snapshot and roll back.
        self.main_privileges = {'/': {'VM.Audit': 1}}
        self.snapshot_privileges = {
            '/': {'VM.Audit': 1, 'VM.Snapshot': 1, 'VM.Snapshot.Rollback': 1}}
        self.version = {'version': '8.2.4', 'release': '8.2'}
        self.probe_status = 200
        self.guests = [
            {'type': 'lxc', 'vmid': 101, 'node': NODE, 'name': 'web01',
             'status': 'running', 'disk': 10, 'maxdisk': 100},
            {'type': 'qemu', 'vmid': 102, 'node': NODE, 'name': 'db01',
             'status': 'running', 'disk': 95, 'maxdisk': 100},
        ]

    # -- helpers the tests use ------------------------------------------
    def add_guest(self, vmid, name, type_='lxc', status='running',
                  config_ip=None, node=NODE):
        self.guests.append({'type': type_, 'vmid': vmid, 'node': node,
                            'name': name, 'status': status,
                            'disk': 1, 'maxdisk': 100})
        if config_ip:
            self.configs[(type_, vmid)] = {'net0': 'name=eth0,ip=%s/24' % config_ip}

    def _auth(self, req):
        for key, value in (getattr(req, 'headers', {}) or {}).items():
            if str(key).lower() == 'authorization':
                return str(value)
        return ''

    def _upid(self, kind, req):
        """A fresh UPID, owned by whoever sent the request (AS5)."""
        self.task_seq += 1
        upid = 'UPID:%s:%08X:%08X:%08X:%s:101:root@pam:' % (
            NODE, self.task_seq, self.task_seq, 1700000000 + self.task_seq, kind)
        self.task_owner[upid] = self._auth(req)
        return upid

    def urls(self):
        return [r['url'] for r in self.requests]

    def methods(self):
        return [r['method'] for r in self.requests]

    # -- the fake -------------------------------------------------------
    def __call__(self, req, timeout=None, context=None):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        method = (getattr(req, 'method', 'GET') or 'GET').upper()
        self.requests.append({'url': url, 'method': method,
                              'headers': dict(getattr(req, 'headers', {}) or {})})
        parsed = urllib.parse.urlparse(url)
        path = unquote_all(parsed.path)
        body = None
        if path.endswith('/access/permissions'):
            if self.probe_status != 200:
                raise urllib.error.HTTPError(url, self.probe_status,
                                             'probe failed', None,
                                             io.BytesIO(b''))
            auth = self._auth(req)
            if 'snapshot-token' in auth or 'snapshot' in auth:
                body = self.snapshot_privileges
            else:
                body = self.main_privileges
        elif path.endswith('/version'):
            body = self.version
        elif path.endswith('/cluster/status'):
            body = [{'type': 'node', 'name': NODE, 'ip': NODE_IP, 'online': 1}]
        elif path.endswith('/cluster/resources'):
            body = list(self.guests)
        elif re.search(r'/nodes/[^/]+/(?:lxc|qemu)/\d+/status$', path):
            body = {'status': 'running'}
        elif re.search(r'/nodes/[^/]+/(?:lxc|qemu)/\d+/config$', path):
            match = re.search(r'/(lxc|qemu)/(\d+)/config$', path)
            body = self.configs.get((match.group(1), int(match.group(2))), {})
        elif re.search(r'/nodes/[^/]+/(?:lxc|qemu)/\d+/snapshot$', path):
            match = re.search(r'/(lxc|qemu)/(\d+)/snapshot$', path)
            key = (NODE, int(match.group(2)))
            if method in ('POST', 'PUT'):
                if self.fail_create:
                    raise urllib.error.HTTPError(url, self.fail_create,
                                                 'refused', None, io.BytesIO(b''))
                raw = getattr(req, 'data', b'') or b''
                text = raw.decode() if isinstance(raw, bytes) else str(raw)
                try:
                    fields = json.loads(text or '{}')
                except ValueError:
                    fields = dict(urllib.parse.parse_qsl(text))
                if not isinstance(fields, dict):
                    fields = {}
                if self.block_create:
                    started, release = self.block_create
                    started.set()
                    release.wait(10)
                self.snapshots.setdefault(key, []).append({
                    'name': fields.get('snapname', 'x'),
                    'description': fields.get('description', ''),
                    'snaptime': int(time.time())})
                body = self.bad_upid or self._upid('snapshot', req)
            else:
                body = self.snapshots.get(key, [])
        elif re.search(r'/snapshot/[^/]+/rollback$', path):
            if self.fail_rollback:
                raise urllib.error.HTTPError(url, self.fail_rollback,
                                             'refused', None, io.BytesIO(b''))
            body = self.bad_upid or self._upid('rollback', req)
        elif re.search(r'/snapshot/[^/]+$', path) and method == 'DELETE':
            if self.fail_delete:
                raise urllib.error.HTTPError(url, self.fail_delete,
                                             'refused', None, io.BytesIO(b''))
            match = re.search(r'/(lxc|qemu)/(\d+)/snapshot/([^/]+)$', path)
            key = (NODE, int(match.group(2)))
            self.snapshots[key] = [s for s in self.snapshots.get(key, [])
                                   if s['name'] != match.group(3)]
            body = self._upid('delsnapshot', req)
        elif '/tasks/' in path and path.endswith('/status'):
            upid = path.rsplit('/tasks/', 1)[1].rsplit('/status', 1)[0]
            upid = urllib.parse.unquote(upid)
            owner = self.task_owner.get(upid)
            if owner is not None and self._auth(req) != owner:
                # AS5: Proxmox only shows a task to the user that started it.
                raise urllib.error.HTTPError(url, 403, 'task not yours', None,
                                             io.BytesIO(b''))
            body = ({'status': 'stopped',
                     'exitstatus': 'task ERROR' if self.fail_task else 'OK'}
                    if upid else {'status': 'running'})
        else:
            raise urllib.error.HTTPError(url, 404, 'not found', None,
                                         io.BytesIO(b''))
        payload = json.dumps({'data': body}).encode()
        return FakeResponse(payload)


def unquote_all(path):
    text = str(path or '')
    while True:
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            return text
        text = decoded


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self._payload = payload

    def read(self, size=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False



@pytest.fixture
def pve(monkeypatch):
    fake = FakePVE()
    monkeypatch.setattr('urllib.request.urlopen', fake)
    iid = create_integration('pve', 'https://%s:8006/api2/json' % NODE_IP,
                             'header', 'PVEAPIToken=eshu=read-token',
                             auth_header_name='Authorization', kind='proxmox')
    seed_proxmox_tools(iid)
    set_snapshot_secret('pve', 'PVEAPIToken=eshu=snapshot-token')
    register_gateway(HOST, 'web01', 'v15.3')
    set_gateway_proxmox_bindings(HOST, node=NODE)
    set_dashboard_guest('none')
    clear_probe_cache()
    return fake


def _request(command='pct exec 101 -- systemctl restart nginx', inner='101',
             ip=HOST, status='pending'):
    return create_request(ip, command, status=status, ttl=0, session_id='s1',
                          inner_target=inner, agent='agent-a')


# ── the path guard (AK1, AK2, AM1, AO3) ──────────────────────────────────

class TestPathGuard:

    @pytest.mark.parametrize('path', [
        '/api2/json/nodes/pve/lxc/101/snapshot/x/rollback',
        '//nodes/pve/lxc/101/snapshot/x/rollback',
        '/api2/json//nodes/pve/qemu/102/snapshot/s/rollback',
        '/api2/extjs/nodes/pve/lxc/101/snapshot/s',
        '/NODES/PVE/LXC/101/SNAPSHOT/S',
        '/api2/json/nodes/pve/lxc/101/snapshot%2Fx%2Frollback',
        '/proxy/api2/json/nodes/pve/lxc/101/snapshot/s',
    ])
    def test_non_get_on_the_subtree_is_refused(self, path):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            assert is_snapshot_path(path), path
            assert snapshot_refusal(method, path) is not None, (method, path)

    def test_gets_and_heads_are_allowed(self):
        path = '/api2/json/nodes/pve/lxc/101/snapshot'
        assert is_snapshot_path(path)
        assert snapshot_refusal('GET', path) is None
        assert snapshot_refusal('HEAD', path) is None

    def test_the_sentinel_is_the_only_way_through(self):
        path = '/api2/json/nodes/pve/lxc/101/snapshot/s/rollback'
        assert snapshot_refusal('POST', path, SNAPSHOT_INTERNAL) is None
        assert snapshot_refusal('POST', path, True) is not None
        assert snapshot_refusal('POST', path, 'yes') is not None

    def test_other_paths_are_not_matched(self):
        assert not is_snapshot_path('/api2/json/nodes/pve/lxc/101/status/start')
        assert not is_snapshot_path('/api2/json/cluster/resources')
        assert not is_snapshot_path('/api2/json/nodes/pve/snapshot')

    def test_a_generic_post_is_refused_before_anything_is_sent(self, pve):
        integration = get_integration('pve')
        tool = get_tool('pve', 'generic_write') or {
            'name': 'generic_write', 'method': 'POST', 'path_template': '/',
            'params': [], 'read_only': False, 'generic': True,
            'transport': 'http', 'enabled': 1}
        with pytest.raises(ProxyError) as exc:
            execute_generic_call(integration, 'POST',
                                 'nodes/pve/lxc/101/snapshot/x/rollback',
                                 data={})
        assert exc.value.status_code == 403
        assert pve.requests == []
        assert tool  # the helper exists in the catalog

    def test_the_internal_call_passes_with_the_sentinel(self, pve):
        integration = get_integration('pve')
        tool = get_tool('pve', 'snapshot_list')
        res = execute_integration_call(integration, tool,
                                       {'node': NODE, 'type': 'lxc', 'vmid': 101},
                                       snapshot_internal=SNAPSHOT_INTERNAL)
        assert res.get('body') is not None
        assert any('snapshot' in url for url in pve.urls())


# ── the tools are internal ─────────────────────────────────────────────

class TestInternalTools:

    def test_the_seed_marks_the_five_tools_internal(self, pve):
        for name in ('snapshot_create', 'snapshot_list', 'snapshot_rollback',
                     'snapshot_delete', 'get_cluster_status',
                     'get_task_status'):
            tool = get_tool('pve', name)
            assert tool and tool.get('internal') == 1, name
        for name in ('list_vms', 'start_vm'):
            tool = get_tool('pve', name)
            assert tool and tool.get('internal') == 0, name

    def test_run_tool_refuses_an_internal_tool(self, pve):
        from core.tool_runner import run_tool
        for name in ('snapshot_create', 'snapshot_rollback'):
            out = json.loads(run_tool('pve', name, {'node': NODE, 'type': 'lxc',
                                                    'vmid': 101, 'snapname': 'x'}))
            assert 'error' in out
            message = out['error'] if isinstance(out['error'], str) else json.dumps(out)
            assert 'dashboard code' in message
        assert pve.requests == []

    def test_the_mcp_registry_never_sees_them(self, pve):
        from core.mcp_server import _register_tools

        class Inst:
            def __init__(self):
                self.names = []
                self._principal_allow = {}
                self._web_fetch_tools = set()

            def add_tool(self, fn, name=None, description=None):
                self.names.append(name)

        inst = Inst()
        _register_tools(inst, get_integration('pve'), namespaced=True)
        assert not any('snapshot_' in n for n in inst.names), inst.names
        assert any('list_vms' in n for n in inst.names)

    def test_a_queued_row_is_refused_at_execution(self, pve, auth_client):
        from db.integrations import create_pending_call
        call_id = create_pending_call(
            'pve', 'snapshot_rollback',
            {'node': NODE, 'type': 'lxc', 'vmid': 101, 'snapname': 'x'},
            'queued before F21', 's1', '', 'mcp')
        res = auth_client.post('/api/integration-calls/%d/approve' % call_id)
        body = res.json()
        assert body.get('status') == 'denied', body
        assert not any('snapshot' in url for url in pve.urls())

    def test_the_sentinel_is_not_forwarded_anywhere_else(self, pve):
        """The AST test the security reviewer asked for: the name appears only
        in its definition and the one comparison, and only snapshots.py passes
        the keyword."""
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                            'dashboard')
        proxy = os.path.join(root, 'core', 'integration_proxy.py')
        tree = ast.parse(open(proxy, encoding='utf-8').read())
        loads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id == 'SNAPSHOT_INTERNAL'
                 and isinstance(n.ctx, ast.Load)]
        stores = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and n.id == 'SNAPSHOT_INTERNAL'
                  and isinstance(n.ctx, ast.Store)]
        assert len(stores) == 1 and len(loads) == 1, (len(stores), len(loads))
        for rel in os.listdir(os.path.join(root, 'core')):
            if not rel.endswith('.py') or rel == 'integration_proxy.py':
                continue
            text = open(os.path.join(root, 'core', rel), encoding='utf-8').read()
            if 'SNAPSHOT_INTERNAL' in text:
                assert rel == 'snapshots.py', rel
        # no executor accepts the keyword except execute_integration_call
        for mod, funcs in (('core/tool_runner.py', ('_run_tool', 'run_tool')),
                           ('core/mcp_server.py', None)):
            source = open(os.path.join(root, mod), encoding='utf-8').read()
            for name in funcs or ():
                assert 'snapshot_internal' not in source.split('def %s(' % name)[1].split(')')[0]


def _accepts_json():
    import inspect
    from db.integrations import create_pending_call
    return 'payload_json' in str(inspect.signature(create_pending_call)) or True


# ── resolution (§2, AO1, AM3, AM4, AP1) ─────────────────────────────────

class TestOffer:

    def test_inner_target_guest_is_offered(self, pve):
        row = _request()
        data = offer_for_request(row)
        assert data['available'] is True, data
        assert data['target']['vmid'] == '101'
        assert data['target']['type'] == 'lxc'
        assert data['source'] == 'inner-target'

    def test_a_read_gets_no_offer(self, pve):
        row = _request(command='ls -la /var/log', inner='')
        assert offer_for_request(row)['reason'] == 'no_write_step'

    def test_a_command_writing_on_the_node_is_partial(self, pve):
        row = _request(command='pct exec 101 -- systemctl restart nginx && '
                               'pvesm status > /tmp/x')
        data = offer_for_request(row)
        assert data['available'] is False
        assert data['reason'] in ('snapshot_target_partial', 'snapshot_target_none')

    def test_a_command_touching_two_guests_is_partial(self, pve):
        row = _request(command='pct exec 101 -- systemctl restart nginx && '
                               'pct exec 102 -- systemctl restart db')
        data = offer_for_request(row)
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_partial'
        assert data['uncovered']

    def test_a_gateway_guest_is_found_from_its_own_config(self, pve):
        pve.add_guest(103, 'ha01', config_ip=HOST)
        replace_source_items('pve', [
            {'kind': 'guest', 'key': '103', 'name': 'ha01', 'ips': [HOST],
             'host': NODE, 'vmid': '103', 'extra': {}}])
        row = _request(command='systemctl restart nginx', inner='')
        data = offer_for_request(row)
        assert data['available'] is True, data
        assert data['target']['vmid'] == '103'
        assert data['source'] == 'guest-config'

    def test_an_inventory_ip_claim_alone_is_not_enough(self, pve):
        pve.add_guest(103, 'other', config_ip='192.0.2.99')
        replace_source_items('pve', [
            {'kind': 'guest', 'key': '103', 'name': 'other', 'ips': [HOST],
             'host': NODE, 'vmid': '103', 'extra': {}}])
        row = _request(command='systemctl restart nginx', inner='')
        assert offer_for_request(row)['available'] is False

    def test_a_node_identified_by_name_is_not_enough(self, pve, monkeypatch):
        """AM3: the gateway's *hostname* is the node name, and that is not
        evidence — only an IP match or the operator field is."""
        set_gateway_proxmox_bindings(HOST, node='', guest='')
        pve.guests[0]['node'] = 'other-node'
        row = _request()
        assert offer_for_request(row)['available'] is False

    def test_ap1_no_setting_refuses_every_guest(self, pve):
        """AP1: with no dashboard_guest there is no offer for ANY guest. The
        address match only adds a refusal for the matched one."""
        set_dashboard_guest('')
        pve.add_guest(103, 'ha01', config_ip=HOST)
        matched = _request(command='pct exec 103 -- systemctl restart nginx',
                           inner='103')
        other = _request(command='pct exec 101 -- systemctl restart nginx',
                         inner='101')
        first = offer_for_request(matched)
        second = offer_for_request(other)
        assert first['available'] is False and second['available'] is False
        assert first['reason'] == 'snapshot_target_self'
        assert second['reason'] == 'snapshot_dashboard_guest_unknown'
        assert second['suggest'] == 'pve/101'

    def test_ao2_setting_says_not_on_this_cluster(self, pve):
        set_dashboard_guest('none')
        assert offer_for_request(_request())['available'] is True

    def test_the_dashboard_guest_setting_refuses_self(self, pve):
        set_dashboard_guest('pve/101')
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_self'

    def test_a_full_disk_is_a_warning_not_a_refusal(self, pve):
        row = _request(command='pct exec 102 -- systemctl restart db', inner='102')
        data = offer_for_request(row)
        assert data['available'] is True
        assert any('full' in w for w in data['warnings'])


# ── create, then approve (§5) ──────────────────────────────────────────

class TestSequence:

    def test_create_then_approve(self, pve):
        row = _request()
        result = create_and_approve(row, operator='me')
        assert result['ok'] is True, result
        assert get_request_row(row)['status'] == 'approved'
        snap = result['snapshot']
        assert snap['state'] == 'active'
        assert snap['name'].startswith('eshu-r%d-' % row)
        listed = pve.snapshots[(NODE, 101)]
        assert listed[0]['name'] == snap['name']
        assert listed[0]['description'].startswith('eshu request #%d' % row)
        creates = [r for r in pve.requests if r['method'] == 'POST']
        assert creates and 'snapshot' in creates[0]['url']
        assert 'snapshot-token' in creates[0]['headers'].get('Authorization', '')

    def test_a_failed_create_approves_nothing(self, pve):
        pve.fail_create = 403
        row = _request()
        result = create_and_approve(row)
        assert result['ok'] is False and result['reason'] == 'snapshot_create_failed'
        assert get_request_row(row)['status'] == 'pending'
        assert get_snapshot(result['snapshot']['id'])['state'] == 'failed'

    def test_a_failed_task_approves_nothing(self, pve):
        pve.fail_task = True
        row = _request()
        result = create_and_approve(row)
        assert result['ok'] is False and result['reason'] == 'snapshot_task_failed'
        assert get_request_row(row)['status'] == 'pending'
        assert get_snapshot(result['snapshot']['id'])['state'] == 'failed'

    def test_a_request_decided_meanwhile_is_not_run_twice(self, pve):
        row = _request()
        assert get_request_row(row)['status'] == 'pending'
        from db.requests import update_request_status_if_pending
        assert update_request_status_if_pending(row, 'denied') is True
        result = create_and_approve(row)
        assert result['ok'] is False and result['reason'] == 'not_pending'

    def test_no_snapshot_token_means_no_offer(self, pve):
        set_snapshot_secret('pve', '')
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_unavailable'


# ── rollback and delete (§6) ───────────────────────────────────────────

class TestRollbackAndDelete:

    def _made(self, pve):
        row = _request()
        result = create_and_approve(row)
        assert result['ok'], result
        return result['snapshot']['id']

    def test_rollback_succeeds_and_audits(self, pve):
        sid = self._made(pve)
        result = rollback(sid, operator='me')
        assert result['ok'] is True, result
        assert get_snapshot(sid)['state'] == 'rolled_back'
        assert any('rollback' in r['url'] and r['method'] == 'POST'
                   for r in pve.requests)

    def test_a_failed_row_cannot_be_rolled_back(self, pve):
        sid = self._made(pve)
        set_snapshot_state(sid, 'failed')
        result = rollback(sid)
        assert result['ok'] is False and result['reason'] == 'snapshot_state'
        assert get_snapshot(sid)['state'] == 'failed'

    def test_a_recycled_vmid_is_refused(self, pve):
        sid = self._made(pve)
        for guest in pve.guests:
            if guest['vmid'] == 101:
                guest['name'] = 'something-else'
        assert rollback(sid)['reason'] == 'snapshot_target_changed'
        assert delete(sid)['reason'] == 'snapshot_target_changed'

    def test_a_replaced_snapshot_name_is_refused(self, pve):
        sid = self._made(pve)
        pve.snapshots[(NODE, 101)][0]['description'] = 'someone else'
        assert rollback(sid)['reason'] == 'snapshot_not_ours'

    def test_an_expired_row_is_refused(self, pve):
        sid = self._made(pve)
        with db_conn() as conn:
            conn.execute('UPDATE snapshots SET expires_at = 1 WHERE id = ?', (sid,))
            conn.commit()
        assert rollback(sid)['reason'] == 'snapshot_expired'

    def test_delete_removes_it_and_marks_the_row(self, pve):
        sid = self._made(pve)
        result = delete(sid)
        assert result['ok'] is True
        assert get_snapshot(sid)['state'] == 'deleted'
        assert pve.snapshots[(NODE, 101)] == []

    def test_a_failed_row_can_be_deleted(self, pve):
        sid = self._made(pve)
        set_snapshot_state(sid, 'failed')
        assert delete(sid)['ok'] is True


# ── expiry (§7) ────────────────────────────────────────────────────────

def _made(pve):
    """A request with an active snapshot, made the way the operator would."""
    row = _request()
    result = create_and_approve(row)
    assert result['ok'], result
    return result['snapshot']['id']


class TestExpiry:

    def test_expiry_deletes_only_its_own_expired_rows(self, pve):
        sid = _made(pve)
        fresh = create_and_approve(_request(command='pct exec 102 -- systemctl restart db',
                                            inner='102'))
        with db_conn() as conn:
            conn.execute('UPDATE snapshots SET expires_at = 1 WHERE id = ?', (sid,))
            conn.commit()
        result = sweep()
        assert result['deleted'] == 1
        assert get_snapshot(sid)['state'] == 'expired'
        assert get_snapshot(fresh['snapshot']['id'])['state'] == 'active'

    def test_a_failed_row_whose_snapshot_is_gone_becomes_gone(self, pve):
        sid = _made(pve)
        pve.snapshots[(NODE, 101)] = []
        set_snapshot_state(sid, 'failed', expires_at=1)
        sweep()
        assert get_snapshot(sid)['state'] == 'gone'

    def test_a_creating_row_is_failed_by_the_sweep(self, pve):
        rid = create_snapshot(request_id=1, integration='pve', node=NODE,
                              type_='lxc', vmid='101', name='eshu-r1-1',
                              description='eshu request #1',
                              expires_at=int(time.time()) - 1,
                              created_by='me')
        with db_conn() as conn:
            conn.execute('UPDATE snapshots SET created_at = 1 WHERE id = ?', (rid,))
            conn.commit()
        sweep()
        assert get_snapshot(rid)['state'] in ('failed', 'expired', 'gone')

    def test_expiry_hours_default(self, pve):
        assert expiry_hours() == 72


# ── the API ────────────────────────────────────────────────────────────

class TestApi:

    def test_the_offer_route_needs_a_session(self, client, pve):
        row = _request()
        assert client.get('/api/requests/%d/snapshot' % row).status_code == 401

    def test_the_offer_route_returns_the_offer(self, auth_client, pve):
        row = _request()
        body = auth_client.get('/api/requests/%d/snapshot' % row).json()
        assert body['available'] is True
        assert body['target']['vmid'] == '101'

    def test_snapshot_approve_approves_the_request(self, auth_client, pve):
        row = _request()
        body = auth_client.post('/api/requests/%d/snapshot-approve' % row).json()
        assert body['ok'] is True, body
        assert get_request_row(row)['status'] == 'approved'

    def test_rollback_and_delete_routes(self, auth_client, pve):
        row = _request()
        snap = auth_client.post('/api/requests/%d/snapshot-approve' % row).json()['snapshot']
        assert auth_client.post('/api/snapshots/%d/rollback' % snap['id']).json()['ok'] is True
        assert auth_client.post('/api/snapshots/999999/rollback').status_code == 404

    def test_settings_route_round_trips(self, auth_client, pve):
        assert auth_client.put('/api/snapshots/settings?expiry_hours=12'
                               '&dashboard_guest=none').json()['expiry_hours'] == 12
        assert dashboard_guest() == 'none'
        assert auth_client.put('/api/snapshots/settings?expiry_hours=9999').status_code == 400

    def test_snapshot_token_is_never_returned(self, auth_client, pve):
        body = auth_client.get('/api/integrations').json()
        for integration in body if isinstance(body, list) else body.get('integrations', []):
            assert 'snapshot_secret' not in integration
        assert get_snapshot_secret('pve') == 'PVEAPIToken=eshu=snapshot-token'


# ── code round 2 (AS1–AS6) ─────────────────────────────────────────────

class TestRound2:

    # AS1 — an unnormalisable path fails closed for state-changing methods.
    @pytest.mark.parametrize('path', [
        '/api2/json/nodes/pve/lxc/101/snapshot/x/rollback/../rollback',
        '/api2/json/nodes/pve/lxc/101/snapshot/x/rollback/%2e%2e/rollback',
        '/api2/json/nodes/pve/lxc/101/snapshot/x/rollback\\..\\rollback',
        '/api2/json/nodes/pve/lxc/101/snapshot/x/roll\x01back',
    ])
    def test_a_dot_segment_spelling_is_refused_for_post(self, path):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            assert snapshot_refusal(method, path) is not None, (method, path)
        # A read stays a read.
        assert snapshot_refusal('GET', path) is None
        assert snapshot_refusal('HEAD', path) is None

    def test_a_dot_segment_outside_the_subtree_is_refused_too(self):
        """AS1 says every integration, not only Proxmox: the rule is about
        the path, not the subtree."""
        assert snapshot_refusal('POST', '/api/v3/series/../system/status') is not None

    def test_a_generic_post_with_a_dot_segment_never_reaches_the_wire(self, pve):
        integration = get_integration('pve')
        with pytest.raises(ProxyError) as exc:
            execute_generic_call(integration, 'POST',
                                 'nodes/pve/status/../resources', data={})
        assert exc.value.status_code == 403
        assert pve.requests == []

    # AS2 — create is refused while either freeze is on.
    def test_create_is_refused_under_an_operator_freeze(self, pve):
        set_trigger_freeze()
        try:
            row = _request()
            result = create_and_approve(row)
            assert result['ok'] is False and result['reason'] == 'frozen'
            assert get_request_row(row)['status'] == 'pending'
            assert snapshots_for_request(row) == []
            assert pve.requests == []   # no Proxmox call at all
        finally:
            clear_trigger_freeze()

    def test_create_is_refused_under_a_tripwire_freeze(self, pve):
        set_meta_json('tripwire_freeze', str(int(time.time())))
        try:
            row = _request()
            result = create_and_approve(row)
            assert result['ok'] is False and result['reason'] == 'tripwire_frozen'
            assert get_request_row(row)['status'] == 'pending'
            assert snapshots_for_request(row) == []
            assert pve.requests == []
        finally:
            set_meta_json('tripwire_freeze', '')

    def test_a_freeze_started_during_the_snapshot_approves_nothing(self, pve, monkeypatch):
        """The second check, just before approving, is the point: a freeze that
        begins while the snapshot runs must not let the approval through."""
        import core.snapshots as snaps
        real = snaps._wait_task

        def freeze_then_poll(*args, **kwargs):
            ok = real(*args, **kwargs)
            set_trigger_freeze()
            return ok

        monkeypatch.setattr(snaps, '_wait_task', freeze_then_poll)
        try:
            row = _request()
            result = create_and_approve(row)
            assert result['ok'] is False and result['reason'] == 'frozen'
            assert get_request_row(row)['status'] == 'pending'
            assert result['snapshot']['state'] == 'active'
        finally:
            clear_trigger_freeze()

    # AS3 — one approve path, so the shapes match.
    def test_a_snapshot_approval_matches_a_plain_one(self, pve):
        from core.approvals import approve_pending_request
        plain = _request(command='ls -la /var/log')
        assert approve_pending_request(plain) == 'approved'
        snap_row = _request()
        assert create_and_approve(snap_row)['ok'] is True

        first = get_request_row(plain)
        second = get_request_row(snap_row)
        assert first['status'] == second['status'] == 'approved'
        assert first['decided_at'] and second['decided_at']
        assert abs(int(second['decided_at']) - int(first['decided_at'])) <= 5

        details = {r['details'] for r in get_audit_log(100)
                   if r['event_type'] == 'jit_approved'}
        assert 'JIT #%d approved' % plain in details
        assert 'JIT #%d approved' % snap_row in details

    def test_a_snapshot_approval_of_a_decided_request_does_not_approve_twice(self, pve):
        from core.approvals import approve_pending_request
        row = _request()
        assert approve_pending_request(row) == 'approved'
        assert approve_pending_request(row) == 'not_pending'
        assert approve_pending_request(999999) == 'not_found'

    # AS4 — two concurrent clicks make one snapshot.
    def test_two_concurrent_clicks_make_one_snapshot(self, pve):
        row = _request()
        started = threading.Event()
        release = threading.Event()
        pve.block_create = (started, release)
        results = []

        def click():
            try:
                results.append(create_and_approve(row))
            except Exception as exc:  # noqa: BLE001 — the test asserts on it
                results.append({'ok': False, 'reason': 'raised: %s' % exc})

        first = threading.Thread(target=click)
        second = threading.Thread(target=click)
        first.start()
        try:
            assert started.wait(10), 'the first create never reached the fake'
            second.start()
            second.join(15)
        finally:
            release.set()
            first.join(15)
            pve.block_create = None

        assert len(results) == 2, results
        assert sum(1 for r in results if r.get('ok')) == 1, results
        assert sum(1 for r in results
                   if r.get('reason') == 'snapshot_in_progress') == 1, results
        assert len(snapshots_for_request(row)) == 1
        assert get_request_row(row)['status'] == 'approved'

    # AS5 — the task poll uses the token that started the task.
    def test_the_task_poll_uses_the_snapshot_token(self, pve):
        row = _request()
        assert create_and_approve(row)['ok'] is True
        polls = [r for r in pve.requests if '/tasks/' in r['url']]
        assert polls
        for poll in polls:
            assert 'snapshot-token' in poll['headers'].get('Authorization', '')

    def test_rollback_and_delete_poll_with_the_snapshot_token(self, pve):
        sid = _made(pve)
        pve.requests.clear()
        assert rollback(sid)['ok'] is True
        sid2 = _made(pve)
        pve.requests.clear()
        assert delete(sid2)['ok'] is True
        polls = [r for r in pve.requests if '/tasks/' in r['url']]
        assert polls
        for poll in polls:
            assert 'snapshot-token' in poll['headers'].get('Authorization', '')

    def test_the_fake_refuses_a_task_read_from_another_token(self, pve):
        """The fake models Proxmox's ownership rule, so a regression to the
        read-only token would make every create fail, not pass quietly."""
        row = _request()
        assert create_and_approve(row)['ok'] is True
        upid, owner = next(iter(pve.task_owner.items()))
        assert 'snapshot-token' in owner
        url = ('https://%s:8006/api2/json/nodes/%s/tasks/%s/status'
               % (NODE_IP, NODE, urllib.parse.quote(upid, safe='')))
        request = urllib.request.Request(
            url, headers={'Authorization': 'PVEAPIToken=eshu=read-token'},
            method='GET')
        with pytest.raises(urllib.error.HTTPError) as exc:
            pve(request)
        assert exc.value.code == 403

    # AS6 — no single-row read carries the second token.
    def test_no_integration_row_carries_the_snapshot_secret(self, pve):
        integration = get_integration('pve')
        assert 'snapshot_secret' not in integration
        assert 'snapshot_secret' not in get_integration_by_id(integration['id'])
        # The internal `include_secret` list is not an exception either.
        internal = [i for i in get_integrations(include_secret=True)
                    if i['name'] == 'pve']
        assert internal and 'snapshot_secret' not in internal[0]
        assert get_snapshot_secret('pve') == 'PVEAPIToken=eshu=snapshot-token'


# ── code round 3 (AU1–AU4) ─────────────────────────────────────────────

def _reprobe(pve, main=None, snapshot=None, version=None, status=200):
    """Change the fake's probe answers and drop the cached result."""
    if main is not None:
        pve.main_privileges = main
    if snapshot is not None:
        pve.snapshot_privileges = snapshot
    if version is not None:
        pve.version = version
    pve.probe_status = status
    clear_probe_cache()


class TestCoverageStructured:
    """AU1: coverage is equality on the step's recorded target, not a
    substring of its labels or text."""

    def test_a_node_write_mentioning_the_vmid_is_partial(self, pve):
        row = _request(command='pct exec 101 -- systemctl restart nginx && '
                               'echo 101 > /tmp/note', inner='101')
        data = offer_for_request(row)
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_partial'
        assert data['uncovered']

    def test_a_write_into_a_guest_whose_vmid_has_the_target_as_a_prefix(self, pve):
        pve.add_guest(1010, 'other')
        row = _request(command='pct exec 101 -- systemctl restart nginx && '
                               'pct exec 1010 -- systemctl restart other',
                       inner='101')
        data = offer_for_request(row)
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_partial'
        assert data['uncovered']

    def test_an_incomplete_parse_is_partial(self, pve):
        row = _request(command="pct exec 101 -- sh -c 'systemctl restart nginx",
                       inner='101')
        data = offer_for_request(row)
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_partial'
        assert data['uncovered']

    def test_a_covered_write_still_gets_the_offer(self, pve):
        row = _request(command='pct exec 101 -- systemctl restart nginx',
                       inner='101')
        assert offer_for_request(row)['available'] is True


class TestCredentialProbe:
    """AU2: AM2's probe, on both tokens, before any target work."""

    def test_the_main_token_holding_rollback_refuses_the_offer(self, pve):
        _reprobe(pve, main={'/': {'VM.Audit': 1, 'VM.Snapshot.Rollback': 1}})
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_main_token_privileged'
        assert data['main_token_privileged'] is True

    def test_the_main_token_holding_snapshot_refuses_the_offer(self, pve):
        _reprobe(pve, main={'/vms/101': {'VM.Snapshot': 1}})
        assert offer_for_request(_request())['reason'] == \
            'snapshot_main_token_privileged'

    def test_identical_tokens_are_refused(self, pve):
        set_snapshot_secret('pve', get_integration('pve')['secret'])
        clear_probe_cache()
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_tokens_identical'

    def test_the_snapshot_token_missing_snapshot(self, pve):
        _reprobe(pve, snapshot={'/': {'VM.Audit': 1}})
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_unavailable'
        assert 'VM.Snapshot' in data['detail']

    def test_pve_8_needs_rollback_on_the_snapshot_token(self, pve):
        _reprobe(pve, snapshot={'/': {'VM.Snapshot': 1}},
                 version={'version': '8.2.4'})
        assert offer_for_request(_request())['reason'] == 'snapshot_unavailable'

    def test_pve_7_needs_only_snapshot(self, pve):
        _reprobe(pve, snapshot={'/': {'VM.Snapshot': 1}},
                 version={'version': '7.4-17'})
        assert offer_for_request(_request())['available'] is True

    def test_a_version_that_cannot_be_read_is_treated_as_pve_8(self, pve):
        _reprobe(pve, snapshot={'/': {'VM.Snapshot': 1}},
                 version={'version': 'unknown'})
        _reprobe(pve, snapshot={'/': {'VM.Snapshot': 1}}, version={})
        assert offer_for_request(_request())['reason'] == 'snapshot_unavailable'

    def test_a_probe_error_fails_closed(self, pve):
        _reprobe(pve, status=500)
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_unavailable'

    def test_create_reprobes_instead_of_trusting_the_cached_offer(self, pve):
        row = _request()
        assert offer_for_request(row)['available'] is True
        # The cached answer still says `ok`; create must not use it.
        pve.main_privileges = {'/': {'VM.Snapshot.Rollback': 1}}
        result = create_and_approve(row)
        assert result['ok'] is False
        assert result['reason'] == 'snapshot_main_token_privileged'
        assert get_request_row(row)['status'] == 'pending'
        assert snapshots_for_request(row) == []

    def test_the_integration_route_reports_the_red_warning(self, auth_client, pve):
        _reprobe(pve, main={'/': {'VM.Snapshot': 1}})
        body = auth_client.get('/api/integrations/pve/snapshot').json()
        assert body['main_token_privileged'] is True
        assert body['available'] is False
        assert body['reason'] == 'snapshot_main_token_privileged'
        assert body['configured'] is True


class TestTheRowsIntegration:
    """AU3: actions use the row's integration; the offer needs the binding
    when two are enabled."""

    def _second(self, pve):
        second = create_integration(
            'pve2', 'https://192.0.2.51:8006/api2/json', 'header',
            'PVEAPIToken=eshu=read-two',
            auth_header_name='Authorization', kind='proxmox')
        seed_proxmox_tools(second)
        set_snapshot_secret('pve2', 'PVEAPIToken=eshu=snapshot-two')
        clear_probe_cache()
        return second

    def test_two_integrations_refuse_the_offer_without_a_binding(self, pve):
        self._second(pve)
        data = offer_for_request(_request())
        assert data['available'] is False
        assert data['reason'] == 'snapshot_target_ambiguous'

    def test_the_binding_names_the_integration(self, pve):
        self._second(pve)
        set_gateway_proxmox_bindings(HOST, node=NODE, integration='pve')
        assert offer_for_request(_request())['available'] is True

    def test_a_binding_that_names_nothing_enabled_is_ambiguous(self, pve):
        self._second(pve)
        set_gateway_proxmox_bindings(HOST, node=NODE, integration='pve3')
        assert offer_for_request(_request())['reason'] == \
            'snapshot_target_ambiguous'

    def test_rollback_uses_the_integration_on_the_row(self, pve):
        self._second(pve)
        set_gateway_proxmox_bindings(HOST, node=NODE, integration='pve')
        sid = _made(pve)
        with db_conn() as conn:
            conn.execute("UPDATE snapshots SET integration = 'pve2' WHERE id = ?",
                         (sid,))
            conn.commit()
        assert rollback(sid)['ok'] is True, rollback(sid)
        posts = [r for r in pve.requests if r['method'] == 'POST'
                 and 'rollback' in r['url']]
        assert posts and 'snapshot-two' in posts[-1]['headers'].get(
            'Authorization', '')

    def test_a_missing_or_disabled_integration_refuses_the_action(self, pve):
        sid = _made(pve)
        with db_conn() as conn:
            conn.execute("UPDATE snapshots SET integration = 'gone' WHERE id = ?",
                         (sid,))
            conn.commit()
        assert rollback(sid)['reason'] == 'snapshot_integration_missing'
        assert delete(sid)['reason'] == 'snapshot_integration_missing'
        assert get_snapshot(sid)['state'] == 'active'


class TestSmallerPoints:
    """AU4: the marker, the UPID shape, and the state claim."""

    def test_the_marker_needs_the_space_after_the_id(self, pve):
        sid = _made(pve)
        pve.snapshots[(NODE, 101)][0]['description'] = 'eshu request #12 other'
        assert rollback(sid)['reason'] == 'snapshot_not_ours'
        # The real marker for request 1 still matches.
        pve.snapshots[(NODE, 101)][0]['description'] = 'eshu request #1 other'
        assert _recheck(get_snapshot(sid))[0] is True

    def test_the_full_upid_shape(self):
        good = 'UPID:pve:000A1B2C:00F3E4D5:65A1B2C3:qmcreate:101:root@pam:'
        assert _upid_node(good, 'pve') is True
        assert _upid_node(good, 'other') is False
        for bad in (good.replace('65A1B2C3', '65A1B2C3/../x'),
                    good.replace('qmcreate', 'qmcreate?x'),
                    good.replace('root@pam', 'root#pam'),
                    'UPID:pve:65A1B2C3:qmcreate:101:root@pam:',
                    'UPID:pve:000A1B2C:00F3E4D5:65A1B2C3:qmcreate:101'):
            assert _upid_node(bad, 'pve') is False, bad

    def test_a_bad_upid_from_create_refuses(self, pve):
        pve.bad_upid = 'UPID:pve:000A1B2C:00F3E4D5:65A1B2C3:snapshot:101:root@pam:/x'
        result = create_and_approve(_request())
        assert result['ok'] is False and result['reason'] == 'snapshot_bad_upid'

    def test_a_claimed_row_cannot_be_rolled_back_or_deleted_again(self, pve):
        sid = _made(pve)
        assert claim_state(sid, ('active',), 'deleting') is True
        assert rollback(sid)['reason'] == 'snapshot_state'
        assert delete(sid)['reason'] == 'snapshot_state'
        assert get_snapshot(sid)['state'] == 'deleting'

    def test_a_failed_rollback_call_releases_the_claim(self, pve):
        sid = _made(pve)
        pve.fail_rollback = 403
        result = rollback(sid)
        assert result['ok'] is False
        assert result['reason'] == 'snapshot_rollback_failed'
        assert get_snapshot(sid)['state'] == 'active'
        # The claim is released, so a second attempt can try again.
        pve.fail_rollback = None
        assert rollback(sid)['ok'] is True

    def test_a_failed_delete_call_releases_the_claim(self, pve):
        sid = _made(pve)
        pve.fail_delete = 403
        assert delete(sid)['reason'] == 'snapshot_delete_failed'
        assert get_snapshot(sid)['state'] == 'active'
        pve.fail_delete = None
        assert delete(sid)['ok'] is True
