"""Shared proxy engine for integration calls.

Both the MCP tool handlers and the approval executor route through here, so
every forwarded call gets identical credential injection, SSRF guarding,
truncation, and audit logging.
"""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from db.integrations import record_integration_call

# Reuse the fleet pattern: store up to 1 MB, keep a 2 KB preview for lists.
MAX_BODY_BYTES = 1048576
PREVIEW_CHARS = 2000
DEFAULT_TIMEOUT = 30

ALLOWED_AUTH_TYPES = ('none', 'bearer', 'basic', 'header')


class ProxyError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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
        val = args.get(name)
        if val is None or val == '':
            continue
        if '{' + name + '}' in path:
            path = path.replace('{' + name + '}', urllib.parse.quote(str(val), safe=''))
        elif p.get('type') == 'json':
            raw_body = val
        elif method in ('POST', 'PUT', 'PATCH'):
            body_params[name] = val
        else:
            query_params[name] = val
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


def _project_dict(data: dict, fields: list) -> dict:
    """Project a single object to the listed fields. Fields may be dotted
    paths (e.g. `attributes.friendly_name`); the output key is the last
    segment (so `attributes.friendly_name` -> `friendly_name`)."""
    out = {}
    for f in fields:
        parts = f.split('.')
        cur = data
        ok = True
        for part in parts:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            out[parts[-1]] = cur
    return out


def _project_body(body: str, fields: list) -> str:
    """Project a JSON response down to the listed fields.

    Descends into the common `{"data": ...}` API envelope (Proxmox), then
    projects list items / a single object. Supports dotted field paths.
    Returns the body unchanged if it isn't valid JSON or a shape we can project."""
    if not fields:
        return body
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    # Unwrap the common {"data": <payload>} envelope.
    if isinstance(data, dict) and isinstance(data.get('data'), (list, dict)):
        data = data['data']
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

    start = time.time()
    outcome = 'ok'
    status_code = None
    body = ''
    truncated = 0
    error = None
    try:
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            status_code = resp.status
            raw = resp.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                truncated = 1
                raw = raw[:MAX_BODY_BYTES]
            body = raw.decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        status_code = e.code
        outcome = 'error'
        try:
            body = e.read(MAX_BODY_BYTES).decode('utf-8', errors='replace')
        except Exception:
            body = ''
    except urllib.error.URLError as e:
        outcome = 'error'
        error = str(e.reason)
    except Exception as e:
        outcome = 'error'
        error = f"{type(e).__name__}: {e}"
    latency_ms = int((time.time() - start) * 1000)

    # Response projection (token efficiency): if the tool declares a `fields`
    # list and the caller didn't ask for `full`, trim the body to those fields.
    fields = tool.get('fields') or []
    if outcome == 'ok' and fields and not (args or {}).get('full'):
        body = _project_body(body, fields)

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
