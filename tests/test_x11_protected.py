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
    row = a.create_allowance('operator', agent_id, 'Plugs', 'switch',
                             'turn_off', ['switch.plug'])
    assert row['id']
    a.set_protected_entities('operator', ['switch.plug'])
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
        auth_client, monkeypatch):
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
