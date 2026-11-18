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


def test_the_old_claim_path_is_gone_and_the_body_claim_still_works(
        auth_client, gateway_headers):
    token, _id = _window(auth_client)
    old = auth_client.post('/api/approved-windows/execute/' + token,
                           headers=gateway_headers)
    assert old.status_code == 410
    assert token not in old.text
    assert '/api/approved-windows/execute' in old.json()['detail']
    row = [x for x in get_audit_log(50)
           if x['event_type'] == 'window_token_route_retired'][0]
    assert token not in row['details']
    assert token_fingerprint(token) in row['details']

    claim = auth_client.post('/api/approved-windows/execute',
                             json={'window_token': token},
                             headers=gateway_headers)
    assert claim.status_code == 200, claim.text
    assert token not in claim.text
    claimed = [x for x in get_audit_log(50)
               if x['event_type'] == 'window_claimed'][0]
    assert token not in claimed['details']
    assert token_fingerprint(token) in claimed['details']


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
