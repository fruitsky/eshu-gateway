"""Shared proxy engine for integration calls.

Both the MCP tool handlers and the approval executor route through here, so
every forwarded call gets identical credential injection, SSRF guarding,
truncation, and audit logging.
"""
import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from db.integrations import record_integration_call

# Reuse the fleet pattern: store up to 1 MB, keep a 2 KB preview for lists.
MAX_BODY_BYTES = 1048576
PREVIEW_CHARS = 2000
DEFAULT_TIMEOUT = 30

ALLOWED_AUTH_TYPES = ('none', 'bearer', 'basic', 'header', 'oauth2')

# Hard-to-undo mutations. Disruptive-but-reversible verbs (restart, reboot,
# stop, toggle) are deliberately excluded so routine writes auto-run under the
# 'destructive' gate mode. A single constant — trivial to tune per installation.
DESTRUCTIVE_VERBS = ('delete', 'remove', 'purge', 'format', 'reset')
_DESTRUCTIVE_RE = re.compile(r'\b(?:' + '|'.join(DESTRUCTIVE_VERBS) + r')\b', re.IGNORECASE)


def is_destructive(method: str, path: str) -> bool:
    """Classify a mutation as destructive: HTTP DELETE, or a destructive verb
    appearing in the path / WS command string."""
    if (method or '').upper() == 'DELETE':
        return True
    return bool(path and _DESTRUCTIVE_RE.search(path))

# In-memory OAuth2 access-token cache, keyed by integration name. Omada tokens
# don't expire, so a fetched token is reused for the process lifetime unless an
# upstream 401 forces a re-fetch.
_oauth_tokens = {}

# Shared context for integrations that opt out of TLS verification (self-signed
# certs, common on LAN controllers).
_UNVERIFIED_CTX = ssl._create_unverified_context()


def _ssl_context(integration: dict):
    """Return a no-verify TLS context when the integration's `verify_tls` is
    off; `None` (library default, verified) otherwise."""
    if not integration.get('verify_tls', 1):
        return _UNVERIFIED_CTX
    return None


class ProxyError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _omadac_id(integration: dict) -> str:
    """Omada embeds the controller/account id as the last path segment of the
    base URL (e.g. .../openapi/v1/<omadacId>); the token exchange needs it too."""
    base = (integration.get('base_url') or '').rstrip('/')
    return base.rsplit('/', 1)[-1] if base else ''


def _fetch_oauth2_token(integration: dict) -> str:
    """Omada OAuth2 client_credentials token exchange.

    POST <token_url>?grant_type=client_credentials with a JSON body of
    {omadacId, client_id, client_secret}; the access token comes back under
    `result.accessToken`. Tokens are cached (no TTL) and re-fetched only on an
    upstream 401."""
    token_url = (integration.get('token_url') or '').strip()
    client_id = integration.get('client_id') or ''
    client_secret = integration.get('client_secret') or ''
    omadac_id = _omadac_id(integration)
    if not token_url:
        raise ProxyError(500, "OAuth2 integration is missing a token_url")
    if not client_id or not client_secret:
        raise ProxyError(500, "OAuth2 integration is missing client_id / client_secret")
    if not omadac_id:
        raise ProxyError(500, "OAuth2 integration base_url must end in the Omada account id")
    parsed = urllib.parse.urlparse(token_url)
    if parsed.scheme not in ('http', 'https'):
        raise ProxyError(500, "OAuth2 token_url must be http(s)")
    sep = '&' if '?' in token_url else '?'
    url = f"{token_url}{sep}grant_type=client_credentials"
    body = json.dumps({
        'omadacId': omadac_id,
        'client_id': client_id,
        'client_secret': client_secret,
    }).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT, context=_ssl_context(integration)) as resp:
            raw = resp.read(MAX_BODY_BYTES + 1)
            payload = json.loads(raw.decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        raise ProxyError(502, f"OAuth2 token endpoint returned HTTP {e.code}")
    except urllib.error.URLError as e:
        raise ProxyError(502, f"OAuth2 token endpoint unreachable: {e.reason}")
    result = payload.get('result') or {}
    token = result.get('accessToken')
    if isinstance(token, str) and token:
        return token
    raise ProxyError(502, "OAuth2 token endpoint returned no accessToken")


def _oauth2_headers(integration: dict) -> dict:
    name = integration.get('name') or ''
    token = _oauth_tokens.get(name)
    if not token:
        token = _fetch_oauth2_token(integration)
        if name:
            _oauth_tokens[name] = token
    return {'Authorization': 'AccessToken=' + token}


def _auth_headers(integration: dict) -> dict:
    auth_type = (integration.get('auth_type') or 'none').lower()
    secret = integration.get('secret') or ''
    headers = {}
    if auth_type == 'bearer' and secret:
        headers['Authorization'] = 'Bearer ' + secret
    elif auth_type == 'basic' and secret:
        headers['Authorization'] = 'Basic ' + base64.b64encode(secret.encode('utf-8')).decode('ascii')
    elif auth_type == 'header' and secret:
        name = integration.get('auth_header_name') or 'Authorization'
        headers[name] = secret
    elif auth_type == 'oauth2':
        headers.update(_oauth2_headers(integration))
    return headers


def _build_request(tool: dict, args: dict):
    method = (tool.get('method') or 'GET').upper()
    template = tool.get('path_template') or ''
    path = template
    query_params = {}
    body_params = {}
    raw_body = None
    for p in tool.get('params') or []:
        name = p.get('name')
        # `query_key` lets the MCP-facing param name differ from the wire key
        # (e.g. timeStart -> filters.timeStart). Path substitution uses `name`.
        key = p.get('query_key') or name
        val = args.get(name)
        if val is None:
            val = p.get('default')
        if val is None or val == '':
            continue
        if '{' + name + '}' in path:
            path = path.replace('{' + name + '}', urllib.parse.quote(str(val), safe=''))
        elif p.get('type') == 'json':
            raw_body = val
        elif method in ('POST', 'PUT', 'PATCH'):
            body_params[key] = val
        else:
            query_params[key] = val
    query_string = urllib.parse.urlencode(query_params)
    return method, path, query_string, body_params, raw_body


def _guard_ssrf(base_url: str, path: str):
    """Only ever forward to the integration's configured host. Reject anything
    that would change the target (scheme-injection, authority tricks, traversal)."""
    if '://' in path or path.startswith('//'):
        raise ProxyError(403, "Blocked: path attempts to change the target host")
    if '..' in path or path.startswith('@'):
        raise ProxyError(403, "Blocked: path traversal is not allowed")
    base = urllib.parse.urlparse(base_url)
    candidate = urllib.parse.urlparse(base_url.rstrip('/') + '/' + path.lstrip('/'))
    if candidate.scheme not in ('http', 'https'):
        raise ProxyError(403, "Blocked: unsupported scheme")
    if candidate.netloc != base.netloc:
        raise ProxyError(403, "Blocked: target host does not match the configured integration")


def _extract(data, dotted: str):
    """Extract a (possibly dotted) path from a dict, or None if missing."""
    cur = data
    for part in dotted.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _project_dict(data: dict, fields: list) -> dict:
    """Project a single object to the listed fields. Fields may be dotted
    paths (e.g. `attributes.friendly_name`); the output key is the last
    segment (so `attributes.friendly_name` -> `friendly_name`)."""
    out = {}
    for f in fields:
        val = _extract(data, f)
        if val is not None:
            out[f.split('.')[-1]] = val
    return out


def _unwrap_envelope(data):
    """Descend through the common API envelopes. Proxmox/Omada wrap payloads in
    `{"data": ...}` and Omada additionally in `{"result": ...}` (which itself
    holds a `{"data": [...]}` grid). Unwrapping both lets projection/search/limit
    shaping operate on the actual list/object regardless of wrapper."""
    if not isinstance(data, dict):
        return data
    if isinstance(data.get('result'), (list, dict)):
        data = data['result']
    if isinstance(data, dict) and isinstance(data.get('data'), (list, dict)):
        data = data['data']
    return data


def _project_body(body: str, fields: list) -> str:
    """Project a JSON response down to the listed fields.

    Descends into the common `{"data": ...}` API envelope (Proxmox) and the
    Omada `{"result": {"data": [...]}}` envelope, then projects list items /
    a single object. Supports dotted field paths.
    Returns the body unchanged if it isn't valid JSON or a shape we can project."""
    if not fields:
        return body
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    data = _unwrap_envelope(data)
    if isinstance(data, list):
        projected = []
        for item in data:
            if isinstance(item, dict):
                projected.append(_project_dict(item, fields))
            else:
                projected.append(item)
        return json.dumps(projected)
    if isinstance(data, dict):
        return json.dumps(_project_dict(data, fields))
    return body


def _upstream_error(body: str):
    """Detect an Omada-style logical-error envelope (HTTP 200 with a non-zero
    `errorCode`). Returns a clear message, or None for success/non-JSON bodies.
    Projection would otherwise flatten these into `{}`, hiding the failure."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        code = data.get('errorCode')
        if code not in (None, 0, '0'):
            msg = data.get('msg') or data.get('message') or ''
            return f"Omada error {code}: {msg}".strip()
    return None


def _apply_shaping(body: str, tool: dict, args: dict) -> str:
    """Client-side response shaping: exact filters + search filter + limit +
    field projection.

    `full=true` skips all shaping. For each name in the tool's `filter_fields`,
    an exact-match filter is applied against `args[name]`. `search` filters a
    list by case-insensitive substring on the tool's `search_field`; `limit`
    caps the list (default 50 via the synthetic param). Applied to the
    unwrapped data before projection. `strip_envelope` unwraps the API envelope
    (e.g. Omada's {errorCode,msg,result}) even when there is nothing to
    project, so passthrough tools still drop the wrapper."""
    a = args or {}
    fields = tool.get('fields') or []
    search_field = tool.get('search_field') or ''
    filter_fields = tool.get('filter_fields') or []
    strip_envelope = tool.get('strip_envelope')
    if a.get('full') or (not fields and not search_field and not filter_fields and not strip_envelope):
        return body
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    data = _unwrap_envelope(data)
    if isinstance(data, list):
        if filter_fields:
            for f in filter_fields:
                val = a.get(f)
                if val is not None and val != '':
                    data = [item for item in data
                            if isinstance(item, dict) and _extract(item, f) == val]
        if search_field:
            search = a.get('search')
            if search:
                needle = str(search).lower()
                data = [item for item in data
                        if isinstance(item, dict)
                        and needle in str(_extract(item, search_field)).lower()]
            limit = a.get('limit')
            if limit is not None:
                try:
                    n = int(limit)
                    if n >= 0:
                        data = data[:n]
                except (TypeError, ValueError):
                    pass
    if fields:
        if isinstance(data, list):
            return json.dumps([_project_dict(item, fields) if isinstance(item, dict) else item for item in data])
        if isinstance(data, dict):
            return json.dumps(_project_dict(data, fields))
    if search_field or filter_fields or strip_envelope:
        return json.dumps(data)
    return body


def _http_roundtrip(integration: dict, url: str, body_bytes, headers: dict, method: str):
    """Perform the HTTP request with the OAuth2 401-retry. Returns
    (status_code, body, truncated, error, latency_ms, outcome)."""
    auth_type = (integration.get('auth_type') or 'none').lower()
    start = time.time()
    outcome = 'ok'
    status_code = None
    body = ''
    truncated = 0
    error = None
    attempt = 0
    while True:
        attempt += 1
        try:
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT, context=_ssl_context(integration)) as resp:
                status_code = resp.status
                raw = resp.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES:
                    truncated = 1
                    raw = raw[:MAX_BODY_BYTES]
                body = raw.decode('utf-8', errors='replace')
            break
        except urllib.error.HTTPError as e:
            status_code = e.code
            outcome = 'error'
            try:
                body = e.read(MAX_BODY_BYTES).decode('utf-8', errors='replace')
            except Exception:
                body = ''
            # OAuth2: a 401 may mean the cached token was rejected. Clear the
            # cache and retry once with a freshly-fetched token.
            if (auth_type == 'oauth2' and e.code == 401 and attempt == 1
                    and integration.get('name') in _oauth_tokens):
                _oauth_tokens.pop(integration.get('name'), None)
                headers = _auth_headers(integration)
                continue
            break
        except urllib.error.URLError as e:
            outcome = 'error'
            error = str(e.reason)
            break
        except Exception as e:
            outcome = 'error'
            error = f"{type(e).__name__}: {e}"
            break
    latency_ms = int((time.time() - start) * 1000)
    return status_code, body, truncated, error, latency_ms, outcome


def execute_integration_call(integration: dict, tool: dict, args: dict, agent: str = '') -> dict:
    """Forward a call to the integration and return a JSON-safe result dict.
    Raises ProxyError for policy rejections (SSRF guard, etc.)."""
    if not integration or not integration.get('enabled'):
        raise ProxyError(404, "Integration not found or disabled")
    if not tool or not tool.get('enabled'):
        raise ProxyError(404, "Tool not found or disabled")
    auth_type = (integration.get('auth_type') or 'none').lower()
    if auth_type not in ALLOWED_AUTH_TYPES:
        raise ProxyError(500, f"Unsupported auth_type: {auth_type}")

    method, path, query_string, body_params, raw_body = _build_request(tool, args)
    base_url = (integration.get('base_url') or '').rstrip('/')
    # Omada v2 endpoints live under /openapi/v2/... while the integration's
    # base URL is /openapi/v1/... — a tool flagged `version: v2` swaps the
    # segment (the token exchange still reads the stored v1 base for omadacId).
    if tool.get('version') == 'v2':
        base_url = base_url.replace('/openapi/v1/', '/openapi/v2/')
    _guard_ssrf(base_url, path)

    url = base_url + '/' + path.lstrip('/')
    if query_string:
        url += '?' + query_string

    headers = _auth_headers(integration)
    headers.setdefault('Accept', 'application/json')
    body_bytes = None
    if method in ('POST', 'PUT', 'PATCH'):
        payload = raw_body if raw_body is not None else body_params
        if payload:
            body_bytes = json.dumps(payload).encode('utf-8')
            headers.setdefault('Content-Type', 'application/json')

    status_code, body, truncated, error, latency_ms, outcome = _http_roundtrip(
        integration, url, body_bytes, headers, method)

    # Surface upstream logical errors (Omada errorCode != 0) instead of
    # projecting the error envelope down to {}.
    if outcome == 'ok':
        up_err = _upstream_error(body)
        if up_err:
            outcome = 'error'
            error = up_err
            body = ''
        else:
            body = _apply_shaping(body, tool, args)

    record_integration_call(
        integration=integration.get('name', ''),
        tool=tool.get('name', ''),
        agent=agent,
        method=method,
        path=path,
        status_code=status_code,
        latency_ms=latency_ms,
        response_summary=(body or error or '')[:PREVIEW_CHARS],
        response_bytes=len(body),
        truncated=truncated,
        outcome=outcome,
    )

    return {
        'status_code': status_code,
        'body': body,
        'truncated': truncated,
        'latency_ms': latency_ms,
        'error': error,
    }


def execute_generic_call(integration: dict, method: str, path: str, params=None,
                         data=None, agent: str = '', tool_name: str = '') -> dict:
    """Call an arbitrary endpoint on the integration — the generic read/write
    floor. method/path come from the agent; `params` becomes the query string,
    `data` the JSON body. Credentials, TLS, SSRF guard, and audit all apply."""
    if not integration or not integration.get('enabled'):
        raise ProxyError(404, "Integration not found or disabled")
    method = (method or 'GET').upper()
    if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
        raise ProxyError(400, f"Unsupported method: {method}")
    auth_type = (integration.get('auth_type') or 'none').lower()
    if auth_type not in ALLOWED_AUTH_TYPES:
        raise ProxyError(500, f"Unsupported auth_type: {auth_type}")

    path = (path or '').lstrip('/')
    base_url = (integration.get('base_url') or '').rstrip('/')
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)

    headers = _auth_headers(integration)
    headers.setdefault('Accept', 'application/json')
    body_bytes = None
    if method in ('POST', 'PUT', 'PATCH') and data:
        body_bytes = json.dumps(data).encode('utf-8')
        headers.setdefault('Content-Type', 'application/json')

    status_code, body, truncated, error, latency_ms, outcome = _http_roundtrip(
        integration, url, body_bytes, headers, method)

    if outcome == 'ok':
        up_err = _upstream_error(body)
        if up_err:
            outcome = 'error'
            error = up_err
            body = ''

    record_integration_call(
        integration=integration.get('name', ''),
        tool=tool_name,
        agent=agent,
        method=method,
        path=path,
        status_code=status_code,
        latency_ms=latency_ms,
        response_summary=(body or error or '')[:PREVIEW_CHARS],
        response_bytes=len(body),
        truncated=truncated,
        outcome=outcome,
    )

    return {
        'status_code': status_code,
        'body': body,
        'truncated': truncated,
        'latency_ms': latency_ms,
        'error': error,
    }
