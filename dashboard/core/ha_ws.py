"""Home Assistant WebSocket API client (sync, per-call).

Some HA surfaces (entity/device registries, Lovelace, system log, ...) are only
reachable over the WebSocket API — there is no REST equivalent. This module
speaks the HA WS protocol for a single command:

    connect -> auth_required -> auth (token) -> auth_ok -> send {id, type, ...}
             -> read messages until the id-correlated result -> return `result`

A fresh connection per call keeps v1 simple (no keepalive, reconnect, or
concurrency/locking concerns). `auth_invalid` is surfaced as a 401 ProxyError so
the operator can tell an upstream HA credential problem apart from the MCP
bearer token (which is unrelated).
"""
import json
import ssl

import websocket  # websocket-client

from core.integration_proxy import ProxyError

READ_TIMEOUT = 30


def _ws_url(integration: dict) -> str:
    base = (integration.get('base_url') or '').rstrip('/')
    if base.startswith('https://'):
        return 'wss://' + base[len('https://'):] + '/websocket'
    if base.startswith('http://'):
        return 'ws://' + base[len('http://'):] + '/websocket'
    return base + '/websocket'


def _recv(ws):
    data = ws.recv()
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return {'_raw': data}


def ha_ws_request(integration: dict, command: str, payload: dict) -> dict:
    """Run one HA WebSocket command and return its `result`.

    Raises ProxyError on missing credential, handshake/auth failure, transport
    errors, or an upstream `success: false` result."""
    token = integration.get('secret') or ''
    if not token:
        raise ProxyError(500, "HA WebSocket: integration has no secret/token")
    sslopt = None
    if not integration.get('verify_tls', 1):
        sslopt = {'cert_reqs': ssl.CERT_NONE}
    ws = websocket.create_connection(_ws_url(integration), timeout=READ_TIMEOUT, sslopt=sslopt)
    try:
        msg = _recv(ws)
        if not isinstance(msg, dict) or msg.get('type') != 'auth_required':
            raise ProxyError(502, f"HA WebSocket: expected auth_required, got {msg}")
        ws.send(json.dumps({'type': 'auth', 'access_token': token}))
        msg = _recv(ws)
        if not isinstance(msg, dict) or msg.get('type') != 'auth_ok':
            reason = (msg or {}).get('message', 'unknown') if isinstance(msg, dict) else 'unknown'
            raise ProxyError(401, f"HA auth_invalid: {reason}")
        ws.send(json.dumps({'id': 1, 'type': command, **(payload or {})}))
        while True:
            msg = _recv(ws)
            if not isinstance(msg, dict) or msg.get('id') != 1:
                continue  # ignore events/pings/unsolicited messages
            if msg.get('type') == 'result':
                if not msg.get('success'):
                    err = msg.get('error') or {}
                    code = err.get('code', '') if isinstance(err, dict) else ''
                    message = err.get('message', '') if isinstance(err, dict) else str(err)
                    raise ProxyError(502, f"HA WS error {code}: {message}".strip())
                return msg.get('result')
            return msg
    finally:
        ws.close()
