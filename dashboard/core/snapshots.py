"""F21: snapshot before approve.

One place decides whether a request's snapshot is offered, creates it,
polls Proxmox, and rolls it back or deletes it later. The four Proxmox tools
are `internal` (never registered on an MCP surface, refused by `run_tool`,
by the pending-call executor and by the path guard in
`core/integration_proxy.py`), and they run on a **second** token
(`integrations.snapshot_secret`) that agent-facing code never holds.

Nothing here trusts a name: the node comes from `cluster/status` matched by
IP (or the operator's `gateways.proxmox_node`), a gateway-as-guest is matched
only against the guest's own configuration, and every action re-checks the
row against the live guest before it touches anything.
"""
import json
import re
import threading
import time

from core.approvals import approve_pending_request
from core.integration_proxy import SNAPSHOT_INTERNAL, ProxyError
from core.integration_proxy import execute_integration_call
from db.audit import record_audit_event
from db.core import db_conn
from db.gateways import (
    get_gateway_proxmox_bindings, get_trigger_freeze,
)
from db.integrations import get_integration, get_integrations, get_tool
from db.inventory import get_inventory_items
from db.misc import get_meta_json, set_meta_json
from db.requests import get_request_row
from db.snapshots import (
    bump_attempts, create_snapshot, due_snapshots, get_snapshot,
    set_snapshot_state, snapshots_for_request, stale_creating,
)

POLL_INTERVAL = 2
POLL_TIMEOUT_KEY = 'snapshot_poll_timeout_s'
EXPIRY_HOURS_KEY = 'snapshot_expiry_hours'
DASHBOARD_GUEST_KEY = 'dashboard_guest'
DEFAULT_EXPIRY_HOURS = 72
MAX_EXPIRY_HOURS = 336
MAX_POLL_TIMEOUT = 600
SWEEP_OLDER_THAN = 900
THIN_POOL_WARN = 85
MAX_ATTEMPTS = 6

_IPV4 = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')
_UPID = re.compile(r'^UPID:([^:]+):')
_IP_IN_CONFIG = re.compile(r'(?:^|[,;\s])ip=([0-9a-zA-Z./:]+)', re.IGNORECASE)
_SAFE_NAME = re.compile(r'^[a-z][a-z0-9-]{2,}$')

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None


# ── settings ───────────────────────────────────────────────────────────

def _int_setting(key, default, lo, hi):
    try:
        value = int(get_meta_json(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def expiry_hours():
    return _int_setting(EXPIRY_HOURS_KEY, DEFAULT_EXPIRY_HOURS, 1, MAX_EXPIRY_HOURS)


def poll_timeout():
    return _int_setting(POLL_TIMEOUT_KEY, 120, 30, MAX_POLL_TIMEOUT)


def dashboard_guest():
    """The operator's answer: node/vmid, '' (unset), or 'none' (not on this
    cluster)."""
    return str(get_meta_json(DASHBOARD_GUEST_KEY, '') or '').strip()


def set_dashboard_guest(value):
    return set_meta_json(DASHBOARD_GUEST_KEY, str(value or '').strip()[:40])


# ── the Proxmox connection ─────────────────────────────────────────────

def proxmox_integration():
    for integration in get_integrations(include_secret=True):
        if (integration.get('kind') or '').lower() == 'proxmox' \
                and integration.get('enabled'):
            return integration
    return None


def _snapshot_integration(base):
    """A copy of the integration whose `secret` is the snapshot token (AM2).

    The agent-facing token stays read-only; only this copy can create,
    roll back or delete.
    """
    from db.integrations import get_snapshot_secret
    token = get_snapshot_secret(base['name'])
    if not token:
        return None
    copy = dict(base)
    copy['secret'] = token
    return copy


def _tool(integration, name):
    tool = get_tool(integration['name'], name)
    if not tool or not tool.get('enabled'):
        raise ProxyError(404, 'snapshot tool missing: %s' % name)
    return tool


def _call(integration, name, args, internal_token=None):
    """One tool call. `internal_token` is the sentinel for the four writes."""
    tool = _tool(integration, name)
    res = execute_integration_call(
        integration, tool, args, agent='eshu-snapshot',
        snapshot_internal=internal_token)
    status = res.get('status_code')
    refused = isinstance(status, int) and not 200 <= status < 300
    if res.get('error') or refused:
        raise ProxyError(status or 502,
                         str(res.get('error') or
                             'Proxmox refused the call (HTTP %s)' % status))
    body = res.get('body')
    try:
        data = json.loads(body) if body else None
    except ValueError:
        return body
    # Proxmox answers every call as {"data": ...}; the tools do not project it,
    # so unwrap the envelope here (a UPID is the data itself).
    if isinstance(data, dict) and set(data) == {'data'}:
        return data['data']
    return data


def _read(integration, name, args):
    """Reads go on the main token (the snapshot token has the same reads, but
    keeping them separate shows which token a call used)."""
    return _call(integration, name, args)


# ── resolution (§2) ────────────────────────────────────────────────────

def _node_rows(integration):
    data = _read(integration, 'get_cluster_status', {}) or []
    rows = [r for r in data if isinstance(r, dict) and r.get('type') == 'node']
    return rows


def _guest_rows(integration):
    data = _read(integration, 'get_cluster_resources', {}) or []
    out = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        if (row.get('type') or '') not in ('qemu', 'lxc'):
            continue
        vmid = str(row.get('vmid') or '')
        node = str(row.get('node') or '')
        if vmid and node:
            out[(node, vmid)] = row
    return out


def _node_for_gateway(integration, target_ip, bindings):
    """The node the gateway is: cluster/status by IP, else the operator field.

    Never a name match (AM3): a DNS or DHCP name can be re-pointed by anyone
    on the network, and the wrong node means the wrong guest.
    """
    rows = _node_rows(integration)
    for row in rows:
        if str(row.get('ip') or '') == target_ip:
            return str(row.get('name') or ''), 'cluster-status'
    bound = (bindings.get('proxmox_node') or '').strip()
    if bound and any(str(r.get('name') or '') == bound for r in rows):
        return bound, 'operator-node'
    return '', ''


def _config_ips(integration, node, type_, vmid):
    """The IPs written in the guest's own configuration (AM3)."""
    tool = 'get_container_config' if type_ == 'lxc' else 'get_vm_config'
    try:
        data = _read(integration, tool, {'node': node, 'vmid': int(vmid)}) or {}
    except (ProxyError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    found = []
    for key, value in data.items():
        if not (str(key).startswith('net') or str(key).startswith('ipconfig')):
            continue
        for match in _IP_IN_CONFIG.finditer(str(value or '')):
            address = match.group(1).split('/')[0]
            if _IPV4.match(address):
                found.append(address)
    return found


def _vmid_from_config(integration, node, type_, vmid, ip):
    return ip in _config_ips(integration, node, type_, vmid)


def _config_candidates(integration, guests, ip):
    """Guests whose *own config* claims `ip`, verified one by one.

    The inventory is only used to choose which guests to ask (cheap), never to
    decide: an IP a guest reports — a DHCP lease, an ARP entry, a Pi-hole
    record — is not evidence.
    """
    items = get_inventory_items()
    candidates = []
    for item in items:
        # F14 stores the *integration's* name as the source (not the kind).
        if (item.get('source') or '') != integration.get('name') \
                or (item.get('kind') or '') != 'guest':
            continue
        if ip not in (item.get('ips') or []):
            continue
        vmid = str(item.get('vmid') or '')
        node = str(item.get('host') or '')
        if (node, vmid) in guests:
            candidates.append((node, vmid))
    verified = []
    for node, vmid in candidates:
        type_ = str(guests[(node, vmid)].get('type') or '')
        if type_ and _vmid_from_config(integration, node, type_, vmid, ip):
            verified.append((node, vmid, type_))
    return verified


def _gateway_bindings(ip):
    try:
        return get_gateway_proxmox_bindings(ip) or {}
    except Exception:
        return {}


def _inner_targets(row):
    from core.cmd_parse import breakdown
    data = breakdown(row.get('command') or '') or {}
    return data


def _writes_uncovered(data, inner):
    """AO1: the steps the snapshot would not cover, or [] when it covers all.

    Case A needs exactly one inner target, equal to the resolved guest, and
    every write/unknown step carrying it. A step with no wrapper runs on the
    node and is not covered.
    """
    steps = data.get('steps') or []
    uncovered = []
    for step in steps:
        if (step.get('kind') or '') not in ('write', 'unknown'):
            continue
        context = ' '.join(str(c) for c in (step.get('context') or []))
        text = str(step.get('text') or '')
        if inner and (inner in context or inner in text):
            continue
        uncovered.append(text[:80])
    return uncovered


def resolve_target(row, integration=None):
    """Who would be snapshotted, and why not when there is no answer.

    Returns {'ok': bool, 'reason': code, 'detail': str, 'target': {...},
    'source': str, 'uncovered': [...]}.
    """
    integration = integration or proxmox_integration()
    if not integration:
        return {'ok': False, 'reason': 'snapshot_unavailable',
                'detail': 'no enabled Proxmox integration'}
    if not _snapshot_integration(integration):
        return {'ok': False, 'reason': 'snapshot_unavailable',
                'detail': 'the integration has no snapshot token'}
    ip = str(row.get('target_ip') or '')
    bindings = _gateway_bindings(ip)
    inner = str(row.get('inner_target') or '')
    data = _inner_targets(row)
    try:
        guests = _guest_rows(integration)
    except ProxyError as exc:
        return {'ok': False, 'reason': 'snapshot_unavailable',
                'detail': str(exc.message or '')[:120]}

    if inner:
        node, node_source = _node_for_gateway(integration, ip, bindings)
        if not node:
            return {'ok': False, 'reason': 'snapshot_target_none',
                    'detail': 'node_not_identified'}
        guest = guests.get((node, inner))
        if not guest:
            return {'ok': False, 'reason': 'snapshot_target_none',
                    'detail': 'guest_not_on_node'}
        uncovered = _writes_uncovered(data, inner)
        if uncovered:
            return {'ok': False, 'reason': 'snapshot_target_partial',
                    'detail': 'the snapshot would not cover %d step(s)' % len(uncovered),
                    'uncovered': uncovered}
        return _target_ok(integration, guest, node, node_source, 'inner-target',
                          ip, row)

    bound = (bindings.get('proxmox_guest') or '').strip()
    if bound and '/' in bound:
        node, vmid = bound.split('/', 1)
        guest = guests.get((node, vmid))
        if guest:
            return _target_ok(integration, guest, node, 'operator-guest',
                              'operator-guest', ip, row)
    verified = _config_candidates(integration, guests, ip)
    if len(verified) == 1:
        node, vmid, type_ = verified[0]
        return _target_ok(integration, guests[(node, vmid)], node,
                          'guest-config', 'guest-config', ip, row)
    if len(verified) > 1:
        return {'ok': False, 'reason': 'snapshot_target_ambiguous',
                'detail': 'the address is in more than one guest config'}
    # a node command with no inner target: a snapshot would not cover it
    node, _ = _node_for_gateway(integration, ip, bindings)
    if node:
        return {'ok': False, 'reason': 'snapshot_target_none',
                'detail': 'the command runs on the node, not in a guest'}
    return {'ok': False, 'reason': 'snapshot_target_none',
            'detail': 'no guest matches this gateway'}


def _target_ok(integration, guest, node, node_source, source, ip, row):
    vmid = str(guest.get('vmid') or '')
    type_ = str(guest.get('type') or '')
    name = str(guest.get('name') or '')
    if str(guest.get('status') or '') == 'stopped':
        pass  # a stopped guest is fine: the disks are the point
    # AM4/AP1: the dashboard's own guest, and the unknown case.
    dash = dashboard_guest()
    if dash and dash.lower() == 'none':
        pass
    elif dash and dash == '%s/%s' % (node, vmid):
        return {'ok': False, 'reason': 'snapshot_target_self',
                'detail': 'this is the guest that runs the dashboard'}
    elif not dash:
        # AP1: with no setting there is no offer for ANY guest. The address
        # match may only add a refusal, never stand in for the setting.
        if _guest_looks_like_dashboard(integration, node, type_, vmid, ip):
            return {'ok': False, 'reason': 'snapshot_target_self',
                    'detail': 'this guest claims the dashboard address; set '
                              'dashboard_guest to make the check exact'}
        return {'ok': False, 'reason': 'snapshot_dashboard_guest_unknown',
                'detail': 'set dashboard_guest (node/vmid, or "none") first',
                'suggest': '%s/%s' % (node, vmid)}
    return {'ok': True, 'reason': '', 'detail': '',
            'source': source, 'node_source': node_source,
            'target': {'node': node, 'type': type_, 'vmid': vmid,
                       'name': name,
                       'ips': _guest_config_ips(integration, node, type_, vmid),
                       'status': str(guest.get('status') or '')},
            'warnings': _warnings(integration, guest)}


def _guest_config_ips(integration, node, type_, vmid):
    try:
        return _config_ips(integration, node, type_, vmid)
    except Exception:
        return []


def _guest_looks_like_dashboard(integration, node, type_, vmid, ip):
    """Does this guest's own config claim the gateway's address?"""
    try:
        return ip in _config_ips(integration, node, type_, vmid)
    except Exception:
        return False


def _warnings(integration, guest):
    warnings = []
    try:
        disk = float(guest.get('disk') or 0)
        maxdisk = float(guest.get('maxdisk') or 0)
        if maxdisk > 0 and disk * 100.0 / maxdisk >= THIN_POOL_WARN:
            warnings.append('the guest disk is above %d%% full; a rollback '
                            'has to write' % THIN_POOL_WARN)
    except (TypeError, ValueError):
        pass
    if str(guest.get('status') or '') == 'stopped':
        warnings.append('the guest is stopped; the snapshot restores its disks only')
    return warnings


def _bindings(ip):
    return _gateway_bindings(ip)


# ── the offer ──────────────────────────────────────────────────────────

def offer_for_request(request_id):
    """What the card shows: the offer, the reason when there is none, and the
    snapshots this request already has."""
    row = get_request_row(int(request_id))
    if not row:
        return None
    existing = snapshots_for_request(int(request_id))
    out = {'request_id': int(request_id), 'existing': existing}
    if row.get('status') != 'pending':
        out.update({'available': False, 'reason': 'not_pending', 'detail': ''})
        return out
    from core.cmd_parse import breakdown
    summary = (breakdown(row.get('command') or '') or {}).get('summary') or {}
    if not (summary.get('write') or summary.get('unknown')):
        out.update({'available': False, 'reason': 'no_write_step', 'detail': ''})
        return out
    resolved = resolve_target(row)
    out['available'] = bool(resolved.get('ok'))
    out['reason'] = resolved.get('reason') or ''
    out['detail'] = resolved.get('detail') or ''
    out['uncovered'] = resolved.get('uncovered') or []
    out['suggest'] = resolved.get('suggest') or ''
    if resolved.get('ok'):
        out['target'] = resolved['target']
        out['source'] = resolved.get('source') or ''
        out['warnings'] = resolved.get('warnings') or []
    return out


# ── create, then approve (§5) ──────────────────────────────────────────

def _freeze_reason():
    """Why a new snapshot is refused while a freeze is on (§8), or ''.

    Two kinds: the operator's fleet freeze (`meta.trigger_freeze`), and the
    tripwire freeze F24 will set (`meta.tripwire_freeze`). F24 has not shipped,
    so the second key is normally absent; reading it here means §8's rule is
    already in place when it does, and the tests can exercise both.
    """
    if get_trigger_freeze() is not None:
        return 'frozen'
    try:
        tripwire = get_meta_json('tripwire_freeze', '')
    except Exception:  # noqa: BLE001 — a freeze probe must not fail a read
        tripwire = ''
    if tripwire not in (None, '', 0, '0', False):
        return 'tripwire_frozen'
    return ''


def _snapshot_name(request_id, now):
    name = 'eshu-r%d-%d' % (int(request_id), int(now))
    return name if _SAFE_NAME.match(name) else ''


def _description(request_id, command):
    from core.secret_scrub import scrub_string
    text = scrub_string(command or '')[:80]
    return 'eshu request #%d %s' % (int(request_id), text)


def _upid_node(upid, node):
    match = _UPID.match(str(upid or ''))
    if not match or match.group(1) != node:
        return False
    return True


def _wait_task(integration, node, upid, timeout):
    """Poll the task until it stops. True on exitstatus OK."""
    encoded = str(upid).replace(':', '%3A').replace('!', '%21')
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = _read(integration, 'get_task_status',
                           {'node': node, 'upid': encoded}) or {}
        except ProxyError:
            return False
        if isinstance(status, dict) and status.get('status') == 'stopped':
            return str(status.get('exitstatus') or '') == 'OK'
        time.sleep(POLL_INTERVAL)
    return False


def create_and_approve(request_id, operator=''):
    """Create the snapshot, then approve the request — or approve nothing.

    Returns {'ok': bool, 'reason': code, 'detail': str, 'snapshot': row}.
    """
    request_id = int(request_id)
    row = get_request_row(request_id)
    if not row:
        return {'ok': False, 'reason': 'not_found', 'detail': 'no such request'}
    if row.get('status') != 'pending':
        return {'ok': False, 'reason': 'not_pending',
                'detail': 'the request was already decided'}
    # AS2: create is refused while a freeze is on (design §8) — checked before
    # any resolution, so a frozen system makes no Proxmox call at all.
    freeze = _freeze_reason()
    if freeze:
        return {'ok': False, 'reason': freeze,
                'detail': 'a freeze is on; no snapshot is created and nothing '
                          'is approved'}
    resolved = resolve_target(row)
    if not resolved.get('ok'):
        return {'ok': False, 'reason': resolved.get('reason') or 'snapshot_unavailable',
                'detail': resolved.get('detail') or ''}
    integration = proxmox_integration()
    snapshot_integration = _snapshot_integration(integration)
    if not snapshot_integration:
        return {'ok': False, 'reason': 'snapshot_unavailable',
                'detail': 'no snapshot token'}
    target = resolved['target']
    now = int(time.time())
    with _LOCK:
        # AS4: a double click must not make two snapshots. The check is inside
        # the lock, so the second caller sees the first caller's row.
        for existing in snapshots_for_request(request_id):
            if existing.get('state') in ('creating', 'active'):
                return {'ok': False, 'reason': 'snapshot_in_progress',
                        'detail': 'this request already has a snapshot being '
                                  'made or active'}
        name = _snapshot_name(request_id, now)
        if not name:
            return {'ok': False, 'reason': 'snapshot_bad_name', 'detail': ''}
        snapshot_id = create_snapshot(
            request_id=request_id, integration=integration['name'],
            node=target['node'], type_=target['type'], vmid=target['vmid'],
            name=name, description=_description(request_id, row.get('command')),
            guest_name=target['name'], guest_ips=','.join(target['ips']),
            target_source=resolved.get('source') or '', created_by=operator,
            expires_at=now + expiry_hours() * 3600, now=now)
    args = {'node': target['node'], 'type': target['type'],
            'vmid': int(target['vmid']), 'snapname': name,
            'description': _description(request_id, row.get('command')),
            'vmstate': 0}
    try:
        upid = _call(snapshot_integration, 'snapshot_create', args,
                     SNAPSHOT_INTERNAL)
    except ProxyError as exc:
        set_snapshot_state(snapshot_id, 'failed', error=exc.message)
        record_audit_event('snapshot_create_failed', row.get('target_ip'),
                           details='Request #%d: %s' % (request_id, str(exc.message)[:160]),
                           actor=operator)
        return {'ok': False, 'reason': 'snapshot_create_failed',
                'detail': str(exc.message or '')[:200],
                'snapshot': get_snapshot(snapshot_id)}
    if not _upid_node(upid, target['node']):
        set_snapshot_state(snapshot_id, 'failed', error='bad_upid')
        return {'ok': False, 'reason': 'snapshot_bad_upid',
                'detail': 'the task id did not name the stored node',
                'snapshot': get_snapshot(snapshot_id)}
    # AS5: the task was started by the snapshot token, and Proxmox only shows
    # a task to the user that started it (or someone with Sys.Audit). Poll with
    # the same token or every snapshot ends `failed` on a real cluster.
    if not _wait_task(snapshot_integration, target['node'], upid, poll_timeout()):
        set_snapshot_state(snapshot_id, 'failed', error='task_failed_or_timeout')
        record_audit_event('snapshot_create_failed', row.get('target_ip'),
                           details='Request #%d: the task did not finish OK' % request_id,
                           actor=operator)
        return {'ok': False, 'reason': 'snapshot_task_failed',
                'detail': 'the task did not finish OK; the snapshot is left to '
                          'the expiry job',
                'snapshot': get_snapshot(snapshot_id)}
    set_snapshot_state(snapshot_id, 'active')
    # AS2: check again just before approving — a freeze that started while the
    # snapshot ran must not let the approval through. The snapshot stays on the
    # guest as an `active` row; the expiry job owns it from here (§7).
    freeze = _freeze_reason()
    if freeze:
        return {'ok': False, 'reason': freeze,
                'detail': 'a freeze started while the snapshot was being taken; '
                          'nothing was approved',
                'snapshot': get_snapshot(snapshot_id)}
    # AS3: the same path as the plain Approve button.
    approved = approve_pending_request(request_id) == 'approved'
    record_audit_event('snapshot_created', row.get('target_ip'),
                       details='Request #%d: snapshot %s on %s/%s (%s)'
                               % (request_id, name, target['node'],
                                  target['vmid'], resolved.get('source')),
                       actor=operator)
    if not approved:
        return {'ok': True, 'reason': 'already_decided',
                'detail': 'the snapshot exists; the request was decided while '
                          'it ran', 'snapshot': get_snapshot(snapshot_id)}
    return {'ok': True, 'reason': '', 'detail': '',
            'snapshot': get_snapshot(snapshot_id)}


# ── rollback and delete (§6) ───────────────────────────────────────────

def _recheck(row, allow_failed=False, for_expiry=False):
    """The five checks, in order. Returns (ok, reason, detail).

    `for_expiry` skips the expiry test: the expiry job only ever acts on rows
    that are already past it, so refusing them there would never delete
    anything.
    """
    state = row.get('state')
    if state != 'active' and not (allow_failed and state == 'failed'):
        return False, 'snapshot_state', 'a %s snapshot cannot be used' % state
    if not for_expiry and row.get('expires_at') \
            and int(row['expires_at']) <= int(time.time()):
        return False, 'snapshot_expired', 'past its expiry'
    integration = proxmox_integration()
    if not integration:
        return False, 'snapshot_unavailable', 'no Proxmox integration'
    ip = ''
    try:
        guests = _guest_rows(integration)
    except ProxyError as exc:
        return False, 'snapshot_unavailable', str(exc.message or '')[:120]
    guest = guests.get((row['node'], row['vmid']))
    if not guest:
        return False, 'snapshot_target_changed', 'the guest is gone from the cluster'
    current_name = str(guest.get('name') or '')
    current_ips = ','.join(_guest_config_ips(integration, row['node'],
                                             row['type'], row['vmid']))
    if row.get('guest_name') and current_name and current_name != row['guest_name']:
        return False, 'snapshot_target_changed', 'a different guest now has that vmid'
    if row.get('guest_ips') and current_ips and current_ips != row['guest_ips']:
        return False, 'snapshot_target_changed', 'the guest addresses changed'
    # AM4/AP1 at action time: the dashboard's own guest is never rolled back.
    dash = dashboard_guest()
    if dash and dash.lower() != 'none' and dash == '%s/%s' % (row['node'], row['vmid']):
        return False, 'snapshot_target_self', 'this guest runs the dashboard'
    try:
        listed = _read(integration, 'snapshot_list',
                       {'node': row['node'], 'type': row['type'],
                        'vmid': int(row['vmid'])}) or []
    except ProxyError as exc:
        return False, 'snapshot_unavailable', str(exc.message or '')[:120]
    for entry in listed:
        if not isinstance(entry, dict) or entry.get('name') != row['name']:
            continue
        if str(entry.get('description') or '').startswith(
                'eshu request #%d' % int(row['request_id'] or 0)):
            return True, '', ''
        return False, 'snapshot_not_ours', 'the name now holds another snapshot'
    return False, 'snapshot_not_ours', 'the snapshot is not on the guest'


def rollback(snapshot_id, operator=''):
    row = get_snapshot(int(snapshot_id))
    if not row:
        return {'ok': False, 'reason': 'not_found'}
    if get_trigger_freeze() is not None:
        # An operator freeze still allows a rollback (design §8): it is the way
        # back, and the only caller is the operator.
        pass
    ok, reason, detail = _recheck(row)
    if not ok:
        record_audit_event('snapshot_refused', '',
                           details='Rollback refused for #%d: %s (%s)'
                                   % (row['id'], reason, detail), actor=operator)
        return {'ok': False, 'reason': reason, 'detail': detail}
    integration = proxmox_integration()
    snap_int = _snapshot_integration(integration)
    if not snap_int:
        return {'ok': False, 'reason': 'snapshot_unavailable'}
    try:
        upid = _call(snap_int, 'snapshot_rollback',
                     {'node': row['node'], 'type': row['type'],
                      'vmid': int(row['vmid']), 'snapname': row['name']},
                     SNAPSHOT_INTERNAL)
    except ProxyError as exc:
        return {'ok': False, 'reason': 'snapshot_rollback_failed',
                'detail': str(exc.message or '')[:200]}
    if not _upid_node(upid, row['node']):
        return {'ok': False, 'reason': 'snapshot_bad_upid'}
    if not _wait_task(snap_int, row['node'], upid, poll_timeout()):
        return {'ok': False, 'reason': 'snapshot_task_failed',
                'detail': 'the rollback task did not finish OK'}
    set_snapshot_state(row['id'], 'rolled_back', rolled_back_at=int(time.time()))
    record_audit_event('snapshot_rolled_back', '',
                       details='Request #%s: %s rolled back %s/%s to %s'
                               % (row.get('request_id'), operator, row['node'],
                                  row['vmid'], row['name']), actor=operator)
    return {'ok': True, 'snapshot': get_snapshot(row['id'])}


def delete(snapshot_id, operator='', reason='manual', for_expiry=False):
    row = get_snapshot(int(snapshot_id))
    if not row:
        return {'ok': False, 'reason': 'not_found'}
    ok, why, detail = _recheck(row, allow_failed=True, for_expiry=for_expiry)
    if not ok:
        record_audit_event('snapshot_refused', '',
                           details='Delete refused for #%d: %s (%s)'
                                   % (row['id'], why, detail), actor=operator)
        return {'ok': False, 'reason': why, 'detail': detail}
    integration = proxmox_integration()
    snap_int = _snapshot_integration(integration)
    if not snap_int:
        return {'ok': False, 'reason': 'snapshot_unavailable'}
    try:
        upid = _call(snap_int, 'snapshot_delete',
                     {'node': row['node'], 'type': row['type'],
                      'vmid': int(row['vmid']), 'snapname': row['name']},
                     SNAPSHOT_INTERNAL)
    except ProxyError as exc:
        return {'ok': False, 'reason': 'snapshot_delete_failed',
                'detail': str(exc.message or '')[:200]}
    if upid and not _upid_node(upid, row['node']):
        return {'ok': False, 'reason': 'snapshot_bad_upid'}
    if upid and not _wait_task(snap_int, row['node'], upid, poll_timeout()):
        return {'ok': False, 'reason': 'snapshot_task_failed'}
    set_snapshot_state(row['id'], 'expired' if reason == 'expiry' else 'deleted',
                       deleted_at=int(time.time()))
    record_audit_event('snapshot_deleted', '',
                       details='Snapshot #%d (%s on %s/%s) deleted by %s'
                               % (row['id'], row['name'], row['node'],
                                  row['vmid'], reason), actor=operator)
    return {'ok': True, 'snapshot': get_snapshot(row['id'])}


# ── the expiry job (§7) ────────────────────────────────────────────────

def sweep(now=None, operator='expiry'):
    """Delete this automation's own expired rows, re-checked.

    `active` rows past expiry are deleted; `failed` rows are re-checked and
    either deleted (`expired`) or marked `gone`. A row that fails the re-check
    is retried, and `orphaned` after MAX_ATTEMPTS.
    """
    now = int(now or time.time())
    deleted = 0
    for row in stale_creating(SWEEP_OLDER_THAN, now=now):
        set_snapshot_state(row['id'], 'failed', error='create_interrupted')
    for row in due_snapshots(now=now):
        ok, reason, detail = _recheck(row, allow_failed=True, for_expiry=True)
        if not ok:
            # The snapshot is not on the guest any more: it is gone, not ours
            # to delete, and there is nothing to retry.
            if reason == 'snapshot_not_ours':
                set_snapshot_state(row['id'], 'gone', error='not_found_on_guest')
                continue
            bump_attempts(row['id'])
            fresh = get_snapshot(row['id'])
            if fresh and int(fresh.get('attempts') or 0) >= MAX_ATTEMPTS:
                set_snapshot_state(row['id'], 'orphaned', error=reason)
            continue
        result = delete(row['id'], operator=operator, reason='expiry',
                        for_expiry=True)
        if result.get('ok'):
            deleted += 1
        elif result.get('reason') == 'snapshot_not_ours':
            set_snapshot_state(row['id'], 'gone', error='not_found_on_guest')
    if deleted:
        record_audit_event('snapshot_expiry', '',
                           details='Expiry deleted %d snapshot(s)' % deleted,
                           actor=operator)
    return {'deleted': deleted}


def _sweep_loop():
    while not _STOP.is_set():
        try:
            sweep()
        except Exception as exc:  # noqa: BLE001 — a job must not die
            print('[snapshots] expiry sweep failed: %s' % exc, flush=True)
        _STOP.wait(600)


def start_expiry_job():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_sweep_loop, name='snapshot-expiry',
                               daemon=True)
    _THREAD.start()


def stop_expiry_job():
    _STOP.set()
