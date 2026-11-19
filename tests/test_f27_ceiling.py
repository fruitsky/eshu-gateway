"""F27: the allowance ceiling — catalogue, seed, checks and the API."""
import pytest

from core import allowances as a


def _enable_risky(c, ids):
    # BX6: the server now requires the hash the form was loaded with.
    return c.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True,
        'set_hash': a.set_hash()})


def test_the_seed_has_the_operator_defaults(auth_client):
    body = auth_client.get('/api/allowances/ceiling').json()
    assert 'cover.open_cover' not in body['enabled']
    assert 'climate.turn_on' not in body['enabled']
    for cid in ('light.turn_on', 'switch.toggle', 'fan.turn_off',
                'input_boolean.turn_on', 'media_player.media_stop'):
        assert cid in body['enabled']
    assert body['set_hash'] == a.set_hash()
    assert len(body['never']['domains']) == 11
    assert any(d['value'] == 'open' for d in body['never']['services'])


def test_ceiling_allows_only_enabled_catalogue_rows(auth_client):
    assert a.ceiling_allows('ha', 'light', 'turn_on') is True
    assert a.ceiling_allows('ha', 'cover', 'open_cover') is False
    assert a.ceiling_allows('ha', 'lock', 'unlock') is False
    assert a.ceiling_allows('ha', 'light', 'delete_everything') is False


def test_a_risky_row_needs_the_ack(auth_client):
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': a.enabled_ids() + ['cover.open_cover'],
        'set_hash': a.set_hash()})
    assert r.status_code == 400
    assert 'risk' in r.text.lower()
    assert a.ceiling_allows('ha', 'cover', 'open_cover') is False

    r = _enable_risky(auth_client, a.enabled_ids() + ['cover.open_cover'])
    assert r.status_code == 200
    assert a.ceiling_allows('ha', 'cover', 'open_cover') is True


def test_the_stop_exemption_is_the_exact_pair(auth_client):
    # Cover Stop and media Stop are the safe direction; other words still cap.
    assert a.ceiling_allows('ha', 'cover', 'stop_cover') is False  # row off
    _enable_risky(auth_client, a.enabled_ids() + [
        'cover.open_cover', 'cover.close_cover', 'cover.stop_cover'])
    assert a.ceiling_allows('ha', 'cover', 'stop_cover') is True
    assert a.ceiling_allows('ha', 'media_player', 'media_stop') is True
    # An operator-added `stop` would still switch both off. `stop` is a
    # built-in, so the API refuses it; insert it as an operator word to test
    # that the exemption is for the *built-in* only.
    from db.core import db_conn
    with db_conn() as conn:
        conn.execute("INSERT INTO allowance_never_words (word, added_by, added_at) "
                     "VALUES ('stop', 'test', 1)")
        conn.commit()
    assert a.is_destructive('stop_cover', ('stop',)) is True
    assert a.ceiling_allows('ha', 'cover', 'stop_cover') is False
    assert a.ceiling_allows('ha', 'media_player', 'media_stop') is False


def test_bv1_open_is_never_but_not_destructive():
    assert a.never_reason('lock', 'open') == 'opens a lock or a valve'
    assert a.never_reason('valve', 'open') == 'opens a lock or a valve'
    assert a.never_reason('cover', 'open_cover') == ''
    assert a.is_destructive('open_cover') is False
    assert a.is_destructive('alarm_disarm') is True
    assert a.is_destructive('restart_container') is True


def test_a_stale_set_hash_is_409(auth_client):
    old = a.set_hash()
    _enable_risky(auth_client, a.enabled_ids() + ['cover.open_cover'])
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': a.enabled_ids(), 'set_hash': old, 'risk_ack': True})
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'ceiling_changed'


def test_operator_words_are_validated_and_removal_is_a_widening(auth_client):
    for bad in ('Stop', 'ab', 'a' * 33, 'has space', 'dash-word', 'stop'):
        assert auth_client.post('/api/allowances/words',
                                json={'word': bad}).status_code == 400
    assert auth_client.post('/api/allowances/words',
                            json={'word': 'jellyfin'}).status_code == 200
    assert 'jellyfin' in a.operator_words()
    r = auth_client.delete('/api/allowances/words/jellyfin')
    assert r.status_code == 200
    assert 'jellyfin' not in a.operator_words()
    # Removal is a widening; it does not re-enable anything by itself.
    assert a.ceiling_allows('ha', 'cover', 'open_cover') is False


def test_every_change_is_audited(auth_client):
    from db.audit import get_audit_log
    _enable_risky(auth_client, a.enabled_ids() + ['cover.open_cover'])
    types = [r['event_type'] for r in get_audit_log(50)]
    assert 'allowance_ceiling_changed' in types
    row = [r for r in get_audit_log(50)
           if r['event_type'] == 'allowance_ceiling_changed'][0]
    assert 'cover.open_cover' in row['details']
    assert a.set_hash() in row['details']


def test_the_api_needs_a_session(client):
    assert client.get('/api/allowances/ceiling').status_code == 401
    assert client.put('/api/allowances/ceiling',
                      json={'enabled': []}).status_code == 401


def test_turning_the_ceiling_notify_off_is_audited(auth_client):
    from db.audit import get_audit_log
    from db.misc import set_notify_config
    set_notify_config('', 'jit,ceiling')
    r = auth_client.put('/api/notify-config', json={'url': '', 'events': 'jit'})
    assert r.status_code == 200
    assert 'notify_ceiling_off' in [x['event_type'] for x in get_audit_log(50)]


def test_a_put_without_set_hash_is_a_400(auth_client):
    """BX6: the stale-form check belongs to the server, not the UI."""
    r = auth_client.put('/api/allowances/ceiling',
                        json={'enabled': a.enabled_ids()})
    assert r.status_code == 400
    assert 'set_hash' in r.text
    # The checked write still needed no risk ack: nothing was enabled.
    assert r.json()['detail']['code'] == 'set_hash_required'


def test_two_saves_cannot_both_pass_the_compare(auth_client):
    """BX6: compare and write are one BEGIN IMMEDIATE.

    The second save presents the hash the first one started from; it must see
    the new set and get a 409 instead of overwriting it.
    """
    first = a.set_hash()
    ids = a.enabled_ids() + ['cover.open_cover']
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'set_hash': first})
    assert r.status_code == 200, r.text
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids + ['cover.close_cover'], 'risk_ack': True,
        'set_hash': first})
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'ceiling_changed'
    assert 'cover.close_cover' not in a.enabled_ids()


def test_the_same_ceiling_rule_serves_both_call_styles(auth_client, monkeypatch):
    """One deny rule, whether the caller supplies a transaction or not."""
    from db.core import db_conn
    import core.allowances as mod

    a.ensure_initialised()
    extra = {'domain': 'selftest', 'label': 'Self-test', 'default': False,
             'risk': '', 'actions': {'stop_now': 'Stop now'}}
    monkeypatch.setattr(mod, 'CATALOGUE', list(mod.CATALOGUE) + [extra])
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO allowance_ceiling "
            "(integration, catalogue_id, enabled, updated_by, updated_at) "
            "VALUES ('ha', 'selftest.stop_now', 1, 'test', 0)")
        conn.commit()
        # A built-in destructive word (`stop`) is refused by both styles.
        assert a.ceiling_allows('ha', 'selftest', 'stop_now') is False
        assert a.ceiling_allows('ha', 'selftest', 'stop_now', conn=conn) is False

        # An operator never-word also wins over the built-in stop exemption.
        conn.execute(
            "INSERT OR REPLACE INTO allowance_never_words "
            "(word, added_by, added_at) VALUES ('stop', 'test', 0)")
        conn.commit()
        assert a.ceiling_allows('ha', 'media_player', 'media_stop') is False
        assert a.ceiling_allows('ha', 'media_player', 'media_stop',
                                conn=conn) is False


def test_is_destructive_and_the_ceiling_rule_agree(auth_client):
    from db.core import db_conn
    a.ensure_initialised()
    with db_conn() as conn:
        operator = {r['word'] for r in conn.execute(
            'SELECT word FROM allowance_never_words').fetchall()}
    for service, exempt in (('stop_cover', ('stop',)),
                            ('media_stop', ('stop',)),
                            ('turn_off', ()),
                            ('restart_container', ()),
                            ('open_cover', ())):
        assert a.is_destructive(service, exempt) == a._ceiling_denied(
            service, exempt, operator), service
