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
        'enabled': ids, 'risk_ack': True, 'confirm': True})
    assert r.status_code == 200, r.text


def _make(auth_client, agent_id, entities=('cover.garage',), risk_ack=True,
          **kw):
    body = {'agent_id': agent_id, 'name': 'Garage', 'domain': 'cover',
            'service': 'open_cover', 'entities': list(entities),
            'risk_ack': risk_ack}
    body.update(kw)
    return auth_client.post('/api/allowances', json=body)


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
        'enabled': ids, 'confirm': True})
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
    r = auth_client.post('/api/allowances/profiles/%d/apply' % pid,
                         json={'agent_id': agent_id, 'risk_ack': True})
    assert r.status_code == 200, r.text
    assert r.json()['created'][0]['name'] == 'Blind'
    hit = a.match_and_claim({'id': agent_id}, 'ha', 'call_service', {
        'domain': 'cover', 'service': 'open_cover',
        'data': {'entity_id': 'cover.blind'}})
    assert hit and hit[1] == 'Blind'
