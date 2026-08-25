"""Reusable cookie/session + CSRF auth manager (NPM, future Uptime Kuma).

NPM v2 (and other admin-UI APIs) authenticate with a JWT session + CSRF token
rather than a per-request bearer key:

  POST {token_url}            {identity, secret}       -> {"token": <jwt>, ...}
  GET  {base}/tokens          Bearer <jwt>             -> {"token": <csrf>}
  every mutating request      Authorization: Bearer <jwt>  +  X-Csrf-Token: <csrf>

The manager caches jwt + csrf + expiry per integration, refreshes before
expiry, and re-authenticates once on 401. A 403 *after* a fresh login means the
server-side session/CSRF state is broken (csrf_failed) — surfaced as its own
code. Never logs the jwt/csrf.

Structured so a later Uptime Kuma integration can reuse it as-is.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

# name -> {'jwt': str, 'csrf': str, 'expires_at': float|None, 'token_url': str}
_sessions = {}

# Re-auth this many seconds before the JWT's nominal expiry.
SESSION_SAFETY_MARGIN = 60


def _proxy():
    """Lazy import to avoid the integration_proxy <-> session_auth cycle."""
    from core.integration_proxy import DEFAULT_TIMEOUT, MAX_BODY_BYTES, _ssl_context
    return DEFAULT_TIMEOUT, MAX_BODY_BYTES, _ssl_context


def _login_error(step: str, exc: Exception) -> ValueError:
    """Turn a urllib error into an actionable message. SSL errors almost always
    mean the URL scheme is https against NPM's plain-HTTP port 81; a 401 means
    the identity/secret pair was rejected."""
    import ssl
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return ValueError(
                f"session {step} returned HTTP 401 — NPM rejected the "
                "identity/secret; check the NPM user's email (Client ID) and "
                "password (Client Secret) match exactly")
        return ValueError(f"session {step} returned HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, ssl.SSLError):
            return ValueError(
                f"session {step} failed with an SSL error ({reason}) — check "
                "the base/token URL uses http:// (NPM's port 81 is plain HTTP, "
                "not HTTPS)")
        return ValueError(f"session {step} unreachable: {reason}")
    return ValueError(f"session {step} failed: {exc}")


def _login(integration: dict):
    """POST the token URL with {identity, secret}; returns (jwt, expires_at)."""
    token_url = (integration.get('token_url') or '').strip()
    identity = integration.get('client_id') or ''
    secret = integration.get('client_secret') or ''
    if not token_url:
        raise ValueError("session integration is missing a token_url")
    if not identity or not secret:
        raise ValueError("session integration is missing client_id / client_secret")
    body = json.dumps({'identity': identity, 'secret': secret}).encode('utf-8')
    req = urllib.request.Request(
        token_url, data=body, method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    timeout, max_bytes, ssl_ctx = _proxy()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx(integration)) as resp:
            payload = json.loads(resp.read(max_bytes + 1).decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise _login_error('login', e)
    except urllib.error.URLError as e:
        raise _login_error('login', e)
    jwt = payload.get('token')
    if not (isinstance(jwt, str) and jwt):
        raise ValueError("session login returned no token")
    expires_at = None
    try:
        expires_in = int(payload.get('expires_in') or 0)
    except (TypeError, ValueError):
        expires_in = None
    if expires_in:
        expires_at = time.time() + max(0, expires_in - SESSION_SAFETY_MARGIN)
    return jwt, expires_at


def _fetch_csrf(integration: dict, jwt: str) -> str:
    """GET {base}/tokens with the jwt; NPM returns the CSRF token in `token`."""
    base = (integration.get('base_url') or '').rstrip('/')
    url = base + '/tokens'
    req = urllib.request.Request(url, method='GET', headers={
        'Authorization': 'Bearer ' + jwt, 'Accept': 'application/json'})
    timeout, max_bytes, ssl_ctx = _proxy()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx(integration)) as resp:
            payload = json.loads(resp.read(max_bytes + 1).decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise _login_error('csrf fetch', e)
    except urllib.error.URLError as e:
        raise _login_error('csrf fetch', e)
    csrf = payload.get('token')
    if not (isinstance(csrf, str) and csrf):
        raise ValueError("session csrf fetch returned no token")
    return csrf


def _ensure_session(integration: dict) -> dict:
    """Login + fetch CSRF if needed, honoring the cache and expiry. Returns the
    session dict {jwt, csrf}. Raises ValueError on auth failure (surface as the
    integration's 'unauthorized' / auth-error code)."""
    name = integration.get('name') or ''
    entry = _sessions.get(name)
    if entry and entry.get('jwt') and (entry.get('expires_at') is None
                                       or entry['expires_at'] > time.time()):
        return entry
    jwt, expires_at = _login(integration)
    csrf = _fetch_csrf(integration, jwt)
    entry = {'jwt': jwt, 'csrf': csrf, 'expires_at': expires_at}
    if name:
        _sessions[name] = entry
    return entry


def session_headers(integration: dict, method: str = 'GET') -> dict:
    """Headers for a session-authed request: Authorization: Bearer <jwt>
    always; X-Csrf-Token added for mutating methods."""
    entry = _ensure_session(integration)
    headers = {'Authorization': 'Bearer ' + entry['jwt']}
    if (method or 'GET').upper() not in ('GET', 'HEAD'):
        headers['X-Csrf-Token'] = entry['csrf']
    return headers


def invalidate(integration: dict):
    """Drop the cached session (e.g. after a 401) so the next request re-logins."""
    name = integration.get('name') or ''
    if name:
        _sessions.pop(name, None)


def is_session_auth(integration: dict) -> bool:
    return (integration.get('auth_type') or '').lower() == 'session'