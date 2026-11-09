"""F14: the inventory — "where is X?" across Eshu and the homelab integrations.

Sources are **read-only and optional**: Eshu itself, Proxmox, Nginx Proxy
Manager, Pi-hole and Omada (Home Assistant add-on slugs need F9, which is not
on this branch). Each source read goes through
`core/read_policy.is_read_integration_call` (§2.6a) before anything is sent, so
the inventory can never execute a tool that is not a read.

The refresh caches one row per thing in `inventory_items` and records the last
result per source in `inventory_sources`. A source that fails keeps its last
good rows and is reported **stale** rather than disappearing.

`build_entities` joins the rows on IP first, then on a normalised name (an
FQDN also contributes its first label). Disagreements are shown, never
silently resolved: an IP claimed by two different names is a `conflict` on the
entity.
"""
import json
import re
import threading
import time

from core.integration_proxy import ProxyError
from core.integration_proxy import execute_integration_call
from core.ha_ws import execute_ws_call
from core.read_policy import is_read_integration_call
from core.tool_handlers import run_handler
from core.agent_context import get_current_agent
from db.audit import record_audit_event
from db.core import db_conn
from db.gateways import get_gateways
from db.integrations import (
    get_integration, get_integrations, get_tool, get_tools,
)
from db.inventory import (
    get_inventory_items, get_inventory_sources, record_source,
    replace_source_items,
)
from db.misc import get_meta_json, set_meta_json

INTERVAL_KEY = 'inventory_interval_minutes'
DEFAULT_INTERVAL_MINUTES = 15
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 120
WHEREIS_MAX = 5
WHEREIS_MAX_CHARS = 600

_LOCK = threading.Lock()

# Device- and guest-chosen names are untrusted text for a model: strip control
# characters before they reach the card or the MCP result.
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]+')


def _clean(value):
    return _CONTROL_RE.sub('', str(value or ''))[:200]
_STOP = threading.Event()

# Kind priority when picking an entity's display name.
_NAME_ORDER = {'gateway': 0, 'guest': 1, 'dns': 2, 'proxy': 3, 'client': 4}


class SourceError(Exception):
    """A source read failed; the caller records the reason and keeps the rows."""


# ── settings ────────────────────────────────────────────────────────────

def interval_minutes():
    try:
        value = int(get_meta_json(INTERVAL_KEY, DEFAULT_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value))


def set_interval_minutes(minutes):
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    value = max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, value))
    set_meta_json(INTERVAL_KEY, value)
    return value


# ── read-only source call ───────────────────────────────────────────────

def _call_tool(integration, tool_name, args):
    """Run one integration read, gated by the §2.6a read policy.

    Returns the parsed payload. Raises SourceError when the tool is missing,
    the policy refuses it, or the upstream call fails.
    """
    tool = get_tool(integration['name'], tool_name)
    if not tool or not tool.get('enabled'):
        raise SourceError('tool_missing:%s' % tool_name)
    transport = tool.get('transport') or 'http'
    ok, why = is_read_integration_call(tool, transport, args)
    if not ok:
        raise SourceError('not_a_read:%s' % why)
    try:
        if tool.get('handler'):
            out = run_handler(tool['handler'], integration, tool, args or {},
                              agent='inventory')
            try:
                data = json.loads(out)
            except ValueError:
                raise SourceError('bad_json')
            if isinstance(data, dict) and data.get('error'):
                raise SourceError(str(data.get('error')))
            return data
        if transport == 'ws':
            res = execute_ws_call(
                integration, tool.get('path_template') or (args or {}).get('command'),
                (args or {}).get('payload'), agent='inventory',
                tool_name=tool_name)
        else:
            res = execute_integration_call(integration, tool, args or {},
                                           agent='inventory')
        if res.get('error'):
            raise SourceError(str(res['error']))
        try:
            return json.loads(res.get('body') or 'null')
        except ValueError:
            raise SourceError('bad_json')
    except ProxyError as exc:
        raise SourceError(str(exc.message or ''))
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - a source must never crash a refresh
        raise SourceError('%s: %s' % (type(exc).__name__, exc))


def allowed_sources(agent=None):
    """The sources this caller may read, or None for unrestricted (operator).

    Eshu's own gateway rows are always allowed. An integration's rows are
    allowed only when the agent's policy permits at least one enabled read
    tool on that integration — the caller's policy decides, never the
    inventory's own opinion.
    """
    if agent is None:
        return None
    from core.agent_policy import evaluate_call
    allowed = {'eshu'}
    for integration in get_integrations():
        if not integration.get('enabled'):
            continue
        for tool in get_tools(integration['id']):
            if not tool.get('enabled') or not tool.get('read_only'):
                continue
            ok, _why = evaluate_call(agent, integration['name'], tool['name'],
                                     {}, tool=tool, integration=integration)
            if ok:
                allowed.add(integration['name'])
                break
    return allowed


def _integrations(kind):
    return [i for i in get_integrations(include_secret=True)
            if (i.get('kind') or '').lower() == kind and i.get('enabled')]


def _as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('rows', 'items', 'data', 'result'):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _looks_ip(value):
    return bool(re.match(r'^\d{1,3}(\.\d{1,3}){3}$', str(value or '')))


# ── sources ─────────────────────────────────────────────────────────────

def _eshu_source():
    items = []
    for gateway in get_gateways():
        ip = gateway.get('ip') or ''
        items.append({
            'kind': 'gateway', 'key': ip,
            'name': gateway.get('hostname') or ip,
            'ips': [ip] if ip else [],
            'host': gateway.get('hostname') or '',
            'extra': {},
        })
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT target_ip, inner_target, COUNT(*) AS n FROM requests "
                "WHERE inner_target IS NOT NULL AND inner_target != '' "
                "AND inner_target != 'multi' "
                "GROUP BY target_ip, inner_target LIMIT 500").fetchall()
    except Exception:  # noqa: BLE001 - the inventory is best-effort metadata
        rows = []
    for row in rows:
        kind, _sep, name = (row['inner_target'] or '').partition(':')
        items.append({
            'kind': 'guest', 'key': 'inner:%s:%s' % (row['target_ip'], row['inner_target']),
            'name': name or row['inner_target'],
            'ips': [], 'host': row['target_ip'],
            'vmid': name if kind in ('pct', 'qm') else '',
            'extra': {'inner_target': row['inner_target'],
                      'requests': int(row['n'] or 0)},
        })
    return [('eshu', 'eshu', items)], []


def _proxmox_source():
    groups, failures = [], []
    for integration in _integrations('proxmox'):
        try:
            data = _call_tool(integration, 'get_cluster_resources', {})
        except SourceError as exc:
            failures.append((integration['name'], 'proxmox', str(exc)))
            continue
        items = []
        for row in _as_list(data):
            if not isinstance(row, dict):
                continue
            rtype = str(row.get('type') or '')
            if rtype not in ('qemu', 'lxc', 'vm', 'container'):
                continue
            vmid = str(row.get('vmid') or '')
            name = str(row.get('name') or '').strip() or '%s %s' % (rtype, vmid)
            items.append({
                'kind': 'guest',
                'key': '%s/%s' % (rtype, vmid or name),
                'name': name,
                'ips': [row['ip']] if row.get('ip') else [],
                'host': str(row.get('node') or ''),
                'vmid': vmid,
                'extra': {'type': rtype, 'status': row.get('status') or ''},
            })
        groups.append((integration['name'], 'proxmox', items))
    return groups, failures


def _npm_source():
    groups, failures = [], []
    for integration in _integrations('npm'):
        try:
            data = _call_tool(integration, 'proxy_hosts', {})
        except SourceError as exc:
            failures.append((integration['name'], 'npm', str(exc)))
            continue
        items = []
        for row in _as_list(data):
            if not isinstance(row, dict):
                continue
            domains = [str(d) for d in (row.get('domain_names') or []) if d]
            fwd = str(row.get('forward_host') or '')
            port = row.get('forward_port')
            scheme = str(row.get('forward_scheme') or 'http')
            url = ''
            if fwd:
                url = '%s://%s%s' % (scheme, fwd, ':%s' % port if port else '')
            items.append({
                'kind': 'proxy', 'key': str(row.get('id') or (domains[0] if domains else '')),
                'name': domains[0] if domains else 'proxy %s' % row.get('id'),
                'ips': [fwd] if _looks_ip(fwd) else [],
                'fqdns': domains, 'host': fwd, 'url': url,
                'extra': {'enabled': bool(row.get('enabled')),
                          'nginx_online': row.get('nginx_online')},
            })
        groups.append((integration['name'], 'npm', items))
    return groups, failures


def _pihole_source():
    groups, failures = [], []
    for integration in _integrations('pihole'):
        try:
            data = _call_tool(integration, 'local_dns_records', {})
        except SourceError as exc:
            failures.append((integration['name'], 'pihole', str(exc)))
            continue
        if not isinstance(data, dict):
            data = {}
        items = []
        for record in data.get('hosts') or []:
            if not isinstance(record, dict) or not record.get('name'):
                continue
            name = str(record['name'])
            items.append({
                'kind': 'dns', 'key': 'host:%s' % name, 'name': name,
                'ips': [str(ip) for ip in (record.get('ips') or []) if ip],
                'fqdns': [name],
                'extra': {'record': 'host'},
            })
        for record in data.get('cnames') or []:
            if not isinstance(record, dict) or not record.get('name'):
                continue
            name = str(record['name'])
            items.append({
                'kind': 'dns', 'key': 'cname:%s' % name, 'name': name,
                'ips': [], 'fqdns': [name],
                'extra': {'record': 'cname',
                          'target': record.get('target') or ''},
            })
        groups.append((integration['name'], 'pihole', items))
    return groups, failures


def _omada_source():
    groups, failures = [], []
    for integration in _integrations('omada'):
        try:
            sites = _as_list(_call_tool(integration, 'list_sites', {}))
        except SourceError as exc:
            failures.append((integration['name'], 'omada', str(exc)))
            continue
        items = []
        for site in sites[:10]:
            if not isinstance(site, dict) or not site.get('id'):
                continue
            try:
                data = _call_tool(integration, 'list_known_clients',
                                  {'siteId': site['id'], 'pageSize': 500})
            except SourceError as exc:
                failures.append((integration['name'], 'omada', str(exc)))
                continue
            for row in _as_list(data):
                if not isinstance(row, dict):
                    continue
                mac = str(row.get('mac') or '')
                name = str(row.get('name') or row.get('hostName') or mac)
                if not name:
                    continue
                items.append({
                    'kind': 'client', 'key': mac or name,
                    'name': name,
                    'ips': [str(row['ip'])] if row.get('ip') else [],
                    'host': '',
                    'extra': {'mac': mac, 'active': row.get('active'),
                              'last_seen': row.get('lastSeen'),
                              'site': site.get('name') or ''},
                })
        groups.append((integration['name'], 'omada', items))
    return groups, failures


SOURCES = (_eshu_source, _proxmox_source, _npm_source, _pihole_source,
           _omada_source)


# ── refresh ─────────────────────────────────────────────────────────────

def refresh_inventory() -> dict:
    """Read every configured source and swap its rows. Never raises."""
    started = time.time()
    touched = 0
    with _LOCK:
        for adapter in SOURCES:
            try:
                groups, failures = adapter()
            except Exception as exc:  # noqa: BLE001 - one source cannot stop the rest
                groups, failures = [], [('inventory', 'eshu',
                                         '%s: %s' % (type(exc).__name__, exc))]
            for source, label, items in groups:
                replace_source_items(source, items)
                record_source(source, label, True, count=len(items))
                touched += 1
            for source, label, reason in failures:
                record_source(source, label, False, reason=reason)
                touched += 1
    return {
        'at': int(started),
        'duration_ms': int((time.time() - started) * 1000),
        'sources': touched,
        'interval_minutes': interval_minutes(),
        'refreshed': _last_refresh(),
    }


def _last_refresh():
    rows = get_inventory_sources()
    return max([row['last_ok'] or 0 for row in rows] or [0])


def stale_sources(now=None):
    """{source: staleness}. A source is stale when its last refresh failed or
    when it has not succeeded for two intervals."""
    now = int(now or time.time())
    horizon = interval_minutes() * 60 * 2
    out = {}
    for row in get_inventory_sources():
        stale = bool(row.get('last_error')) or (
            row.get('last_ok') and now - row['last_ok'] > horizon)
        out[row['source']] = {
            'label': row.get('label') or row['source'],
            'stale': bool(stale),
            'last_ok': int(row.get('last_ok') or 0),
            'error': row.get('last_error') or '',
            'items': int(row.get('item_count') or 0),
        }
    return out


def _inventory_loop():
    # Sleep first: a boot must not fire source calls while the dashboard starts.
    while not _STOP.is_set():
        _STOP.wait(max(60, interval_minutes() * 60))
        if _STOP.is_set():
            break
        refresh_inventory()


def start_inventory_loop():
    thread = threading.Thread(target=_inventory_loop, daemon=True,
                              name='inventory-refresh')
    thread.start()
    return thread


def stop_inventory_loop():
    _STOP.set()


# ── join ────────────────────────────────────────────────────────────────

def _norm(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')


def _join_keys(item):
    for ip in item.get('ips') or []:
        yield 'ip:' + ip
    for name in [item.get('name')] + list(item.get('fqdns') or []):
        norm = _norm(name)
        if not norm:
            continue
        yield 'name:' + norm
        if '.' in norm:
            label = norm.split('.')[0]
            if len(label) >= 3:
                yield 'name:' + label
    host = _norm(item.get('host'))
    vmid = str(item.get('vmid') or '')
    if host and vmid:
        yield 'vmid:%s:%s' % (host, vmid)


def build_entities(items=None, stale=None, now=None, allowed=None):
    """Join the cached rows into entities (IP first, then normalised name).

    `allowed` is a set of source names the caller may read (None = all);
    filtered *before* the join, so an entity never carries fields from a
    source the caller cannot see.
    """
    items = list(get_inventory_items() if items is None else items)
    if allowed is not None:
        items = [item for item in items if item.get('source') in allowed]
    stale = stale_sources(now) if stale is None else stale
    parent = list(range(len(items)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    seen = {}
    for index, item in enumerate(items):
        for key in _join_keys(item):
            if key in seen:
                union(seen[key], index)
            else:
                seen[key] = index

    grouped = {}
    for index, item in enumerate(items):
        grouped.setdefault(find(index), []).append(item)

    return [_entity(members, stale) for members in grouped.values()]


def _entity(members, stale):
    ordered = sorted(members, key=lambda item: (
        _NAME_ORDER.get(item.get('kind'), 9), item.get('name') or ''))
    name = _clean(ordered[0].get('name') or ordered[0].get('key') or '')
    ips, fqdns, hosts, vmids, urls = [], [], [], [], []
    for item in ordered:
        for ip in item.get('ips') or []:
            if ip not in ips:
                ips.append(_clean(ip))
        for fqdn in item.get('fqdns') or []:
            if fqdn not in fqdns:
                fqdns.append(_clean(fqdn))
        if item.get('host') and item['host'] not in hosts:
            hosts.append(_clean(item['host']))
        if item.get('vmid') and item['vmid'] not in vmids:
            vmids.append(_clean(item['vmid']))
        if item.get('url') and item['url'] not in urls:
            urls.append(_clean(item['url']))
    sources, kinds, extra = [], [], {}
    for item in ordered:
        if item['source'] not in sources:
            sources.append(item['source'])
        if item.get('kind') not in kinds:
            kinds.append(item.get('kind'))
        for key, value in (item.get('extra') or {}).items():
            if value not in (None, '', []) and key not in extra:
                extra[key] = _clean(value) if isinstance(value, str) else value
    source_rows = []
    for source in sources:
        state = stale.get(source) or {}
        source_rows.append({
            'source': source,
            'label': state.get('label') or source,
            'stale': bool(state.get('stale')),
        })
    conflicts = []
    for ip in ips:
        names = sorted({(item.get('name') or '') for item in ordered
                        if ip in (item.get('ips') or []) and item.get('name')})
        if len(names) > 1:
            conflicts.append({'ip': ip, 'names': names})
    return {
        'name': name,
        'ips': ips,
        'fqdns': fqdns,
        'hosts': hosts,
        'vmids': vmids,
        'urls': urls,
        'sources': source_rows,
        'kinds': kinds,
        'conflicts': conflicts,
        'extra': extra,
        'stale': any(row['stale'] for row in source_rows) if source_rows else False,
    }


# ── queries ─────────────────────────────────────────────────────────────

def _matches(entity, needle):
    fields = [entity.get('name') or '']
    fields.extend(entity.get('ips') or [])
    fields.extend(entity.get('fqdns') or [])
    fields.extend(entity.get('vmids') or [])
    fields.extend(entity.get('hosts') or [])
    fields.extend(entity.get('urls') or [])
    extra = entity.get('extra') or {}
    fields.append(str(extra.get('mac') or ''))
    fields.append(str(extra.get('inner_target') or ''))
    values = [str(value).lower() for value in fields if value]
    return any(needle == value for value in values), \
        any(needle in value for value in values)


def entities_matching(query, limit=25, items=None, allowed=None):
    """Exact matches first, then substrings, stable by name."""
    needle = str(query or '').strip().lower()
    entities = build_entities(items, allowed=allowed)
    if not needle:
        return entities[:limit]
    exact, partial = [], []
    for entity in entities:
        is_exact, is_partial = _matches(entity, needle)
        if is_exact:
            exact.append(entity)
        elif is_partial:
            partial.append(entity)
    ordered = sorted(exact, key=lambda e: e['name']) + \
        sorted(partial, key=lambda e: e['name'])
    return ordered[:limit]


def _trim(entity, max_chars=WHEREIS_MAX_CHARS):
    """Serialise one entity under a character budget, dropping the least
    important fields first and flagging `truncated`."""
    payload = dict(entity)
    payload['truncated'] = False
    def size(p):
        return len(json.dumps(p, separators=(',', ':')))
    for field in ('extra', 'urls', 'vmids', 'hosts', 'conflicts', 'fqdns', 'ips'):
        if size(payload) <= max_chars:
            break
        payload[field] = [] if isinstance(payload.get(field), list) else {}
        payload['truncated'] = True
    if size(payload) > max_chars:
        payload['name'] = (payload.get('name') or '')[:80]
        payload['sources'] = payload.get('sources') or []
    return payload


def whereis(query, agent=None):
    """The MCP answer: up to 5 entities, each under 600 characters.

    Goes through the caller's agent policy: the tool itself must be in the
    allowlist, and only the sources the agent may read are joined. The call is
    audited with the agent, the query length (never the query) and the result
    count.
    """
    if agent is None:
        agent = get_current_agent()
    if agent is not None:
        from core.agent_policy import visible_tool_allowed
        if not visible_tool_allowed(agent, 'whereis'):
            return {'error': 'forbidden',
                    'reason': "tool 'whereis' is not in this agent's allowlist",
                    'policy': agent.get('name') or ''}
    allowed = allowed_sources(agent)
    matches = entities_matching(query, limit=WHEREIS_MAX, allowed=allowed)
    rows = [_trim(entity) for entity in matches]
    if agent is not None:
        record_audit_event(
            'whereis_query',
            details=f"whereis by '{agent.get('name') or 'agent'}': "
                    f"query_len={len(str(query or ''))} entities={len(rows)}",
            actor=agent.get('name') or '')
    return {
        'query': str(query or ''),
        'entities': rows,
        'count': len(rows),
    }


def inventory_snapshot(query='', limit=25):
    return {
        'items': entities_matching(query, limit=limit),
        'sources': stale_sources(),
        'interval_minutes': interval_minutes(),
        'refreshed_at': _last_refresh(),
    }
