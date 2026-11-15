"""F27 answer 8: one generic line when the agent has a live allowance."""
import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token


@pytest.fixture
def agent_id():
    _raw, aid = create_agent_token('household')
    return aid


@pytest.fixture
def ha(monkeypatch):
    ents = {'cover.garage': {'id': 'reg-1', 'labels': []}}
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    return ents


def test_the_agent_line_is_generic(auth_client, agent_id, ha):
    assert a.agent_allowance_line(agent_id) == ''
    ids = a.enabled_ids() + ['cover.open_cover']
    auth_client.put('/api/allowances/ceiling', json={
        'enabled': ids, 'risk_ack': True, 'confirm': True})
    auth_client.post('/api/allowances', json={
        'agent_id': agent_id, 'name': 'Garage', 'domain': 'cover',
        'service': 'open_cover', 'entities': ['cover.garage'],
        'risk_ack': True})
    line = a.agent_allowance_line(agent_id)
    assert 'run without approval' in line
    assert 'cover' not in line and 'garage' not in line
