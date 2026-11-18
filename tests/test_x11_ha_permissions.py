"""X11/S2: set an agent's HA permissions in one go."""
import copy
import time

import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token
from db.core import db_conn
from db.integrations import create_integration, get_integration, update_integration


ENTITIES = {
    'switch.kettle': {'id': 'reg-kettle', 'labels': []},
    'switch.lamp': {'id': 'reg-lamp', 'labels': ['lbl-plugs']},
    'switch.other': {'id': 'reg-other', 'labels': []},
    'light.hall': {'id': 'reg-hall', 'labels': []},
    'cover.garage': {'id': 'reg-garage', 'labels': []},
}


@pytest.fixture
def agent_id():
    _raw, aid = create_agent_token('household')
    return aid


@pytest.fixture
def ha(monkeypatch):
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', '', kind='ha')
    ents = copy.deepcopy(ENTITIES)
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(
        a, '_risky_classes',
        lambda entities: ([{'entity_id': 'cover.garage',
                            'device_class': 'garage'}]
                          if 'cover.garage' in entities else []))
    return ents


def _put(c, agent_id, permissions, version=None, preview=False,
         risk_ack=False, preview_hash=None, resume_ack=False):
    if not preview and preview_hash is None:
        previewed = _put(c, agent_id, permissions, version=version,
                         preview=True, risk_ack=risk_ack)
        if previewed.status_code == 200:
            preview_hash = previewed.json().get('preview_hash') or ''
    return c.put('/api/agents/%d/ha-permissions' % agent_id, json={
        'permissions': permissions, 'version': version,
        'preview': preview, 'risk_ack': risk_ack,
        'preview_hash': preview_hash or '', 'resume_ack': resume_ack})


def _get(c, agent_id):
    r = c.get('/api/agents/%d/ha-permissions' % agent_id)
    assert r.status_code == 200, r.text
    return r.json()


def _perm(entities, service='turn_off', domain='switch', **kw):
    body = {'name': 'Plugs', 'domain': domain, 'service': service,
            'entities': entities}
    body.update(kw)
    return body


def test_preview_then_apply_and_an_unchanged_round_trip(
        auth_client, agent_id, ha):
    perms = [_perm(['switch.kettle', 'switch.lamp'])]
    r = _put(auth_client, agent_id, perms, version=1, preview=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['preview'] is True and body['changed'] is True
    assert 'household will be able to turn off 2 devices' in body['summary']
    assert body['risk_ack_required'] is False
    assert _get(auth_client, agent_id)['permissions'] == []

    r = _put(auth_client, agent_id, perms, version=1)
    assert r.status_code == 200, r.text
    assert r.json()['version'] == 2
    got = _get(auth_client, agent_id)
    assert len(got['permissions']) == 1
    assert got['permissions'][0]['rate_per_hour'] == 30
    assert got['permissions'][0]['window'] == ''
    first_id = got['permissions'][0]['id']

    r = _put(auth_client, agent_id, perms, version=2)
    assert r.status_code == 200, r.text
    assert r.json()['changed'] is False
    assert r.json()['version'] == 2
    assert r.json()['summary'] == 'household is unchanged.'
    assert _get(auth_client, agent_id)['permissions'][0]['id'] == first_id


def test_a_stale_version_is_409(auth_client, agent_id, ha):
    _put(auth_client, agent_id, [_perm(['switch.kettle'])], version=1)
    r = _put(auth_client, agent_id, [_perm(['switch.lamp'])], version=1)
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'config_changed'
    assert r.json()['detail']['version'] == 2


def test_an_invalid_item_leaves_everything_as_it_was(
        auth_client, agent_id, ha):
    _put(auth_client, agent_id, [_perm(['switch.kettle'])], version=1)
    before = _get(auth_client, agent_id)
    a.set_protected_entities('operator', ['switch.other'])
    r = _put(auth_client, agent_id,
             [_perm(['switch.lamp']), _perm(['switch.other'])], version=2)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'protected_device'
    after = _get(auth_client, agent_id)
    assert after == before


def test_unchanged_rows_keep_id_and_uses_and_changed_rows_get_a_version(
        auth_client, agent_id, ha):
    first = [_perm(['switch.kettle'], name='Kettle'),
             _perm(['switch.lamp'], name='Lamp')]
    assert _put(auth_client, agent_id, first, version=1).status_code == 200
    got = _get(auth_client, agent_id)
    by_name = {p['name']: p for p in got['permissions']}
    kettle_id = by_name['Kettle']['id']
    lamp_id = by_name['Lamp']['id']
    with db_conn() as conn:
        conn.execute('INSERT INTO allowance_uses (allowance_id, used_at, call_ref) '
                     'VALUES (?, ?, ?)', (kettle_id, 1, 'test'))
        conn.commit()
    second = [_perm(['switch.kettle'], name='Kettle'),
              _perm(['switch.lamp'], name='Lamp', rate_per_hour=60)]
    r = _put(auth_client, agent_id, second, version=got['version'])
    assert r.status_code == 200, r.text
    after = {p['name']: p for p in _get(auth_client, agent_id)['permissions']}
    assert after['Kettle']['id'] == kettle_id
    assert after['Lamp']['id'] != lamp_id
    assert after['Lamp']['version'] == by_name['Lamp']['version'] + 1
    with db_conn() as conn:
        n = conn.execute('SELECT COUNT(*) AS n FROM allowance_uses '
                         'WHERE allowance_id = ?', (kettle_id,)).fetchone()['n']
    assert n == 1


def test_risk_is_listed_in_the_preview_and_needs_the_ack(
        auth_client, agent_id, ha):
    ids = a.enabled_ids() + ['cover.open_cover']
    r = auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True,
        'set_hash': a.set_hash()})
    assert r.status_code == 200, r.text
    perm = _perm(['cover.garage'], domain='cover', service='open_cover')
    preview = _put(auth_client, agent_id, [perm], version=1, preview=True)
    assert preview.status_code == 200, preview.text
    assert preview.json()['risk_ack_required'] is True
    assert any(x['kind'] == 'device' and x['entity_id'] == 'cover.garage'
               for x in preview.json()['risky'])
    refused = _put(auth_client, agent_id, [perm], version=1)
    assert refused.status_code == 400
    assert refused.json()['detail']['code'] == 'risk_ack_required'
    accepted = _put(auth_client, agent_id, [perm], version=1, risk_ack=True)
    assert accepted.status_code == 200, accepted.text


def test_a_label_entry_is_stored_as_a_label_and_follows_joiners(
        auth_client, agent_id, ha):
    perm = {'name': 'Label plugs', 'domain': 'switch', 'service': 'turn_off',
            'label_id': 'lbl-plugs'}
    r = _put(auth_client, agent_id, [perm], version=1)
    assert r.status_code == 200, r.text
    got = _get(auth_client, agent_id)['permissions'][0]
    assert got['label_id'] == 'lbl-plugs'
    assert got['entities'] == ['switch.lamp']
    ha['switch.other']['labels'] = ['lbl-plugs']
    a.refresh_labels()
    aid = got['id']
    import json
    assert 'switch.other' in {
        p['entity_id'] for p in json.loads(a.get_allowance(aid)['pending_json'])}


def test_no_ha_integration_refuses_the_save(auth_client, agent_id, monkeypatch):
    from db.integrations import delete_integration
    # Remove the fixture's integration by name even if this test reuses it.
    delete_integration('Home Assistant')
    r = _put(auth_client, agent_id, [_perm(['switch.kettle'])], version=1)
    assert r.status_code == 503


def test_duplicate_and_overlapping_permissions_are_refused(
        auth_client, agent_id, ha):
    duplicate = [_perm(['switch.kettle']), _perm(['switch.kettle'])]
    r = _put(auth_client, agent_id, duplicate, version=1)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'duplicate_permission'
    overlap = [_perm(['switch.kettle', 'switch.lamp']),
               _perm(['switch.lamp', 'switch.other'])]
    r = _put(auth_client, agent_id, overlap, version=1)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'overlapping_permission'
    assert _get(auth_client, agent_id)['permissions'] == []


def test_editing_a_permission_keeps_its_expiry(auth_client, agent_id, ha):
    expires = int(time.time()) + 3600
    perm = _perm(['switch.kettle'], expires_at=expires)
    assert _put(auth_client, agent_id, [perm], version=1).status_code == 200
    before = _get(auth_client, agent_id)['permissions'][0]
    assert before['expires_at'] == expires
    changed = _perm(['switch.kettle'], rate_per_hour=60)
    r = _put(auth_client, agent_id, [changed], version=2)
    assert r.status_code == 200, r.text
    after = _get(auth_client, agent_id)['permissions'][0]
    assert after['id'] != before['id']
    assert after['rate_per_hour'] == 60
    assert after['expires_at'] == expires
    # An explicit 0 clears the expiry; it is not the absent-field fallback.
    cleared = _perm(['switch.kettle'], rate_per_hour=60, expires_at=0)
    r = _put(auth_client, agent_id, [cleared], version=3)
    assert r.status_code == 200, r.text
    assert _get(auth_client, agent_id)['permissions'][0]['expires_at'] == 0


def test_a_new_permission_gets_the_defaults_and_a_changed_one_keeps_fields(
        auth_client, agent_id, ha):
    expires = int(time.time()) + 7200
    new = _put(auth_client, agent_id, [_perm(['switch.kettle'])], version=1)
    assert new.status_code == 200, new.text
    created = _get(auth_client, agent_id)['permissions'][0]
    assert (created['rate_per_hour'], created['window'],
            created['expires_at']) == (30, '', 0)

    seeded = _perm(['switch.kettle', 'switch.lamp'], rate_per_hour=5,
                   window='07:00-23:00', expires_at=expires)
    assert _put(auth_client, agent_id, [seeded], version=2).status_code == 200
    before = _get(auth_client, agent_id)['permissions'][0]
    # Only entities are named; rate, window and expiry are absent and must
    # keep their stored values rather than fall back to the new defaults.
    changed = _perm(['switch.kettle', 'switch.lamp', 'switch.other'])
    r = _put(auth_client, agent_id, [changed], version=3)
    assert r.status_code == 200, r.text
    after = _get(auth_client, agent_id)['permissions'][0]
    assert after['id'] != before['id']
    assert after['rate_per_hour'] == 5
    assert after['window'] == '07:00-23:00'
    assert after['expires_at'] == expires


def test_an_invalid_expiry_is_refused(auth_client, agent_id, ha):
    r = _put(auth_client, agent_id,
             [_perm(['switch.kettle'], expires_at=-1)], version=1)
    assert r.status_code == 400
    assert 'expires_at' in r.text


def test_an_apply_without_a_preview_hash_is_refused(auth_client, agent_id, ha):
    r = auth_client.put('/api/agents/%d/ha-permissions' % agent_id, json={
        'permissions': [_perm(['switch.kettle'])], 'version': 1})
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'preview_hash_required'


def test_an_unchanged_preview_and_apply_with_the_hash_passes(
        auth_client, agent_id, ha):
    preview = _put(auth_client, agent_id, [], version=1, preview=True)
    assert preview.status_code == 200, preview.text
    r = _put(auth_client, agent_id, [], version=1,
             preview_hash=preview.json()['preview_hash'])
    assert r.status_code == 200, r.text
    assert r.json()['changed'] is False


def test_a_label_member_added_after_preview_is_409(auth_client, agent_id, ha):
    ha['switch.lamp']['labels'] = ['lbl-1']
    perm = {'name': 'Label', 'domain': 'switch', 'service': 'turn_off',
            'label_id': 'lbl-1'}
    preview = _put(auth_client, agent_id, [perm], version=1, preview=True)
    assert preview.status_code == 200, preview.text
    ha['switch.other']['labels'] = ['lbl-1']
    r = _put(auth_client, agent_id, [perm], version=1,
             preview_hash=preview.json()['preview_hash'])
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'preview_changed'
    assert _get(auth_client, agent_id)['permissions'] == []


def test_a_risky_device_appearing_after_preview_is_409(
        auth_client, agent_id, ha, monkeypatch):
    ids = a.enabled_ids() + ['cover.open_cover']
    assert auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True,
        'set_hash': a.set_hash()}).status_code == 200
    on = {'value': False}
    monkeypatch.setattr(
        a, '_risky_classes',
        lambda entities: ([{'entity_id': 'cover.garage',
                            'device_class': 'garage'}]
                          if on['value'] and 'cover.garage' in entities else []))
    perm = _perm(['cover.garage'], domain='cover', service='open_cover')
    preview = _put(auth_client, agent_id, [perm], version=1, preview=True)
    assert preview.status_code == 200, preview.text
    on['value'] = True
    r = _put(auth_client, agent_id, [perm], version=1,
             preview_hash=preview.json()['preview_hash'])
    assert r.status_code == 409
    assert r.json()['detail']['code'] == 'preview_changed'


def test_a_rate_only_label_edit_lists_waiting_joiners(
        auth_client, agent_id, ha):
    ha['switch.lamp']['labels'] = ['lbl-1']
    perm = {'name': 'Label', 'domain': 'switch', 'service': 'turn_off',
            'label_id': 'lbl-1'}
    assert _put(auth_client, agent_id, [perm], version=1).status_code == 200
    ha['switch.other']['labels'] = ['lbl-1']
    a.refresh_labels()
    version = _get(auth_client, agent_id)['version']
    preview = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                   version=version, preview=True)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    waiting = [p['entity_id'] for row in body['waiting']
               for p in row['entities']]
    assert waiting == ['switch.other']
    assert 'waiting to join' in body['summary']


def _suspend_by_registry_change(agent_id, ha):
    row = _get  # keep flake-free: this helper only mutates HA
    ha['switch.kettle']['id'] = 'reg-kettle-new'
    a.refresh_labels()
    return row


def test_a_registry_changed_edit_needs_resume_ack_and_resumes(
        auth_client, agent_id, ha):
    perm = _perm(['switch.kettle'])
    assert _put(auth_client, agent_id, [perm], version=1).status_code == 200
    ha['switch.kettle']['id'] = 'reg-kettle-new'
    a.refresh_labels()
    got = _get(auth_client, agent_id)
    assert got['permissions'][0]['suspended_reason'] == 'registry_changed'
    version = got['version']
    preview = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                   version=version, preview=True)
    body = preview.json()
    assert body['resume_ack_required'] is True
    assert body['resumes'][0]['reason'] == 'registry_changed'
    refused = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                   version=version, preview_hash=body['preview_hash'])
    assert refused.status_code == 400
    assert refused.json()['detail']['code'] == 'resume_ack_required'
    accepted = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                    version=version, preview_hash=body['preview_hash'],
                    resume_ack=True)
    assert accepted.status_code == 200, accepted.text
    after = _get(auth_client, agent_id)['permissions'][0]
    assert after['suspended_at'] == 0
    assert after['rate_per_hour'] == 60


def test_an_ha_changed_edit_says_it_will_re_pin_and_does(
        auth_client, agent_id, ha):
    perm = _perm(['switch.kettle'])
    assert _put(auth_client, agent_id, [perm], version=1).status_code == 200
    update_integration('Home Assistant',
                       base_url='http://192.0.2.51:8123/api')
    a.refresh_labels()
    got = _get(auth_client, agent_id)
    assert got['permissions'][0]['suspended_reason'] == 'ha_changed'
    version = got['version']
    preview = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                   version=version, preview=True)
    body = preview.json()
    assert body['resumes'][0]['will_pin_to_current_ha'] is True
    accepted = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
                    version=version, preview_hash=body['preview_hash'],
                    resume_ack=True)
    assert accepted.status_code == 200, accepted.text
    after = _get(auth_client, agent_id)['permissions'][0]
    assert after['suspended_at'] == 0
    integration = get_integration('Home Assistant')
    assert after['rate_per_hour'] == 60
    from core import allowances as aa
    row = aa.get_allowance(after['id'])
    assert row['ha_base_url_fp'] == aa._ha_base_fp(integration['base_url'])


def test_a_protected_permission_edit_still_refuses(auth_client, agent_id, ha):
    perm = _perm(['switch.kettle'])
    assert _put(auth_client, agent_id, [perm], version=1).status_code == 200
    a.set_protected_entities('operator', ['switch.kettle'])
    version = _get(auth_client, agent_id)['version']
    r = _put(auth_client, agent_id, [dict(perm, rate_per_hour=60)],
             version=version, preview=True)
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'protected_device'


def test_apply_rechecks_the_ceiling_inside_the_transaction(
        auth_client, agent_id, ha, monkeypatch):
    perm = _perm(['switch.kettle'])
    preview = _put(auth_client, agent_id, [perm], version=1, preview=True)
    assert preview.status_code == 200, preview.text
    monkeypatch.setattr(a, '_ceiling_allows_conn', lambda *args: False)
    r = _put(auth_client, agent_id, [perm], version=1,
             preview_hash=preview.json()['preview_hash'])
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'ceiling_changed'
    assert _get(auth_client, agent_id)['permissions'] == []
