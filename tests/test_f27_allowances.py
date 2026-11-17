"""F27: allowance conditions, matching, rate and profiles."""
import json
import time

import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token


@pytest.fixture
def agent_id():
    _raw, aid = create_agent_token('household')
    return aid


@pytest.fixture
def ha(monkeypatch):
    ents = {
        'cover.garage': {'id': 'reg-1', 'labels': []},
        'cover.blind': {'id': 'reg-2', 'labels': ['lbl-1']},
        'light.lamp': {'id': 'reg-3', 'labels': []},
    }
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    return ents


def _open_cover(auth_client):
    ids = a.enabled_ids() + ['cover.open_cover', 'cover.close_cover']
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True,
        'set_hash': a.set_hash()})
    assert r.status_code == 200, r.text


def _make(auth_client, agent_id, entities=('cover.garage',), risk_ack=True,
          **kw):
    body = {'agent_id': agent_id, 'name': 'Garage', 'domain': 'cover',
            'service': 'open_cover', 'entities': list(entities),
            'risk_ack': risk_ack}
    body.update(kw)
    return auth_client.post('/api/allowances', json=body)


def _profile_version(c, pid):
    return [p for p in c.get('/api/allowances/profiles').json()
            if p['id'] == pid][0]['version']


def _apply(c, pid, agent_id, risk_ack=True, version=None):
    if version is None:
        version = _profile_version(c, pid)
    return c.post('/api/allowances/profiles/%d/apply' % pid,
                  json={'agent_id': agent_id, 'risk_ack': risk_ack,
                        'profile_version': version})


def test_an_exact_match_runs_and_counts_one_use(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = _make(auth_client, agent_id)
    assert r.status_code == 200, r.text
    aid = r.json()['id']
    hit = a.match_and_claim({'id': agent_id}, 'ha', 'call_service',
                            {'domain': 'cover', 'service': 'open_cover',
                             'data': {'entity_id': 'cover.garage'}})
    assert hit == (aid, 'Garage')
    from db.core import db_conn
    with db_conn() as conn:
        n = conn.execute('SELECT COUNT(*) AS n FROM allowance_uses').fetchone()['n']
    assert n == 1


def test_every_miss_is_a_card_not_a_denial(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    agent = {'id': agent_id}
    base = {'domain': 'cover', 'service': 'open_cover',
            'data': {'entity_id': 'cover.garage'}}
    assert a.match_and_claim(agent, 'ha', 'call_service', base)
    # An extra data key, an unlisted entity and a list with one stranger.
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage', 'brightness': 2}}) is None
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.blind'}}) is None
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': ['cover.garage', 'cover.blind']}}) is None
    # AZ1: an expanding target is a card.
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'all'}}) is None
    assert a.match_and_claim(agent, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage'},
        'target': {'area_id': 'kitchen'}}) is None


def test_rate_and_expiry(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = _make(auth_client, agent_id, rate_per_hour=2)
    assert r.status_code == 200, r.text
    args = {'domain': 'cover', 'service': 'open_cover',
            'data': {'entity_id': 'cover.garage'}}
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args)
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args)
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args) is None


def test_window_is_wall_clock(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = _make(auth_client, agent_id, window='00:00-00:01')
    assert r.status_code == 200, r.text
    noon = int(time.mktime((2026, 6, 1, 12, 0, 0, 0, 0, -1)))
    assert a._in_window('00:00-00:01', noon) is False
    assert a._in_window('11:00-13:00', noon) is True
    args = {'domain': 'cover', 'service': 'open_cover',
            'data': {'entity_id': 'cover.garage'}}
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args) is None


def test_two_matches_get_a_card(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    _make(auth_client, agent_id)
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage'}}) is None


def test_a_disabled_ceiling_stops_the_match(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    # Disable the row again: the next call is a card even though the row lives.
    ids = [cid for cid in a.enabled_ids() if not cid.startswith('cover.')]
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'confirm': True, 'set_hash': a.set_hash()})
    assert r.status_code == 200, r.text
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage'}}) is None


def test_the_second_risk_ack_is_server_checked(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = _make(auth_client, agent_id, risk_ack=False)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'risk_ack_required'


def test_profiles_create_allowances_and_resolve_a_label(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'Garage',
        'templates': [{'domain': 'cover', 'service': 'open_cover',
                       'label_id': 'lbl-1', 'name': 'Blind'}]})
    assert r.status_code == 200, r.text
    pid = r.json()['id']
    r = _apply(auth_client, pid, agent_id)
    assert r.status_code == 200, r.text
    assert r.json()['created'][0]['name'] == 'Blind'
    hit = a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.blind'}})
    assert hit and hit[1] == 'Blind'


def test_az2_an_approved_write_suspends_by_entity(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    out = a.on_config_write('operator', '/config/switches.yaml',
                            'switch:\n  - name: Garage\n    command: x\n')
    assert out['suspended'] == 1
    assert a.list_allowances()[0]['suspended_at']


def test_az2_an_incomplete_mapping_suspends_all(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    out = a.on_config_write('operator', '/config/packages/x.yaml',
                            '!include_dir_named integrations\n')
    assert out['reason'] == 'config_write_unmapped'
    assert out['suspended'] == 1


def test_a_label_leaver_drops_and_a_joiner_waits(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'Blinds',
        'templates': [{'domain': 'cover', 'service': 'open_cover',
                       'label_id': 'lbl-1'}]})
    pid = r.json()['id']
    r = _apply(auth_client, pid, agent_id)
    assert r.status_code == 200, r.text
    aid = r.json()['created'][0]['id']
    args = {'domain': 'cover', 'service': 'open_cover',
            'data': {'entity_id': 'cover.blind'}}
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args)
    ha['cover.blind']['labels'] = []          # left the label
    a.refresh_labels()
    row = a.get_allowance(aid)
    assert json.loads(row['entities_json']) == []
    # BX1: the leaver is gone from the *match*, not only from the column.
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', args) is None
    ha['cover.blind']['labels'] = ['lbl-1']    # joined again
    ha['cover.garage']['labels'] = ['lbl-1']   # a new member
    a.refresh_labels()
    row = a.get_allowance(aid)
    pending = json.loads(row['pending_json'])
    assert {p['entity_id'] for p in pending} == {'cover.blind', 'cover.garage'}
    # A held joiner does not match until accepted.
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage'}}) is None
    # BX5: the risky cover row needs the second confirmation for joiners.
    with pytest.raises(a.AllowanceError) as exc:
        a.accept_join('operator', aid)
    assert exc.value.extra['code'] == 'risk_ack_required'
    out = a.accept_join('operator', aid, risk_ack=True)
    assert set(out['accepted']) == {'cover.blind', 'cover.garage'}
    assert a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.garage'}})


def test_a_changed_registry_id_suspends(auth_client, agent_id, ha):
    _open_cover(auth_client)
    # BX2: the plain-entity half was switched off before the fix.
    plain = _make(auth_client, agent_id).json()['id']
    ha['cover.garage']['id'] = 'reg-changed'
    a.refresh_labels()
    assert a.get_allowance(plain)['suspended_reason'] == 'registry_changed'
    # And the label half still holds.
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [{'domain': 'cover',
                                    'service': 'open_cover',
                                    'label_id': 'lbl-1'}]})
    aid = _apply(auth_client, r.json()['id'], agent_id).json()['created'][0]['id']
    ha['cover.blind']['id'] = 'reg-changed'
    a.refresh_labels()
    assert a.get_allowance(aid)['suspended_reason'] == 'registry_changed'


def test_the_update_agents_diff_shows_revoke_and_add(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [{'domain': 'cover',
                                    'service': 'open_cover',
                                    'entities': ['cover.garage']}]})
    pid = r.json()['id']
    _apply(auth_client, pid, agent_id)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'profile_id': pid,
        'templates': [{'domain': 'cover', 'service': 'open_cover',
                       'entities': ['cover.garage']},
                      {'domain': 'cover', 'service': 'close_cover',
                       'entities': ['cover.garage']}]})
    assert r.status_code == 200, r.text
    d = auth_client.get('/api/allowances/profiles/%d/diff?agent_id=%d' % (pid, agent_id)).json()
    assert len(d['revoke']) == 1 and len(d['add']) == 2
    assert d['up_to_date'] is False
    # Nothing changed until apply.
    assert len(a.list_allowances()) == 1
    r = _apply(auth_client, pid, agent_id)
    assert len(r.json()['created']) == 2
    assert len(a.list_allowances()) == 2



# ── BX2: the pin check covers plain entities and runs after a registry write ─

def test_a_registry_write_runs_the_pin_check_immediately(auth_client, agent_id, ha):
    _open_cover(auth_client)
    aid = _make(auth_client, agent_id).json()['id']
    ha['cover.garage']['id'] = 'reg-renamed'
    from core import tool_runner
    tool_runner.note_ha_registry_write(
        {'kind': 'ha'}, 'config/entity_registry/update')
    assert a.get_allowance(aid)['suspended_reason'] == 'registry_changed'


def test_registry_write_path_matching():
    assert a.is_registry_write_path('config/entity_registry/update')
    assert a.is_registry_write_path('/api/config/entity_registry/update')
    assert a.is_registry_write_path('config/config_entries/options/flow')
    assert not a.is_registry_write_path('config/automation/config/1')
    assert not a.is_registry_write_path('/api/services/light/turn_on')


# ── BX3: AZ2 restore, fail-closed scan, modern template form ────────────────

def test_az2_a_restore_suspends_every_allowance(auth_client, agent_id, ha,
                                                monkeypatch):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    from core import ha_config_ssh
    from core.ha_config_handlers import _h_ha_config_restore
    monkeypatch.setattr(ha_config_ssh, 'restore_config',
                        lambda *a, **k: {'ok': True, 'path': 'x'})
    out = _h_ha_config_restore('ha', 'ha_config_restore', {
        'path': '/config/configuration.yaml',
        'backup': '/config/configuration.yaml.eshu-1234567890.bak'})
    assert 'error' not in out
    row = a.list_allowances()[0]
    assert row['suspended_reason'] == 'config_write_unmapped'


def test_az2_a_scan_that_raises_suspends_every_allowance(auth_client, agent_id,
                                                         ha, monkeypatch):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    from core import ha_config_ssh
    from core import allowances as mod

    def boom(*a, **k):
        raise RuntimeError('scan exploded')
    monkeypatch.setattr(ha_config_ssh, 'write_config',
                        lambda *a, **k: {'ok': True, 'path': 'x'})
    monkeypatch.setattr(mod, 'on_config_write', boom)
    from core.ha_config_handlers import _h_ha_config_write
    out = _h_ha_config_write('ha', 'ha_config_write', {
        'path': '/config/configuration.yaml', 'content': 'x'})
    assert 'error' not in out
    assert a.list_allowances()[0]['suspended_reason'] == 'config_write_unmapped'
    from db.audit import get_audit_log
    assert 'allowance_config_write_scanned' in [
        r['event_type'] for r in get_audit_log(50)]


def test_az2_modern_template_form_is_unmapped(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    out = a.on_config_write(
        'operator', '/config/templates.yaml',
        'template:\n  - switch:\n      name: X\n      turn_on:\n'
        '        service: script.x\n')
    assert out['reason'] == 'config_write_unmapped'
    assert out['suspended'] == 1
    assert a.list_allowances()[0]['suspended_reason'] == 'config_write_unmapped'


def test_az2_a_dashed_domain_key_is_unmapped(auth_client, agent_id, ha):
    _open_cover(auth_client)
    _make(auth_client, agent_id)
    out = a.on_config_write('operator', '/config/switches.yaml',
                            '- switch:\n    name: X\n')
    assert out['reason'] == 'config_write_unmapped'
    assert out['suspended'] == 1


# ── BX4: apply validates first, then revokes and inserts atomically ─────────

def test_apply_profile_refuses_before_revoking(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']}]})
    pid = r.json()['id']
    assert _apply(auth_client, pid, agent_id).status_code == 200
    assert len(a.list_allowances()) == 1
    # A new version adds a risky row. Applying without the second ack must
    # refuse before the existing allowance is revoked.
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'profile_id': pid, 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']},
            {'domain': 'cover', 'service': 'close_cover',
             'entities': ['cover.garage']}]})
    assert r.status_code == 200, r.text
    r = _apply(auth_client, pid, agent_id, risk_ack=False)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'risk_ack_required'
    rows = a.list_allowances()
    assert len(rows) == 1 and rows[0]['revoked_at'] == 0


def test_apply_profile_is_bound_to_the_previewed_version(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']}]})
    pid = r.json()['id']
    assert _apply(auth_client, pid, agent_id, version=1).status_code == 200
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'profile_id': pid, 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']},
            {'domain': 'cover', 'service': 'close_cover',
             'entities': ['cover.garage']}]})
    assert r.status_code == 200, r.text
    r = _apply(auth_client, pid, agent_id, version=1)
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'profile_changed'
    assert len(a.list_allowances()) == 1


def test_apply_profile_requires_a_version(auth_client, agent_id, ha):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']}]})
    r = _apply(auth_client, r.json()['id'], agent_id, version=0)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'profile_version_required'


def test_apply_profile_rolls_back_the_revoke_when_an_insert_fails(
        auth_client, agent_id, ha, monkeypatch):
    _open_cover(auth_client)
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']}]})
    pid = r.json()['id']
    assert _apply(auth_client, pid, agent_id).status_code == 200
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'P', 'profile_id': pid, 'templates': [
            {'domain': 'cover', 'service': 'open_cover',
             'entities': ['cover.blind']},
            {'domain': 'cover', 'service': 'close_cover',
             'entities': ['cover.garage']}]})
    assert r.status_code == 200, r.text
    original = a._insert_allowance
    calls = {'n': 0}

    def flaky(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('insert failed')
        return original(*args, **kwargs)
    monkeypatch.setattr(a, '_insert_allowance', flaky)
    with pytest.raises(RuntimeError):
        a.apply_profile('operator', pid, agent_id, risk_ack=True,
                        profile_version=2)
    rows = a.list_allowances()
    assert len(rows) == 1 and rows[0]['revoked_at'] == 0


# ── BX5: accept-join runs the cap and the ceiling, not just the pins ────────

def test_accept_join_applies_the_64_entity_cap(auth_client, agent_id, ha):
    for i in range(64):
        ha['light.e%d' % i] = {'id': 'reg-l%d' % i, 'labels': []}
    r = auth_client.post('/api/allowances', json={
        'agent_id': agent_id, 'name': 'Big', 'domain': 'light',
        'service': 'turn_on',
        'entities': ['light.e%d' % i for i in range(64)]})
    assert r.status_code == 200, r.text
    aid = r.json()['id']
    ha['light.extra'] = {'id': 'reg-extra', 'labels': []}
    from db.core import db_conn
    with db_conn() as conn:
        conn.execute(
            'UPDATE allowances SET pending_json = ? WHERE id = ?',
            (json.dumps([{'entity_id': 'light.extra',
                          'registry_id': 'reg-extra'}]), aid))
        conn.commit()
    with pytest.raises(a.AllowanceError) as exc:
        a.accept_join('operator', aid, risk_ack=True)
    assert exc.value.status == 400
    assert '64' in str(exc.value)


def test_accept_join_checks_the_ceiling(auth_client, agent_id, ha):
    ha['light.kitchen'] = {'id': 'reg-k', 'labels': ['lbl-l']}
    r = auth_client.post('/api/allowances', json={
        'agent_id': agent_id, 'name': 'Lamp', 'domain': 'light',
        'service': 'turn_on', 'entities': ['light.lamp']})
    assert r.status_code == 200, r.text
    aid = r.json()['id']
    from db.core import db_conn
    with db_conn() as conn:
        conn.execute(
            'UPDATE allowances SET pending_json = ? WHERE id = ?',
            (json.dumps([{'entity_id': 'light.kitchen',
                          'registry_id': 'reg-k'}]), aid))
        conn.commit()
    # Turn the row off after the allowance was saved.
    ids = [cid for cid in a.enabled_ids() if not cid.startswith('light.')]
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'set_hash': a.set_hash()})
    assert r.status_code == 200, r.text
    with pytest.raises(a.AllowanceError) as exc:
        a.accept_join('operator', aid, risk_ack=True)
    assert exc.value.status == 400
    assert 'ceiling' in str(exc.value).lower()


# ── Nits: the operator word is never exempt ─────────────────────────────────

def test_an_operator_word_disables_even_an_exempt_row(auth_client, monkeypatch):
    from core import allowances as mod
    ids = a.enabled_ids() + ['cover.stop_cover']
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'set_hash': a.set_hash()})
    assert r.status_code == 200, r.text
    # Simulate `stop` not being built in, so add_word accepts it as an
    # operator word. is_destructive() then says it is never exempt.
    monkeypatch.setattr(
        mod, 'DESTRUCTIVE_WORDS',
        tuple(w for w in mod.DESTRUCTIVE_WORDS if w != 'stop'))
    out = a.add_word('operator', 'stop')
    assert 'cover.stop_cover' in out['disabled']
    assert 'media_player.media_stop' in out['disabled']
    assert a.ceiling_allows('ha', 'cover', 'stop_cover') is False
