"""The one-time mask of legacy window tokens in audit details."""
import hashlib

from db.audit import (
    WINDOW_TOKEN_MIGRATION_KEY, get_audit_log, migrate_window_token_details,
    record_audit_event,
)
from db.core import db_conn
from db.misc import get_meta_json


def _fp(token):
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _clear_flag():
    with db_conn() as conn:
        conn.execute('DELETE FROM meta WHERE key = ?',
                     (WINDOW_TOKEN_MIGRATION_KEY,))
        conn.commit()


def _details():
    return {r['event_type']: r['details'] for r in get_audit_log(100)}


def test_it_masks_only_the_four_window_events_and_only_the_token():
    tokens = {
        'window_created': 'CreateToken123',
        'window_claimed': 'ClaimToken456',
        'window_request_approved': 'ApproveToken789',
        'window_deleted': 'DeleteToken012',
    }
    record_audit_event('window_created', details=(
        "Window 'x' token=%s cmd=hostname" % tokens['window_created']))
    record_audit_event('window_claimed', details=(
        "Token %s claimed for 'hostname'" % tokens['window_claimed']))
    record_audit_event('window_request_approved', details=(
        "AI window request #1 approved, token=%s" %
        tokens['window_request_approved']))
    record_audit_event('window_deleted', details=(
        'Window #1 token=%s deleted' % tokens['window_deleted']))
    record_audit_event('window_deleted', details='Window #2 token=? deleted')
    record_audit_event('window_created', details=(
        'Window old token_fp=deadbeef token=AlreadyMasked'))
    record_audit_event('jit_created', details='token=NotAWindowToken')
    record_audit_event('window_created', details='token=bad-token')
    _clear_flag()

    assert migrate_window_token_details() == 4

    rows = {}
    for row in get_audit_log(100):
        rows.setdefault(row['event_type'], []).append(row['details'])
    for event, token in tokens.items():
        joined = ' '.join(rows[event])
        assert token not in joined
        assert 'token_fp=%s' % _fp(token) in joined
    assert any('token=?' in d for d in rows['window_deleted'])
    assert any('token=AlreadyMasked' in d for d in rows['window_created'])
    assert rows['jit_created'] == ['token=NotAWindowToken']
    assert 'token=bad-token' in ' '.join(rows['window_created'])
    # The migration's own row has a count, not a token.
    masked = [r for r in get_audit_log(100)
              if r['event_type'] == 'window_token_audit_masked']
    assert masked and '4' in masked[0]['details']
    assert all(t not in masked[0]['details'] for t in tokens.values())


def test_the_flag_makes_it_idempotent():
    record_audit_event('window_created', details='Window token=OneTimeToken')
    _clear_flag()
    assert migrate_window_token_details() == 1
    assert get_meta_json(WINDOW_TOKEN_MIGRATION_KEY, 0) == 1
    before = get_audit_log(20)
    assert migrate_window_token_details() == 0
    after = get_audit_log(20)
    assert [(r['id'], r['details']) for r in before] == [
        (r['id'], r['details']) for r in after]


def test_a_fingerprinted_row_is_not_touched_even_if_it_also_has_token():
    record_audit_event(
        'window_claimed',
        details='Token token_fp=deadbeef claimed; old token=LeftBehind')
    _clear_flag()
    assert migrate_window_token_details() == 0
    row = [r for r in get_audit_log(20)
           if r['event_type'] == 'window_claimed'][0]
    assert 'token=LeftBehind' in row['details']
