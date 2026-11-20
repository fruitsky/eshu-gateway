"""Window tokens travel in a JSON body, not a URL path or query.

The old path routes answer 410 and name the new route. Their access-log line
is masked to `<redacted>`, and our audit rows carry only the token
fingerprint. The approver allowlist is unchanged.
"""
import logging
import time

from db.audit import get_audit_log
from db.windows import token_fingerprint


def _window(auth_client, command='uptime'):
    now = int(time.time())
    r = auth_client.post('/api/approved-windows', json={
        'target_ip': '10.0.0.1', 'command': command, 'match_type': 'exact',
        'window_start': now - 10, 'window_end': now + 3600,
        'max_executions': 1})
    assert r.status_code == 200, r.text
    return r.json()['token'], r.json()['id']


def test_lookup_moves_to_a_body_and_never_echoes_the_token(auth_client):
    token, _id = _window(auth_client)
    r = auth_client.post('/api/window-by-token', json={'token': token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['command'] == 'uptime'
    assert 'token' not in body
    assert token not in r.text


def test_the_old_lookup_path_is_gone_not_an_alias(client, auth_client):
    token, _id = _window(auth_client)
    r = client.get('/api/window-by-token/' + token)
    assert r.status_code == 410
    assert token not in r.text
    assert 'POST /api/window-by-token' in r.json()['detail']
    row = [x for x in get_audit_log(50)
           if x['event_type'] == 'window_lookup_route_retired'][0]
    assert token not in row['details']
    assert token_fingerprint(token) in row['details']


def test_the_legacy_claim_path_still_works_and_is_audited_once(
        auth_client, gateway_headers):
    token, _id = _window(auth_client)
    legacy = auth_client.post('/api/approved-windows/execute/' + token,
                              headers=gateway_headers)
    assert legacy.status_code == 200, legacy.text
    assert token not in legacy.text
    rows = [x for x in get_audit_log(50)
            if x['event_type'] == 'window_legacy_path_claim_used']
    assert len(rows) == 1
    assert token not in rows[0]['details']
    assert token_fingerprint(token) in rows[0]['details']
    assert '10.0.0.1' in rows[0]['details']


def test_a_bad_gateway_token_or_ip_does_not_audit_a_legacy_use(
        auth_client, gateway_headers):
    from db.gateways import register_gateway, set_gateway_token
    token, _id = _window(auth_client)
    bad = auth_client.post('/api/approved-windows/execute/' + token,
                           headers={'X-Gateway-Token': 'wrong'})
    assert bad.status_code == 401
    register_gateway('10.0.0.2', 'other', 'v15.3')
    set_gateway_token('10.0.0.2', 'token-10-0-0-2')
    wrong_ip = auth_client.post('/api/approved-windows/execute/' + token,
                                headers={'X-Gateway-Token': 'token-10-0-0-2'})
    assert wrong_ip.status_code == 403
    rows = [x for x in get_audit_log(50)
            if x['event_type'] == 'window_legacy_path_claim_used']
    assert rows == []


def test_the_retired_lookup_is_rate_limited_and_audits_once(
        client, auth_client):
    import main as app_main
    token, _id = _window(auth_client)
    app_main._retired_lookup_audit.clear()
    first = client.get('/api/window-by-token/' + token)
    second = client.get('/api/window-by-token/' + token)
    assert first.status_code == 410 and second.status_code == 410
    rows = [x for x in get_audit_log(50)
            if x['event_type'] == 'window_lookup_route_retired']
    assert len(rows) == 1
    assert token not in rows[0]['details']
    assert token_fingerprint(token) in rows[0]['details']


def test_the_retrieval_key_read_returns_no_raw_token(client, auth_client):
    token, _id = _window(auth_client)
    with __import__('db.core', fromlist=['db_conn']).db_conn() as conn:
        key = conn.execute('SELECT retrieval_key FROM approved_windows '
                           'WHERE token = ?', (token,)).fetchone()['retrieval_key']
    r = client.get('/api/approved-windows/' + key)
    assert r.status_code == 200
    assert token not in r.text
    assert 'token' not in r.json()
    assert r.json()['token_fp'] == token_fingerprint(token)


def test_new_execution_rows_store_only_a_fingerprint(auth_client,
                                                     gateway_headers):
    from db.core import db_conn
    token, window_id = _window(auth_client)
    r = auth_client.post('/api/approved-windows/execute',
                         json={'window_token': token},
                         headers=gateway_headers)
    assert r.status_code == 200
    with db_conn() as conn:
        row = conn.execute(
            'SELECT token, token_fp FROM window_executions '
            'WHERE window_id = ? ORDER BY id DESC LIMIT 1', (window_id,)).fetchone()
    assert row['token'] == ''
    assert row['token_fp'] == token_fingerprint(token)


def test_existing_execution_tokens_are_migrated_on_start(auth_client):
    from db.core import db_conn, init_db
    raw = 'aB3dEfGhIjKlMnOpQrStUvWx'
    with db_conn() as conn:
        cur = conn.execute(
            'INSERT INTO window_executions '
            '(window_id, token, target_ip, command, executed_at, success, reason, token_fp) '
            "VALUES (1, ?, '10.0.0.1', 'uptime', 1, 1, '', '')", (raw,))
        row_id = cur.lastrowid
        conn.commit()
    init_db()
    with db_conn() as conn:
        row = conn.execute('SELECT token, token_fp FROM window_executions '
                           'WHERE id = ?', (row_id,)).fetchone()
    assert row['token'] == ''
    assert row['token_fp'] == token_fingerprint(raw)


def test_execution_history_only_carries_a_fingerprint(auth_client,
                                                      gateway_headers):
    token, window_id = _window(auth_client, 'hostname')
    r = auth_client.post('/api/approved-windows/execute',
                         json={'window_token': token},
                         headers=gateway_headers)
    assert r.status_code == 200, r.text
    hist = auth_client.get('/api/approved-windows/%d/executions' % window_id)
    assert hist.status_code == 200, hist.text
    assert token not in hist.text
    assert hist.json()[0]['token_fp'] == token_fingerprint(token)


def test_the_access_log_masks_a_token_that_an_old_client_still_sends():
    from main import _WindowTokenAccessFilter, _mask_window_token_path

    token = 'aB3dEfGhIjKlMnOpQrStUvWx'
    assert token not in _mask_window_token_path(
        '/api/window-by-token/' + token)
    assert '<redacted>' in _mask_window_token_path(
        '/api/window-by-token/' + token)
    assert token not in _mask_window_token_path(
        '/api/approved-windows/execute/' + token)
    assert token not in _mask_window_token_path(
        '/api/window-by-token?token=' + token)

    record = logging.LogRecord(
        'uvicorn.access', logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %s',
        ('127.0.0.1:1', 'GET', '/api/window-by-token/' + token,
         '1.1', 410), None)
    assert _WindowTokenAccessFilter().filter(record) is True
    line = record.getMessage()
    assert token not in line
    assert '<redacted>' in line


def test_the_approver_allowlist_is_unchanged():
    from core import approver_auth
    assert not approver_auth.route_allowed('POST', '/api/window-by-token')
    assert not approver_auth.route_allowed('POST',
                                           '/api/approved-windows/execute')
    assert not approver_auth.route_allowed(
        'GET', '/api/window-by-token/abc')
    # The old allowed set still works.
    assert approver_auth.route_allowed('POST', '/api/approve/1')


def test_the_retired_lookup_rate_limits_before_it_audits(client, auth_client, monkeypatch):
    from fastapi import HTTPException
    import main as app_main
    token, _id = _window(auth_client)
    app_main._retired_lookup_audit.clear()
    monkeypatch.setattr(app_main, '_check_rate_limit',
                        lambda *a, **k: (_ for _ in ()).throw(
                            HTTPException(status_code=429, detail='slow down')))
    r = client.get('/api/window-by-token/' + token)
    assert r.status_code == 429
    rows = [x for x in get_audit_log(50)
            if x['event_type'] == 'window_lookup_route_retired']
    assert rows == []
