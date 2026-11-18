"""X11/S3: protected devices are a promise, checked at every path."""
import json

import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token
from db.integrations import create_integration, get_integration


@pytest.fixture
def agent_id():
    _raw, aid = create_agent_token('household')
    return aid


@pytest.fixture
def ha(monkeypatch):
    ents = {
        'switch.plug': {'id': 'reg-plug', 'labels': []},
        'switch.other': {'id': 'reg-other', 'labels': []},
        'switch.joiner': {'id': 'reg-joiner', 'labels': []},
    }
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', '', kind='ha')
    return ents


def _ha_row():
    return get_integration('Home Assistant')


def test_a_save_that_includes_a_protected_entity_is_refused(
        auth_client, agent_id, ha):
    a.set_protected_entities('operator', ['switch.plug'])
    r = auth_client.post('/api/allowances', json={
        'agent_id': agent_id, 'name': 'Plugs', 'domain': 'switch',
        'service': 'turn_off', 'entities': ['switch.plug']})
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'protected_device'
    assert 'switch.plug' in r.json()['detail']['entities']


def test_a_match_that_names_a_protected_device_is_refused(
        auth_client, agent_id, ha):
    from db.audit import get_audit_log
    from db.core import db_conn
    row = a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                             'turn_off', ['switch.plug'])
    assert row['id']
    a.set_protected_entities('operator', ['switch.plug'])
    assert a.get_allowance(row['id'])['suspended_reason'] == 'protected'
    # Simulate a stale grant that protection has not yet caught up with:
    # match itself must still refuse on the stored registry id.
    with db_conn() as conn:
        conn.execute("UPDATE allowances SET suspended_at = 0, "
                     "suspended_reason = '' WHERE id = ?", (row['id'],))
        conn.commit()
    hit = a.match_and_claim({'id': agent_id}, _ha_row(), 'call_service', {
        'domain': 'switch', 'service': 'turn_off',
        'data': {'entity_id': 'switch.plug'}})
    assert hit is None
    assert 'allowance_protected_refused' in [
        x['event_type'] for x in get_audit_log(20)]


def test_a_label_join_of_a_protected_device_is_dropped_with_an_audit_row(
        auth_client, agent_id, ha):
    from db.audit import get_audit_log
    a.ensure_initialised()
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'Label', 'templates': [{
            'domain': 'switch', 'service': 'turn_off', 'label_id': 'lbl-1'}]})
    assert r.status_code == 200, r.text
    ha['switch.other']['labels'] = ['lbl-1']
    pid = r.json()['id']
    applied = auth_client.post('/api/allowances/profiles/%d/apply' % pid, json={
        'agent_id': agent_id, 'profile_version': 1, 'risk_ack': False})
    assert applied.status_code == 200, applied.text
    aid = applied.json()['created'][0]['id']
    # A new device joins the label, but it is protected.
    ha['switch.joiner']['labels'] = ['lbl-1']
    a.set_protected_entities('operator', ['switch.joiner'])
    a.refresh_labels()
    row = a.get_allowance(aid)
    assert json.loads(row['pending_json']) == []
    assert 'allowance_protected_label_skipped' in [
        x['event_type'] for x in get_audit_log(30)]
    assert 'switch.joiner' not in {
        p['entity_id'] for p in json.loads(row['entities_json'])}


def test_the_protected_api_is_audited_and_notifies_on_removal(
        auth_client, ha, monkeypatch):
    from core import notify
    from db.audit import get_audit_log
    calls = []
    monkeypatch.setattr(notify, 'send_notify',
                        lambda *args, **kwargs: calls.append(args) or True)
    r = auth_client.put('/api/allowances/protected',
                        json={'entities': ['switch.plug']})
    assert r.status_code == 200, r.text
    got = auth_client.get('/api/allowances/protected').json()
    assert [x['entity_id'] for x in got['entities']] == ['switch.plug']
    events = [x['event_type'] for x in get_audit_log(20)]
    assert 'allowance_protected_added' in events
    r = auth_client.put('/api/allowances/protected', json={'entities': []})
    assert r.status_code == 200, r.text
    assert calls and 'switch.plug' in calls[0][2]
    events = [x['event_type'] for x in get_audit_log(20)]
    assert 'allowance_protected_removed' in events
    assert auth_client.put('/api/allowances/protected',
                           json={'entities': ['not an entity']}).status_code == 400


def test_protected_routes_are_operator_only(client):
    assert client.get('/api/allowances/protected').status_code == 401
    assert client.put('/api/allowances/protected',
                      json={'entities': []}).status_code == 401


def test_protection_bumps_only_agents_holding_an_affected_permission(
        auth_client, agent_id, ha):
    from db.agent_policies import get_agent_config_version
    a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                       'turn_off', ['switch.plug'])
    before = get_agent_config_version(agent_id)
    a.set_protected_entities('operator', ['switch.other'])
    assert get_agent_config_version(agent_id) == before
    a.set_protected_entities('operator', ['switch.plug', 'switch.other'])
    assert get_agent_config_version(agent_id) == before + 1


def test_protection_suspends_a_held_grant_and_unprotect_does_not_resume(
        auth_client, agent_id, ha):
    from db.agent_policies import get_agent_config_version
    from db.audit import get_audit_log
    row = a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                             'turn_off', ['switch.plug'])
    before_version = get_agent_config_version(agent_id)
    a.set_protected_entities('operator', ['switch.plug'])
    suspended = a.get_allowance(row['id'])
    assert suspended['suspended_at'] and suspended['suspended_reason'] == 'protected'
    assert get_agent_config_version(agent_id) == before_version + 1
    events = [x for x in get_audit_log(30)
              if x['event_type'] == 'allowance_suspended']
    assert any('#%d' % row['id'] in x['details'] for x in events)
    a.set_protected_entities('operator', [])
    still = a.get_allowance(row['id'])
    assert still['suspended_at'] == suspended['suspended_at']
    assert still['suspended_reason'] == 'protected'


def test_resume_refuses_while_any_device_is_still_protected(
        auth_client, agent_id, ha):
    row = a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                             'turn_off', ['switch.plug'])
    a.set_protected_entities('operator', ['switch.plug'])
    with pytest.raises(a.AllowanceError) as exc:
        a.resume_allowance('operator', row['id'])
    assert exc.value.extra['code'] == 'protected_device'
    a.set_protected_entities('operator', [])
    a.resume_allowance('operator', row['id'])
    assert a.get_allowance(row['id'])['suspended_at'] == 0


def test_a_renamed_protected_entity_is_refused_by_registry_id(
        auth_client, agent_id, ha):
    import json
    from db.core import db_conn
    row = a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                             'turn_off', ['switch.plug'])
    a.set_protected_entities('operator', ['switch.plug'])
    # Rename in HA: the entity id changes, the registry id does not.
    ha.pop('switch.plug')
    ha['switch.renamed'] = {'id': 'reg-plug', 'labels': []}
    with pytest.raises(a.AllowanceError) as exc:
        a.create_allowance('operator', agent_id, 'New name', 'switch',
                           'turn_off', ['switch.renamed'])
    assert exc.value.extra['code'] == 'protected_device'
    # Re-pin the old permission to the new entity id, keeping the registry
    # id: match refuses on the registry id even though the protected entity
    # id is different.
    pins = [{'entity_id': 'switch.renamed', 'registry_id': 'reg-plug'}]
    cond = {'domain': {'in': ['switch']}, 'service': {'in': ['turn_off']},
            'data.entity_id': {'in': ['switch.renamed']}}
    with db_conn() as conn:
        conn.execute("UPDATE allowances SET suspended_at = 0, "
                     "suspended_reason = '', entities_json = ?, "
                     "conditions_json = ? WHERE id = ?",
                     (json.dumps(pins), json.dumps(cond), row['id']))
        conn.commit()
    hit = a.match_and_claim({'id': agent_id}, _ha_row(), 'call_service', {
        'domain': 'switch', 'service': 'turn_off',
        'data': {'entity_id': 'switch.renamed'}})
    assert hit is None


def test_a_label_containing_a_protected_device_says_which_and_what_to_do(
        auth_client, agent_id, ha):
    ha['switch.other']['labels'] = ['lbl-1']
    a.set_protected_entities('operator', ['switch.other'])
    r = auth_client.put('/api/agents/%d/ha-permissions' % agent_id, json={
        'permissions': [{'name': 'Label', 'domain': 'switch',
                         'service': 'turn_off', 'label_id': 'lbl-1'}],
        'version': 1, 'preview': True})
    assert r.status_code == 400, r.text
    detail = r.json()['detail']
    assert detail['code'] == 'protected_device'
    assert 'switch.other' in detail['message']
    assert 'pick devices instead of the label' in detail['message']


def test_a_protected_name_without_a_hit_writes_no_refusal_audit(
        auth_client, agent_id, ha):
    from db.audit import get_audit_log
    a.create_allowance('operator', agent_id, 'Other', 'switch',
                       'turn_off', ['switch.other'])
    a.set_protected_entities('operator', ['switch.plug'])
    hit = a.match_and_claim({'id': agent_id}, _ha_row(), 'call_service', {
        'domain': 'switch', 'service': 'turn_off',
        'data': {'entity_id': 'switch.plug'}})
    assert hit is None
    assert 'allowance_protected_refused' not in [
        x['event_type'] for x in get_audit_log(30)]


def test_accept_join_refuses_a_protected_joiner(auth_client, agent_id, ha):
    a.ensure_initialised()
    ha['switch.plug']['labels'] = ['lbl-1']
    r = auth_client.post('/api/allowances/profiles', json={
        'name': 'Label', 'templates': [{
            'domain': 'switch', 'service': 'turn_off', 'label_id': 'lbl-1'}]})
    assert r.status_code == 200, r.text
    pid = r.json()['id']
    applied = auth_client.post('/api/allowances/profiles/%d/apply' % pid, json={
        'agent_id': agent_id, 'profile_version': 1, 'risk_ack': False})
    assert applied.status_code == 200, applied.text
    aid = applied.json()['created'][0]['id']
    ha['switch.other']['labels'] = ['lbl-1']
    a.refresh_labels()
    assert json.loads(a.get_allowance(aid)['pending_json'])
    a.set_protected_entities('operator', ['switch.other'])
    with pytest.raises(a.AllowanceError) as exc:
        a.accept_join('operator', aid)
    assert exc.value.extra['code'] == 'protected_device'
    assert 'switch.other' in exc.value.detail


def test_protection_with_ha_down_is_accepted_then_backfilled(
        auth_client, agent_id, ha):
    """Security answer (a): a tightening must not wait on HA being up."""
    from db.audit import get_audit_log
    a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                       'turn_off', ['switch.plug'])
    down = a.AllowanceError(503, 'Could not read the HA entity registry')
    original = a._ha_registry
    try:
        a._ha_registry = lambda: (_ for _ in ()).throw(down)
        result = a.set_protected_entities('operator', ['switch.plug'])
    finally:
        a._ha_registry = original
    assert result['added'] == ['switch.plug']
    rows = {r['entity_id']: r for r in a.protected_records()}
    assert rows['switch.plug']['registry_id'] == ''
    assert a.get_allowance(a.list_allowances()[0]['id'])['suspended_reason'] == 'protected'
    details = [r['details'] for r in get_audit_log(20)
               if r['event_type'] == 'allowance_protected_added']
    assert any('pending' in d and 'unreachable' in d for d in details)

    # The next successful refresh fills the registry id and audits it.
    a.refresh_labels()
    rows = {r['entity_id']: r for r in a.protected_records()}
    assert rows['switch.plug']['registry_id'] == 'reg-plug'
    events = [r for r in get_audit_log(20)
              if r['event_type'] == 'allowance_protected_registry_filled']
    assert events and 'reg-plug' in events[0]['details']

    # A rename after the backfill is still caught by the stored registry id.
    ha['switch.renamed'] = {'id': 'reg-plug', 'labels': []}
    del ha['switch.plug']
    r = auth_client.post('/api/allowances', json={
        'agent_id': agent_id, 'name': 'Renamed', 'domain': 'switch',
        'service': 'turn_off', 'entities': ['switch.renamed']})
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'protected_device'
