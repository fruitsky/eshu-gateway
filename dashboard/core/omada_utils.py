"""Omada-specific helper behaviors for the generic passthrough path.

Omada list endpoints require `page`/`pageSize` (they 400 without them), and
ACL-create responses omit the created rule's id (agents must re-list to find
it). These helpers are wired into `execute_generic_call` *only* for Omada-kind
integrations, so the shared generic read/write floor stays unchanged for every
other integration.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# List endpoints that 400 without page/pageSize. Suffix-matched against the
# request path (Omada paths are relative to /openapi/v1/<omadacId>).
OMADA_PAGINATED_SUFFIXES = (
    '/acls/osw-acls',
    '/acls/osg-acls',
    '/setting/service/mdns',
    '/setting/service/dhcp',
    '/lan-networks',
    '/profiles/bonjour-service',
    '/profiles/groups',
    '/devices',
    '/clients',
)

OMADA_ACL_CREATE_SUFFIXES = ('/acls/osw-acls', '/acls/osg-acls')

PAGE_SIZE = 50


def inject_omada_pagination(method: str, path: str, params: dict) -> dict:
    """Return a params dict with page/pageSize injected when an Omada GET hits a
    paginated list endpoint and the caller didn't supply them. Never overrides
    explicit params."""
    if (method or '').upper() != 'GET':
        return params
    p = path.rstrip('/')
    if not any(p.endswith(s) for s in OMADA_PAGINATED_SUFFIXES):
        return params
    if not params:
        params = {}
    if 'page' not in params and 'pageSize' not in params:
        params = dict(params)
        params['page'] = 1
        params['pageSize'] = PAGE_SIZE
    return params


def inject_omada_pagination_qs(method: str, path: str, query_string: str) -> str:
    """Inject page/pageSize into an existing query string for a paginated Omada
    GET, preserving the caller's explicit params. Used by the curated-tool path
    (execute_integration_call), which builds the query string up-front."""
    if (method or '').upper() != 'GET':
        return query_string
    p = path.rstrip('/')
    if not any(p.endswith(s) for s in OMADA_PAGINATED_SUFFIXES):
        return query_string
    parsed = urllib.parse.parse_qsl(query_string) if query_string else []
    keys = {k for k, _ in parsed}
    if 'page' in keys or 'pageSize' in keys:
        return query_string
    parts = list(parsed) + [('page', '1'), ('pageSize', str(PAGE_SIZE))]
    return urllib.parse.urlencode(parts)


def _base_url(integration, version: str = 'v1') -> str:
    """The integration's base URL, optionally swapped to another Open API
    version (the stored base is always /openapi/v1/<omadacId>)."""
    base_url = (integration.get('base_url') or '').rstrip('/')
    if version and version != 'v1':
        base_url = base_url.replace('/openapi/v1/', '/openapi/%s/' % version)
    return base_url


def _fetch_json(integration, path: str, params: dict = None, version: str = 'v1'):
    """Small GET against the integration's base URL. Raises on failure."""
    from core.integration_proxy import (
        DEFAULT_TIMEOUT, MAX_BODY_BYTES, _auth_headers, _guard_ssrf, _ssl_context,
    )
    path = (path or '').lstrip('/')
    base_url = _base_url(integration, version)
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method='GET', headers=_auth_headers(integration))
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


def _send_json(integration, path: str, payload: dict, method: str = 'POST',
               version: str = 'v1'):
    """Small JSON request against the integration's base URL. Raises on failure."""
    from core.integration_proxy import (
        DEFAULT_TIMEOUT, MAX_BODY_BYTES, _auth_headers, _guard_ssrf, _ssl_context,
    )
    path = (path or '').lstrip('/')
    base_url = _base_url(integration, version)
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method=(method or 'POST').upper(),
        headers={'Content-Type': 'application/json', **(_auth_headers(integration))})
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


def _post_json(integration, path: str, payload: dict, version: str = 'v1'):
    """Small JSON POST against the integration's base URL. Raises on failure."""
    return _send_json(integration, path, payload, 'POST', version)


def _patch_json(integration, path: str, payload: dict):
    """Small JSON PATCH against the integration's base URL. Raises on failure."""
    return _send_json(integration, path, payload, 'PATCH')


def reorder_acls(integration, site_id: str, acl_type: str, rule_id: str,
                 before_rule_id: str):
    """Reorder an Omada ACL: fetch the list, validate both ids, rebuild the
    full contiguous modifyIndex map with rule_id moved immediately before
    before_rule_id, and POST it. Returns a compact result dict.

    Raises ValueError with a stable message on invalid input (the caller maps
    it to `invalid_request`) and on upstream -1001 (whole-call rejection).
    """
    if acl_type not in ('switch', 'gateway'):
        raise ValueError(f"invalid acl_type: {acl_type}")
    if rule_id == before_rule_id:
        raise ValueError("rule cannot be moved before itself")
    acl_path = '/acls/osw-acls' if acl_type == 'switch' else '/acls/osg-acls'
    listed = _fetch_json(integration, f"/sites/{site_id}{acl_path}",
                         {'page': 1, 'pageSize': 200})
    items = (listed.get('result') or {}).get('data') if isinstance(listed, dict) else None
    if not isinstance(items, list):
        raise ValueError("ACL list returned no data")
    order = []
    for it in items:
        if isinstance(it, dict) and it.get('id'):
            order.append({'id': it['id'], 'description': it.get('description')})
    ids = [o['id'] for o in order]
    if rule_id not in ids:
        raise ValueError(f"ruleId not found: {rule_id}")
    if before_rule_id not in ids:
        raise ValueError(f"beforeRuleId not found: {before_rule_id}")

    new_order = [o for o in order if o['id'] != rule_id]
    insert_at = next(i for i, o in enumerate(new_order) if o['id'] == before_rule_id)
    moved = next(o for o in order if o['id'] == rule_id)
    new_order.insert(insert_at, moved)

    indexes = {o['id']: i + 1 for i, o in enumerate(new_order)}
    resp = _post_json(integration, f"/sites/{site_id}/acls/modifyIndex",
                      {'type': acl_type, 'indexes': indexes})
    if isinstance(resp, dict) and resp.get('errorCode') not in (0, None):
        msg = resp.get('msg') or f"errorCode {resp.get('errorCode')}"
        raise ValueError(f"reorder rejected upstream: {msg}")

    return {
        'moved_rule': {'id': rule_id, 'index': indexes[rule_id]},
        'order': [{'index': o['id'] and (i + 1), 'id': o['id'],
                   'description': o['description']}
                  for i, o in enumerate(new_order)],
    }


def _matches(body: dict, submitted) -> bool:
    """Fingerprint: the created rule matches the submitted body on the identity
    fields. Returns True when every present field agrees (conservative — never
    guess on ambiguity)."""
    if not isinstance(body, dict) or not isinstance(submitted, dict):
        return False
    for key in ('sourceIds', 'destinationIds', 'protocols', 'policy',
                'sourceType', 'destinationType', 'bindingType'):
        if key not in submitted:
            continue
        if body.get(key) != submitted.get(key):
            return False
    return True


def enrich_acl_create(integration, path: str, submitted: dict, body: str):
    """After a successful ACL create, find the new rule's id+index and append
    `created_rule` to the response body. Returns the (possibly enriched) body.
    Returns the body unchanged on any non-list / ambiguous / error outcome —
    never guesses."""
    try:
        data = json.loads(body) if body else {}
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict) or data.get('errorCode') not in (0, None):
        return body

    acl_path = '/acls/osw-acls' if path.rstrip('/').endswith('/acls/osw-acls') else '/acls/osg-acls'
    list_path = path.rsplit('/acls/', 1)[0] + acl_path
    try:
        listed = _fetch_json(integration, list_path, {'page': 1, 'pageSize': PAGE_SIZE})
    except Exception:
        return body
    items = (listed.get('result') or {}).get('data') if isinstance(listed, dict) else None
    if not isinstance(items, list):
        return body

    submitted = submitted or {}
    description = submitted.get('description')
    matches = [it for it in items
               if isinstance(it, dict) and it.get('description') == description]
    if len(matches) == 1:
        created = matches[0]
    elif len(matches) > 1:
        created = next((it for it in matches if _matches(it, submitted)), None)
        if created is None:
            # ambiguous — several same-description rules, none matching the body
            return body
    else:
        return body

    if not isinstance(data, dict):
        data = {}
    data['created_rule'] = {'id': created.get('id'), 'index': created.get('index')}
    return json.dumps(data)


# ── Profile-group membership ────────────────────────────────────────────
#
# Omada's group PATCH is a FULL `ipList` replace — omitting an existing member
# silently drops it. These helpers implement the safe read → merge → PATCH →
# re-read flow so agents express intent ("add these IPs") instead of
# hand-building the whole member list.

# Group types whose members live in `ipList` (0=IP, 1=IP-Port). Other types
# (2=MAC, 3=IPv6, 5=Country, 7=Domain) carry a different shape and are out of
# scope for the curated membership tools.
IP_GROUP_TYPES = (0, 1)

# Upstream Omada error code for "the number of IpGroups has reached the limit".
OMADA_IP_GROUP_QUOTA = -33724


def _omada_group_items(listed):
    """Extract the group array from a /profiles/groups response. Unlike ACL
    lists (which nest the array under `result.data`), the group list wraps the
    array directly under `result`. Returns None when neither shape is present."""
    if not isinstance(listed, dict):
        return None
    result = listed.get('result')
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and isinstance(result.get('data'), list):
        return result['data']
    return None


def _group_projection(group: dict) -> dict:
    """Compact projection of one Omada profile group. Members are included when
    the group carries an `ipList` (IP / IP-Port types); other types report the
    count only."""
    out = {}
    for key in ('groupId', 'name', 'type', 'count'):
        if group.get(key) is not None:
            out[key] = group[key]
    if group.get('buildIn'):
        out['buildIn'] = True
    members = group.get('ipList')
    if isinstance(members, list):
        out['members'] = [
            {k: v for k, v in (('ip', m.get('ip')), ('mask', m.get('mask')),
                               ('description', m.get('description')))
             if v is not None}
            for m in members if isinstance(m, dict) and m.get('ip')
        ]
    return out


def list_groups(integration, site_id: str):
    """Fetch and project every profile group for a site. Raises ValueError on an
    empty/unexpected response."""
    listed = _fetch_json(integration, f"/sites/{site_id}/profiles/groups",
                         {'page': 1, 'pageSize': 200})
    items = _omada_group_items(listed)
    if items is None:
        raise ValueError("group list returned no data")
    return [_group_projection(g) for g in items
            if isinstance(g, dict) and g.get('groupId')]


def _find_group(integration, site_id: str, group_id: str):
    """Return the raw group dict for `group_id`, or None when not found."""
    listed = _fetch_json(integration, f"/sites/{site_id}/profiles/groups",
                         {'page': 1, 'pageSize': 200})
    items = _omada_group_items(listed)
    if items is None:
        raise ValueError("group list returned no data")
    for g in items:
        if isinstance(g, dict) and g.get('groupId') == group_id:
            return g
    return None


def _member_ip(entry):
    """Normalize an add/remove entry to an (ip, mask, description) triple.
    Accepts a dict ({ip, mask?, description?}) or a bare IP string."""
    if isinstance(entry, dict):
        ip = entry.get('ip')
        if ip is None:
            return None
        return str(ip).strip(), entry.get('mask'), entry.get('description')
    if isinstance(entry, str) and entry.strip():
        return entry.strip(), None, None
    return None


def _upstream_group_error(resp) -> str:
    """Readable message for a non-zero Omada errorCode, mapping the IP-group
    quota to something an operator can act on."""
    code = resp.get('errorCode')
    msg = resp.get('msg') or f"errorCode {code}"
    if code == OMADA_IP_GROUP_QUOTA:
        return f"IP group quota reached upstream: {msg}"
    return msg


def update_group_members(integration, site_id: str, group_id: str,
                         add=None, remove=None):
    """Read → merge → PATCH a group's `ipList`, then re-read and return the
    updated projection.

    Omada's group PATCH replaces the WHOLE `ipList`, so this reads the current
    members first, applies the caller's add/remove by IP (preserving every other
    existing member and its description), and PATCHes the merged list. The
    group's `type` is derived from the existing group — never caller-supplied —
    and is used for the item route. Raises ValueError with a stable message on
    invalid input or an upstream rejection."""
    if not site_id or not group_id:
        raise ValueError("siteId and groupId are required")
    adds = [t for t in (_member_ip(m) for m in (add or [])) if t]
    remove_ips = {t[0].lower() for t in (_member_ip(m) for m in (remove or [])) if t}
    if not adds and not remove_ips:
        raise ValueError("nothing to change: provide members to add or remove")

    group = _find_group(integration, site_id, group_id)
    if group is None:
        raise ValueError(f"group not found: {group_id}")
    gtype = group.get('type')
    if gtype not in IP_GROUP_TYPES:
        raise ValueError(
            f"group type {gtype} is not an IP or IP-Port group; membership can "
            "only be edited on type 0 (IP) or type 1 (IP-Port)")

    by_ip = {}
    for m in (group.get('ipList') or []):
        if isinstance(m, dict) and m.get('ip'):
            by_ip[str(m['ip']).strip().lower()] = dict(m)
    for ip in remove_ips:
        by_ip.pop(ip, None)
    for ip, mask, description in adds:
        key = ip.lower()
        entry = {'ip': ip, 'mask': 32 if mask is None else mask}
        if description is not None:
            entry['description'] = description
        elif by_ip.get(key, {}).get('description'):
            entry['description'] = by_ip[key]['description']
        by_ip[key] = entry

    payload = {'name': group.get('name'), 'type': gtype,
               'ipList': list(by_ip.values())}
    resp = _patch_json(
        integration, f"/sites/{site_id}/profiles/groups/{gtype}/{group_id}", payload)
    if isinstance(resp, dict) and resp.get('errorCode') not in (0, None):
        raise ValueError(f"update rejected upstream: {_upstream_group_error(resp)}")

    updated = _find_group(integration, site_id, group_id)
    if updated is None:
        # Fall back to the merged view we just wrote (re-read raced or the
        # controller lagged) rather than reporting failure.
        updated = {**group, 'ipList': list(by_ip.values()),
                   'count': len(by_ip)}
    return _group_projection(updated)


# ── Read completeness (known clients, networks, ACLs, DHCP, devices, events) ─
#
# These back the curated read tools added for the 2026-09-26 "read
# completeness" pass. Each returns a JSON-ready dict (never a bare {}) and
# reports its own completeness via totalRows/returned/truncated. Errors are
# raised as ValueError with a stable message and mapped to typed errors by the
# transforms in core.transforms.

# Omada ACL source/destination type codes → human labels. 0 = network,
# 1 = IP group; 2 is a MAC/other group on this controller (best-effort).
ACL_SOURCE_TYPES = {0: 'network', 1: 'ip-group', 2: 'mac-group'}


def omada_error(code: str, message: str, upstream=None) -> str:
    """Typed tool error: {"error": {"code", "message", "upstream"?}}."""
    err = {'code': code, 'message': message}
    if upstream is not None:
        err['upstream'] = upstream
    return json.dumps({'error': err})


def _result_grid(body):
    """Extract (rows, totalRows) from an Omada grid envelope. Returns
    (None, None) when the shape is neither a grid nor a bare list."""
    if not isinstance(body, dict):
        return None, None
    result = body.get('result')
    if isinstance(result, dict) and isinstance(result.get('data'), list):
        total = result.get('totalRows')
        return result['data'], (total if isinstance(total, int) else None)
    if isinstance(result, list):
        return result, len(result)
    return None, None


def fetch_all(integration, path: str, params: dict = None, page_size: int = 500,
              max_pages: int = 20, version: str = 'v1'):
    """GET a paginated Omada grid and concatenate every page. Stops when a page
    is short or totalRows is reached. Returns (rows, totalRows|None)."""
    rows = []
    page = 1
    total = None
    while page <= max_pages:
        p = dict(params or {})
        p['page'] = page
        p['pageSize'] = page_size
        body = _fetch_json(integration, path, p, version=version)
        data, total = _result_grid(body)
        if data is None:
            raise ValueError('list returned no data')
        rows.extend(data)
        if len(data) < page_size:
            break
        if total is not None and len(rows) >= total:
            break
        page += 1
    return rows, total


def _paginate(items: list, page, page_size: int = 50):
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, int(page_size))
    except (TypeError, ValueError):
        page_size = 50
    start = (page - 1) * page_size
    return items[start:start + page_size]


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
        .encode('utf-8')).hexdigest()


def _norm_mac(value) -> str:
    return str(value or '').strip().upper().replace(':', '-')


def _has(needle: str, *values) -> bool:
    if not needle:
        return True
    hay = ' '.join(str(v or '') for v in values).lower()
    return needle in hay


# ── T1: known clients (all, including offline) ──────────────────────────

_KNOWN_CLIENT_FIELDS = (
    'mac', 'name', 'hostName', 'vendor', 'deviceType', 'deviceCategory',
    'wireless', 'active', 'lastSeen', 'ip', 'vid', 'networkName', 'ssid',
    'apName', 'connectDevType', 'blocked', 'guest',
)


def list_known_clients(integration, site_id: str, search: str = '',
                       active=None, wireless=None, page=1, page_size: int = 50,
                       max_rows: int = 1000):
    """Enumerate every client the controller knows (v2 POST `scope: 0` — all:
    online + offline + blocked), with presence/address facts. Search matches
    name, hostName, MAC, vendor and device type; results are sorted active
    first, then lastSeen desc. Fixed-address detail (useFixedAddr) is not in
    this endpoint — use list_dhcp_reservations or get_client for that."""
    if not site_id:
        raise ValueError('siteId is required')
    rows = []
    page_no = 1
    total = None
    while page_no <= 20:
        body = _post_json(
            integration, f"/sites/{site_id}/clients",
            {'page': page_no, 'pageSize': max_rows, 'scope': 0,
             'sorts': {'lastSeen': 'desc'}},
            version='v2')
        data, total = _result_grid(body)
        if data is None:
            raise ValueError('client list returned no data')
        rows.extend(data)
        if len(data) < max_rows or (total is not None and len(rows) >= total):
            break
        page_no += 1
    if total is None:
        total = len(rows)

    needle = str(search or '').strip().lower()
    matched = []
    for c in rows:
        if not _has(needle, c.get('name'), c.get('hostName'), c.get('mac'),
                    c.get('vendor'), c.get('deviceType'), c.get('deviceCategory')):
            continue
        if active is not None and bool(c.get('active')) != bool(active):
            continue
        if wireless is not None and bool(c.get('wireless')) != bool(wireless):
            continue
        matched.append(c)
    matched.sort(key=lambda c: (0 if c.get('active') else 1,
                                -(c.get('lastSeen') or 0)))

    out = []
    for c in _paginate(matched, page, page_size):
        out.append({k: c.get(k) for k in _KNOWN_CLIENT_FIELDS
                    if c.get(k) is not None})
    return {'totalRows': total, 'matched': len(matched),
            'returned': len(out), 'truncated': len(out) < total,
            'rows': out}


# ── T2: LAN networks (id ↔ name ↔ VLAN ↔ subnet) ────────────────────────

def list_networks(integration, site_id: str):
    """All LAN networks with VLAN id, subnet, gateway, DHCP state and domain."""
    if not site_id:
        raise ValueError('siteId is required')
    rows, total = fetch_all(integration, f"/sites/{site_id}/lan-networks",
                            page_size=200)
    out = []
    for n in rows:
        dhcp = n.get('dhcpSettingsVO') if isinstance(n.get('dhcpSettingsVO'), dict) else {}
        out.append({k: v for k, v in {
            'id': n.get('id'), 'name': n.get('name'), 'vid': n.get('vlan'),
            'subnet': n.get('gatewaySubnet'), 'purpose': n.get('purpose'),
            'dhcpEnabled': dhcp.get('enable'), 'gateway': dhcp.get('gateway'),
            'domain': n.get('domain'), 'primary': n.get('primary'),
        }.items() if v is not None})
    total = total if total is not None else len(out)
    return {'totalRows': total, 'returned': len(out),
            'truncated': len(out) < total, 'rows': out}


# ── T3: ACLs (both layers, evaluation order, resolved names) ────────────

def _acl_name_maps(integration, site_id: str):
    """{id: name} for networks and profile groups, best-effort (resolution is a
    convenience — raw ids are always retained)."""
    nets, groups = {}, {}
    try:
        for n in list_networks(integration, site_id)['rows']:
            if n.get('id'):
                nets[n['id']] = n.get('name')
    except Exception:
        pass
    try:
        for g in list_groups(integration, site_id):
            if g.get('groupId'):
                groups[g['groupId']] = g.get('name')
    except Exception:
        pass
    return nets, groups


def _resolve_acl_ids(ids, stype, nets, groups):
    out = []
    for i in ids or []:
        if stype == 0:
            out.append(nets.get(i, i))
        elif stype in (1, 2):
            out.append(groups.get(i, i))
        else:
            out.append(i)
    return out


def _acl_row(r, nets, groups):
    stype, dtype = r.get('sourceType'), r.get('destinationType')
    return {k: v for k, v in {
        'index': r.get('index'), 'id': r.get('id'),
        'name': r.get('description'),
        'action': 'allow' if r.get('policy') == 1 else 'deny',
        'policy': r.get('policy'), 'status': r.get('status'),
        'protocols': r.get('protocols'),
        'srcType': ACL_SOURCE_TYPES.get(stype, stype),
        'srcIds': r.get('sourceIds'),
        'src': _resolve_acl_ids(r.get('sourceIds'), stype, nets, groups),
        'dstType': ACL_SOURCE_TYPES.get(dtype, dtype),
        'dstIds': r.get('destinationIds'),
        'dst': _resolve_acl_ids(r.get('destinationIds'), dtype, nets, groups),
        'direction': r.get('direction'), 'bindingType': r.get('bindingType'),
        'networkId': r.get('networkId'), 'etherType': r.get('etherType'),
        'customAclPorts': r.get('customAclPorts'),
    }.items() if v is not None}


def list_acls(integration, site_id: str, layer: str = 'both'):
    """Read gateway and/or switch ACLs in evaluation order, with ids resolved to
    network / group names. Emits a normalized block (stable per-rule hashes +
    ordered id list + list hash) so a reorder can be diffed."""
    if not site_id:
        raise ValueError('siteId is required')
    layer = (layer or 'both').lower()
    if layer not in ('gateway', 'switch', 'both'):
        raise ValueError("layer must be gateway, switch or both")
    wanted = []
    if layer in ('gateway', 'both'):
        wanted.append(('gateway', f"/sites/{site_id}/acls/osg-acls"))
    if layer in ('switch', 'both'):
        wanted.append(('switch', f"/sites/{site_id}/acls/osw-acls"))

    nets, groups = _acl_name_maps(integration, site_id)
    layers, normalized = {}, {}
    count = 0
    for name, path in wanted:
        rows, _ = fetch_all(integration, path, page_size=200)
        rows.sort(key=lambda r: r.get('index') if isinstance(r.get('index'), int) else 10 ** 6)
        out = [_acl_row(r, nets, groups) for r in rows]
        layers[name] = out
        count += len(out)
        normalized[name] = {
            'order': [r.get('id') for r in rows],
            'rules': {r.get('id'): _canonical_hash(
                {k: v for k, v in r.items() if k != 'index'}) for r in rows},
            'hash': _canonical_hash([(r.get('index'), r.get('id')) for r in rows]),
        }
    return {'totalRows': count, 'returned': count, 'truncated': False,
            'layers': layers, 'normalized': normalized}


# ── T4: fixed-address / DHCP binding table ──────────────────────────────

def list_dhcp_reservations(integration, site_id: str, search: str = '',
                           page=1, page_size: int = 50):
    """The controller's DHCP user/binding table (MAC ↔ IP ↔ network ↔ name),
    including offline fixed-address entries. Search matches MAC, IP, name and
    network name (so an IP or MAC lookup works, not only hostname)."""
    if not site_id:
        raise ValueError('siteId is required')
    rows, total = fetch_all(integration, f"/sites/{site_id}/setting/service/dhcp",
                            page_size=200)
    needle = str(search or '').strip().lower()
    matched = [u for u in rows if _has(
        needle, u.get('mac'), u.get('ip'), u.get('name'), u.get('clientName'),
        u.get('netName'), u.get('serverName'), u.get('showingType'))]
    out = []
    for u in _paginate(matched, page, page_size):
        out.append({k: v for k, v in {
            'mac': u.get('mac'), 'ip': u.get('ip'),
            'name': u.get('name') or u.get('clientName'),
            'netId': u.get('netId'), 'netName': u.get('netName'),
            'serverName': u.get('serverName'), 'serverMac': u.get('serverMac'),
            'serverType': u.get('serverType'), 'type': u.get('type'),
            'showingType': u.get('showingType'), 'status': u.get('status'),
            'abnormal': u.get('abnormal'),
            'exportToIpMacBinding': u.get('exportToIpMacBinding'),
        }.items() if v is not None})
    total = total if total is not None else len(rows)
    return {'totalRows': total, 'matched': len(matched),
            'returned': len(out), 'truncated': len(out) < total,
            'rows': out}


# ── T5: device detail ───────────────────────────────────────────────────

_DEVICE_FIELDS = (
    'mac', 'name', 'type', 'subtype', 'model', 'modelName', 'ip', 'uptime',
    'status', 'detailStatus', 'modelVersion', 'lastSeen', 'cpuUtil', 'memUtil',
    'sn', 'tagName', 'uplinkDeviceMac', 'uplinkDeviceName', 'uplinkDevicePort',
    'linkSpeed', 'duplex', 'publicIp', 'firmwareVersion', 'active',
)


def get_device(integration, site_id: str, device_mac: str, full: bool = False):
    """Detail for one device by MAC (the Open API exposes no single-device GET,
    so this reads /devices/all and selects by MAC). `full` adds per-type extras:
    AP radios, gateway WAN status."""
    if not site_id or not device_mac:
        raise ValueError('siteId and deviceMac are required')
    rows, _ = fetch_all(integration, f"/sites/{site_id}/devices/all",
                        page_size=200)
    want = _norm_mac(device_mac)
    dev = next((d for d in rows if _norm_mac(d.get('mac')) == want), None)
    if dev is None:
        raise ValueError('device not found: %s' % device_mac)
    row = {k: dev.get(k) for k in _DEVICE_FIELDS if dev.get(k) is not None}
    if full:
        dtype = str(dev.get('type') or '').lower()
        extras = {}
        try:
            if dtype == 'ap':
                extras['radios'] = _fetch_json(
                    integration, f"/sites/{site_id}/aps/{dev.get('mac')}/radios")
            elif dtype == 'gateway':
                extras['wanStatus'] = _fetch_json(
                    integration, f"/sites/{site_id}/gateways/{dev.get('mac')}/wan-status")
        except Exception as e:  # extras are best-effort — never fail the read
            extras['error'] = '%s: %s' % (type(e).__name__, e)
        row['extras'] = extras
    return row


# ── T6: client connect/disconnect event log ─────────────────────────────

_CLIENT_EVENT_RE = re.compile(r'\[client:([0-9A-Fa-f:\-]+)\]')


def list_client_events(integration, site_id: str, client_mac: str = '',
                       module: str = 'Client', time_start=None, time_end=None,
                       page=1, page_size: int = 50, max_events: int = 5000):
    """Site event log (connect/disconnect etc.), optionally narrowed to one
    client MAC (the Open API has no client filter, so matching is done on the
    event content). Omada logs are window-limited — if nothing matches, widen
    timeStart/timeEnd."""
    if not site_id:
        raise ValueError('siteId is required')
    now = int(time.time() * 1000)
    time_end = int(time_end) if time_end else now
    time_start = int(time_start) if time_start else now - 7 * 86400000
    params = {'filters.timeStart': time_start, 'filters.timeEnd': time_end,
              'filters.module': module or 'Client'}
    # Upstream fetch size is fixed (the API page is a transport detail); the
    # caller's pageSize is applied to the filtered output only.
    upstream_page = 500
    fetched, total = fetch_all(integration, f"/sites/{site_id}/logs/events",
                               params, page_size=upstream_page,
                               max_pages=max(1, max_events // upstream_page))
    scanned = len(fetched)

    want = str(client_mac or '').strip().upper().replace('-', ':')
    rows = fetched
    if want:
        rows = [e for e in rows
                if want in str(e.get('content') or '').upper().replace('-', ':')]
    rows.sort(key=lambda e: -(e.get('time') or 0))
    matched = len(rows)
    out = []
    for e in _paginate(rows, page, page_size):
        m = _CLIENT_EVENT_RE.search(str(e.get('content') or ''))
        out.append({'id': e.get('id'), 'time': e.get('time'),
                    'module': e.get('module'), 'key': e.get('key'),
                    'clientMac': m.group(1) if m else None,
                    'content': e.get('content')})
    total = total if total is not None else scanned
    return {'totalRows': total, 'scanned': scanned, 'matched': matched,
            'returned': len(out),
            'truncated': bool(scanned < total or len(out) < matched),
            'window': {'timeStart': time_start, 'timeEnd': time_end},
            'retention_note': ('Omada event logs are window-limited; if no '
                               'events match, widen timeStart/timeEnd. The scan '
                               'is capped at %d events — narrow the window if '
                               'scanned < totalRows.' % max_events),
            'rows': out}