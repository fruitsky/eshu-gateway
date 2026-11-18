"""X11/S4 (CB1): a permission is pinned to the HA integration and base URL."""
import pytest

from core import allowances as a
from db.agent_tokens import create_agent_token
from db.integrations import (create_integration, delete_integration,
                             get_integration, update_integration)


@pytest.fixture
def agent_id():
    _raw, aid = create_agent_token('household')
    return aid


@pytest.fixture
def ha(monkeypatch):
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', '', kind='ha')
    ents = {'switch.plug': {'id': 'reg-plug', 'labels': []}}
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    return ents


def _row(aid):
    return a.get_allowance(aid)


def _make(agent_id):
    return a.create_allowance('operator', agent_id, 'Plug', 'switch',
                              'turn_off', ['switch.plug'])


def test_a_new_allowance_is_pinned_to_the_current_ha(agent_id, ha):
    row = _make(agent_id)
    integration = get_integration('Home Assistant')
    assert row['ha_integration_id'] == integration['id']
    assert row['ha_base_url_fp'] == a._ha_base_fp(integration['base_url'])


def test_a_changed_base_url_suspends_every_permission(
        agent_id, ha, auth_client):
    from db.audit import get_audit_log
    row = _make(agent_id)
    update_integration('Home Assistant',
                       base_url='http://192.0.2.51:8123/api')
    # Defence in depth: matching refuses before the refresh runs.
    assert a.match_and_claim({'id': agent_id}, get_integration('Home Assistant'),
                             'call_service', {
                                 'domain': 'switch', 'service': 'turn_off',
                                 'data': {'entity_id': 'switch.plug'}}) is None
    a.refresh_labels()
    assert _row(row['id'])['suspended_reason'] == 'ha_changed'
    events = [x['event_type'] for x in get_audit_log(30)]
    assert 'allowance_ha_changed' in events


def test_swapping_the_integration_suspends_by_ha_changed(agent_id, ha):
    row = _make(agent_id)
    delete_integration('Home Assistant')
    create_integration('Home Assistant', 'http://192.0.2.50:8123/api',
                       'none', '', kind='ha')
    a.refresh_labels()
    assert _row(row['id'])['suspended_reason'] == 'ha_changed'


def test_resume_re_pins_the_current_ha_and_does_not_re_suspend(agent_id, ha):
    row = _make(agent_id)
    update_integration('Home Assistant',
                       base_url='http://192.0.2.51:8123/api')
    a.refresh_labels()
    assert _row(row['id'])['suspended_at']
    a.resume_allowance('operator', row['id'])
    live = _row(row['id'])
    assert live['suspended_at'] == 0
    integration = get_integration('Home Assistant')
    assert live['ha_integration_id'] == integration['id']
    assert live['ha_base_url_fp'] == a._ha_base_fp(integration['base_url'])
    a.refresh_labels()
    assert _row(row['id'])['suspended_at'] == 0


def test_an_unpinned_legacy_row_is_backfilled_not_suspended(agent_id, ha):
    from db.core import db_conn
    row = _make(agent_id)
    with db_conn() as conn:
        conn.execute('UPDATE allowances SET ha_integration_id = 0, '
                     'ha_base_url_fp = \'\' WHERE id = ?', (row['id'],))
        conn.commit()
    a.refresh_labels()
    live = _row(row['id'])
    assert live['suspended_at'] == 0
    assert live['ha_integration_id'] == get_integration('Home Assistant')['id']
