"""The audit log never carries a live window token."""
import time

from db.audit import get_audit_log
from db.core import db_conn
from db.windows import create_window_request, token_fingerprint


def test_create_claim_approve_and_delete_audit_only_a_fingerprint(
        auth_client, gateway_headers):
    now = int(time.time())
    created = auth_client.post('/api/approved-windows', json={
        'target_ip': '10.0.0.1', 'command': 'hostname',
        'window_start': now - 5, 'window_end': now + 600,
        'max_executions': 1, 'label': 'E2E: audit'}).json()
    token = created['token']
    fp = token_fingerprint(token)
    assert len(fp) == 8 and token not in fp

    claimed = auth_client.post('/api/approved-windows/execute',
                               json={'window_token': token},
                               headers=gateway_headers)
    assert claimed.status_code == 200, claimed.text

    request = create_window_request(
        '10.0.0.1', 'hostname', expires_at=now + 600,
        max_executions=1, label='E2E: audit')
    approved = auth_client.post(
        '/api/window-requests/%d/approve' % request['id'])
    assert approved.status_code == 200, approved.text
    token2 = approved.json()['token']
    fp2 = token_fingerprint(token2)
    assert fp2 != fp

    auth_client.delete('/api/approved-windows/%d' % created['id'])
    with db_conn() as conn:
        row = conn.execute('SELECT id FROM approved_windows WHERE token = ?',
                           (token2,)).fetchone()
    auth_client.delete('/api/approved-windows/%d' % row['id'])

    rows = {}
    for row in get_audit_log(200):
        if row['event_type'] in ('window_created', 'window_claimed',
                                 'window_request_approved',
                                 'window_deleted'):
            rows.setdefault(row['event_type'], []).append(row['details'])
    for event in ('window_created', 'window_claimed', 'window_deleted',
                  'window_request_approved'):
        assert event in rows, event
        for detail in rows[event]:
            assert token not in detail and token2 not in detail, event
    assert any(fp in d for d in rows['window_created'])
    assert any(fp in d for d in rows['window_claimed'])
    assert any(fp2 in d for d in rows['window_request_approved'])
    assert any(fp in d for d in rows['window_deleted'])
