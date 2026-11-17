"""BZ3: a config-entry write suspends the allowances it can redefine."""
import pytest

from core import allowances as a
from core.tool_runner import note_ha_registry_write
from db.agent_tokens import create_agent_token


@pytest.fixture
def ha(monkeypatch):
    ents = {
        'light.helper': {'id': 'reg-1', 'config_entry_id': 'ce1'},
        'light.other': {'id': 'reg-2', 'config_entry_id': 'ce2'},
    }
    monkeypatch.setattr(a, '_ha_registry', lambda: ents)
    monkeypatch.setattr(a, '_risky_classes', lambda entities: [])
    return ents


def _allowance(agent_id, entity):
    return a.create_allowance(
        'operator', agent_id, entity, 'light', 'turn_on', [entity])


def _ids(agent_id, ha):
    return {
        'helper': _allowance(agent_id, 'light.helper'),
        'other': _allowance(agent_id, 'light.other'),
    }


def test_the_ws_form_is_a_config_entry_write():
    assert a.is_registry_write_path('config_entries/options/flow')
    assert a.is_config_entry_write_path('config_entries/options/flow')
    assert a.is_registry_write_path('/api/config/config_entries/entry/ce1/options')
    assert a.is_config_entry_write_path('config/config_entries/entry/ce1')
    # The entity-registry path is not a config-entry write.
    assert not a.is_config_entry_write_path(
        'config/entity_registry/update')


def test_one_entry_id_can_come_from_the_path_or_the_payload():
    assert a.config_entry_id_from_write(
        'config/config_entries/entry/ce1/options') == 'ce1'
    assert a.config_entry_id_from_write(
        'config_entries/options/flow', {'entry_id': 'ce1'}) == 'ce1'
    # Two different ids are as unidentifiable as none.
    assert a.config_entry_id_from_write(
        'config_entries/options/flow',
        {'entry_id': 'ce1', 'config_entry_id': 'ce2'}) == ''


def test_a_template_helper_options_edit_suspends_its_allowance(ha):
    _raw, aid = create_agent_token('household')
    ids = _ids(aid, ha)
    out = a.on_config_entry_write(
        'config-entry-write', 'config_entries/options/flow',
        {'entry_id': 'ce1'})
    assert out['suspended'] == 1
    assert a.get_allowance(ids['helper']['id'])['suspended_reason'] == \
        'config_entry_changed'
    assert a.get_allowance(ids['other']['id'])['suspended_at'] == 0


def test_an_unidentifiable_entry_suspends_every_ha_allowance(ha):
    _raw, aid = create_agent_token('household')
    ids = _ids(aid, ha)
    out = a.on_config_entry_write(
        'config-entry-write', 'config/config_entries/entry', {})
    assert out['suspended'] == 2
    for key in ('helper', 'other'):
        assert a.get_allowance(ids[key]['id'])['suspended_reason'] == \
            'config_entry_changed'


def test_an_unrelated_entry_leaves_the_allowance_live(ha):
    _raw, aid = create_agent_token('household')
    ids = _ids(aid, ha)
    a.on_config_entry_write(
        'config-entry-write', 'config_entries/options/flow',
        {'entry_id': 'ce2'})
    assert a.get_allowance(ids['helper']['id'])['suspended_at'] == 0
    assert a.get_allowance(ids['other']['id'])['suspended_reason'] == \
        'config_entry_changed'


def test_the_approved_ws_write_hook_uses_the_config_entry_rule(ha):
    _raw, aid = create_agent_token('household')
    ids = _ids(aid, ha)
    note_ha_registry_write({'kind': 'ha'}, 'config_entries/options/flow',
                           {'entry_id': 'ce1'})
    assert a.get_allowance(ids['helper']['id'])['suspended_reason'] == \
        'config_entry_changed'
    assert a.get_allowance(ids['other']['id'])['suspended_at'] == 0
