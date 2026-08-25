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


def _post_json(integration, path: str, payload: dict):
    """Small JSON POST against the integration's base URL. Raises on failure."""
    from core.integration_proxy import (
        DEFAULT_TIMEOUT, MAX_BODY_BYTES, _auth_headers, _guard_ssrf, _ssl_context,
    )
    path = (path or '').lstrip('/')
    base_url = (integration.get('base_url') or '').rstrip('/')
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', **(_auth_headers(integration))})
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


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