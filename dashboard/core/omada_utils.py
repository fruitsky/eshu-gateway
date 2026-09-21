"""Omada-specific helper behaviors for the generic passthrough path.

Omada list endpoints require `page`/`pageSize` (they 400 without them), and
ACL-create responses omit the created rule's id (agents must re-list to find
it). These helpers are wired into `execute_generic_call` *only* for Omada-kind
integrations, so the shared generic read/write floor stays unchanged for every
other integration.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

# List endpoints that 400 without page/pageSize. Suffix-matched against the
# request path (Omada paths are relative to /openapi/v1/<omadacId>).
OMADA_PAGINATED_SUFFIXES = (
    '/acls/osw-acls',
    '/acls/osg-acls',
    '/setting/service/mdns',
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


def _fetch_json(integration, path: str, params: dict = None):
    """Small GET against the integration's base URL. Raises on failure."""
    from core.integration_proxy import (
        DEFAULT_TIMEOUT, MAX_BODY_BYTES, _auth_headers, _guard_ssrf, _ssl_context,
    )
    path = (path or '').lstrip('/')
    base_url = (integration.get('base_url') or '').rstrip('/')
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method='GET', headers=_auth_headers(integration))
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


def _send_json(integration, path: str, payload: dict, method: str = 'POST'):
    """Small JSON request against the integration's base URL. Raises on failure."""
    from core.integration_proxy import (
        DEFAULT_TIMEOUT, MAX_BODY_BYTES, _auth_headers, _guard_ssrf, _ssl_context,
    )
    path = (path or '').lstrip('/')
    base_url = (integration.get('base_url') or '').rstrip('/')
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method=(method or 'POST').upper(),
        headers={'Content-Type': 'application/json', **(_auth_headers(integration))})
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


def _post_json(integration, path: str, payload: dict):
    """Small JSON POST against the integration's base URL. Raises on failure."""
    return _send_json(integration, path, payload, 'POST')


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