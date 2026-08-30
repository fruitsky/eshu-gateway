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
import time

import websocket  # websocket-client

from core.integration_proxy import PREVIEW_CHARS, ProxyError
from core.secret_scrub import scrub_body, scrub_string

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


def execute_ws_call(integration: dict, command: str, payload=None,
                    agent: str = '', tool_name: str = '',
                    session_id: str = '', execution_id: str = '') -> dict:
    """Run one HA WS command with audit logging. Returns a result dict shaped
    like the HTTP executors ({status_code, body, truncated, latency_ms, error}),
    so callers (MCP tool runner / approval executor) handle it uniformly."""
    from db.integrations import record_integration_call
    start = time.time()
    outcome = 'ok'
    error = None
    try:
        result = ha_ws_request(integration, command, payload or {})
        body = scrub_body(json.dumps(result))
        status_code = 200
    except ProxyError as e:
        outcome = 'error'
        error = scrub_string(e.message)
        status_code = e.status_code
        body = ''
    latency_ms = int((time.time() - start) * 1000)
    record_integration_call(
        integration=integration.get('name', ''),
        tool=tool_name,
        agent=agent,
        method='WS',
        path=command,
        status_code=status_code,
        latency_ms=latency_ms,
        response_summary=(body or error or '')[:PREVIEW_CHARS],
        response_bytes=len(body),
        truncated=0,
        outcome=outcome,
        session_id=session_id,
        execution_id=execution_id,
    )
    return {'status_code': status_code, 'body': body, 'truncated': 0,
            'latency_ms': latency_ms, 'error': error}
